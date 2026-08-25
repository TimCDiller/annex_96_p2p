"""Command line entry point.

    python -m annex96p2p run          --config config/smoke.yaml
    python -m annex96p2p tournament   --config config/default.yaml
    python -m annex96p2p buildings
    python -m annex96p2p check        submissions/my_team.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import report as report_mod
from .config import Config
from .registry import SubmissionError, build_participants
from .runner import simulate
from .tariff import Tariff


def make_backend(cfg: Config):
    if cfg.backend == "mock":
        from .backends.mock import MockBackend, default_neighbourhood
        return MockBackend(n_days=cfg.days,
                           specs=default_neighbourhood(cfg.n_per_archetype,
                                                       cfg.n_buildings))
    if cfg.backend == "citylearn":
        from .backends.citylearn_backend import CityLearnBackend
        return CityLearnBackend(dataset=cfg.citylearn_dataset,
                                schema=cfg.citylearn_schema, days=cfg.days)
    raise SystemExit("unknown backend %r (expected 'mock' or 'citylearn')" % cfg.backend)


def make_tariff(cfg: Config, n_timesteps: int) -> Tariff:
    kwargs = dict(export_price=cfg.export_price, comfort_price=cfg.comfort_price,
                  price_step=cfg.price_step)
    if cfg.price_csv:
        return Tariff.from_csv(cfg.price_csv, **kwargs)
    return Tariff.synthetic(n_timesteps, delivery_adder=cfg.delivery_adder,
                            volatility=cfg.price_volatility, seed=cfg.seed, **kwargs)


def do_run(cfg: Config, show_defaults: bool, quiet: bool = False,
           cfg_path: Optional[str] = None):
    backend = make_backend(cfg)
    tariff = make_tariff(cfg, backend.n_timesteps)
    try:
        participants, discovery = build_participants(
            backend, cfg.submissions, default_agent=cfg.default_agent,
            unclaimed_mode=cfg.unclaimed_mode, conflict_policy=cfg.conflict_policy,
            assignments=cfg.assignments, strict_assignments=cfg.strict_assignments)
    except SubmissionError as exc:
        # A setup problem, not a crash. Say what is wrong and stop cleanly.
        raise SystemExit(
            "\nCannot start: the submissions do not match the assignment table.\n\n"
            "  %s\n\n"
            "Fix the BUILDINGS or NAME in the offending submission, or adjust\n"
            "the `assignments:` block in %s.\n"
            % (str(exc).replace("\n", "\n  "), cfg_path or "your config"))

    if not quiet:
        text = discovery.render()
        if text:
            print(text)
            print()

    result = simulate(backend, participants, tariff, max_bid_kwh=cfg.max_bid_kwh,
                      slow_call_seconds=cfg.slow_call_seconds)

    if cfg.reference:
        # Second pass: the same building groups, all run by the default
        # agent. Gives every participant a portfolio-size-independent score.
        from .reference import attach, reference_costs
        attach(result, reference_costs(backend, participants, tariff,
                                       agent_key=cfg.reference_agent,
                                       max_bid_kwh=cfg.max_bid_kwh))

    if not quiet:
        print(report_mod.full_report(result, show_defaults=show_defaults))

    problems = result.ledger.check_balance()
    if problems:
        print("\nMARKET DID NOT BALANCE -- this is a bug, please report it:")
        for problem in problems[:5]:
            print("  " + problem)

    if cfg.save_csv:
        report_mod.save(result, cfg.out)
        if not quiet:
            print("\nwrote %s/{settlements,market,leaderboard}.csv" % cfg.out)

    return result


def do_buildings(cfg: Config) -> None:
    backend = make_backend(cfg)
    meta = backend.metadata()
    print("%d buildings available. Claim some in your submission with:"
          % len(backend.building_names))
    print('    BUILDINGS = ["B01", "B02", "B03"]\n')
    keys: List[str] = []
    for value in meta.values():
        for key in value:
            if key not in keys:
                keys.append(key)
    print("  name   " + "  ".join("%-14s" % k for k in keys))
    print("  " + "-" * (7 + 16 * len(keys)))
    for name in backend.building_names:
        row = meta[name]
        print("  %-6s " % name + "  ".join("%-14s" % row.get(k, "") for k in keys))


def do_check(paths: List[str], cfg: Config) -> int:
    """Load a submission and run it for one day, to catch obvious breakage."""
    backend = make_backend(cfg)
    from .registry import discover

    failed = 0
    for path in paths:
        folder = Path(path).parent
        stem = Path(path).stem
        found, discovery = discover(folder, backend.building_names)
        mine = [p for p in found if Path(p.source or "").stem == stem]
        if not mine:
            print("FAIL %s" % path)
            print(discovery.render() or "  submission not loaded")
            failed += 1
            continue
        participant = mine[0]
        print("OK   %s  ->  %s controls %s"
              % (path, participant.name, participant.buildings))
    return failed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="annex96p2p",
        description="Peer-to-peer electricity trading exercise "
                    "(adapted from IEA EBC Annex 96 Common Exercise 1)")
    sub = parser.add_subparsers(dest="command")

    def add_common(p):
        p.add_argument("--config", default=None, help="path to a YAML config")
        p.add_argument("--days", type=int, default=None, help="override simulated days")
        p.add_argument("--submissions", default=None, help="override submissions folder")
        p.add_argument("--out", default=None, help="override output folder")
        p.add_argument("--n-buildings", type=int, default=None, dest="n_buildings",
                       help="exact number of buildings (mock backend); "
                            "25 matches the real Annex 96 neighbourhood")

    run_p = sub.add_parser("run", help="run a simulation")
    add_common(run_p)
    run_p.add_argument("--show-defaults", action="store_true",
                       help="include default agents in the leaderboard")

    tour_p = sub.add_parser("tournament",
                            help="run every submission together, strict about claims")
    add_common(tour_p)
    tour_p.add_argument("--show-defaults", action="store_true")

    b_p = sub.add_parser("buildings", help="list the buildings you can claim")
    add_common(b_p)

    c_p = sub.add_parser("check", help="verify a submission file loads")
    add_common(c_p)
    c_p.add_argument("paths", nargs="+")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    cfg = Config.load(getattr(args, "config", None))
    for field_name in ("days", "submissions", "out", "n_buildings"):
        value = getattr(args, field_name, None)
        if value is not None:
            setattr(cfg, field_name, value)

    if args.command == "buildings":
        do_buildings(cfg)
        return 0
    if args.command == "check":
        return 1 if do_check(args.paths, cfg) else 0
    if args.command == "tournament":
        # A graded run: overlapping claims are a setup error, not a warning.
        cfg.conflict_policy = "error"
        cfg.strict_assignments = bool(cfg.assignments)
        do_run(cfg, show_defaults=args.show_defaults, cfg_path=args.config)
        return 0

    do_run(cfg, show_defaults=args.show_defaults, cfg_path=args.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
