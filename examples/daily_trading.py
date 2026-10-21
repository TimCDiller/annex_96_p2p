"""Compare the original market, matched ownership control, and four new agents.

From the repository root:
    python examples/daily_trading.py --days 7
    python examples/daily_trading.py           # full Vermont month

The original submissions are retained on houses not transferred to the four
new agents. No original submission files or market rules are modified.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from annex96p2p.baselines import SimpleRBCAgent
from annex96p2p.cli import make_backend, make_tariff
from annex96p2p.config import Config
from annex96p2p.registry import (DiscoveryReport, Participant, discover,
                                  fill_unclaimed, build_participants)
from annex96p2p.report import save
from annex96p2p.runner import simulate


def participants(backend, scenario):
    if scenario == "original":
        parts, discovery = build_participants(backend, str(ROOT / "examples/original_submissions"),
                                              conflict_policy="error")
        assert not discovery.skipped and not discovery.unknown_buildings, discovery.render()
        return parts
    old, report = discover(ROOT / "examples/original_submissions", backend.building_names)
    new, new_report = discover(ROOT / "examples/original_submissions/daily_trading", backend.building_names)
    assert not report.skipped and not report.unknown_buildings, report.render()
    assert len(new) == 4 and not new_report.skipped and not new_report.unknown_buildings, new_report.render()
    transferred = {b for p in new for b in p.buildings}
    assert len(transferred) == 8
    retained = []
    for p in old:
        buildings = [b for b in p.buildings if b not in transferred]
        if buildings:
            retained.append(Participant(p.name, type(p.agent)(p.name, buildings), buildings))
    if scenario == "ownership_control":
        new = [Participant(p.name, SimpleRBCAgent(p.name, p.buildings), p.buildings)
               for p in new]
    parts = fill_unclaimed(retained + new, backend.building_names, DiscoveryReport())
    claims = [b for p in parts for b in p.buildings]
    assert len(claims) == len(set(claims)) == len(backend.building_names)
    return parts


def write_comparison(out):
    """Summarize measured results, including sales actually backed by surplus."""
    import pandas as pd
    summaries = json.loads((out / "summary.json").read_text())
    for scenario, summary in summaries.items():
        frame = pd.read_csv(out / scenario / "physical.csv")
        delivered = frame.groupby(frame.timestep // 24).physically_backed_sales_kwh.sum()
        summary["days_with_surplus_backed_sales"] = int((delivered > 1e-6).sum())
        summary["min_daily_surplus_backed_kwh"] = float(delivered.min())
    (out / "summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    control = pd.read_csv(out / "ownership_control/leaderboard.csv").set_index("participant")
    current = pd.read_csv(out / "new_agents/leaderboard.csv").set_index("participant")
    names = ["naive_spot", "naive_clock", "minimum_tariff", "minimum_adaptive"]
    agents = current.loc[names, ["total_cost", "energy_cost", "comfort_cost",
                                  "bought_kwh", "sold_kwh", "degree_hours"]].copy()
    agents["control_total_cost"] = control.loc[names, "total_cost"]
    agents["saving_vs_control"] = agents.control_total_cost - agents.total_cost
    physical = pd.read_csv(out / "new_agents/physical.csv")
    group = physical.groupby("participant")
    agents["comfortable_pct"] = (100 * group.comfortable_building_hours.sum()
                                  / group.building_hours.sum())
    agents["physically_backed_sales_kwh"] = group.physically_backed_sales_kwh.sum()
    agents["final_soc_kwh"] = group.battery_soc_kwh.last()
    agents.to_csv(out / "agent_comparison.csv")
    fields = ["p2p_volume_kwh", "days_with_trade", "days", "min_daily_kwh",
              "physically_backed_sales_kwh", "grid_backed_sales_kwh",
              "days_with_surplus_backed_sales", "min_daily_surplus_backed_kwh",
              "total_bill", "degree_hours", "comfortable_pct"]
    table = pd.DataFrame(summaries).T[fields]
    text = ("Vermont daily-trading experiment\n\n" + table.round(3).to_string()
            + "\n\nNew agents (each controls two houses):\n" + agents.round(3).to_string()
            + "\n\nSame weather, synthetic tariff, $0.50/degree-hour comfort penalty; "
              "no grid export payment. Startup included; final day may be partial.\n"
              "Ownership control uses identical groups with default controllers for the new pairs.\n"
              "Pairs were selected using first-week heating demand; this is an exploratory scenario.\n"
              "Physically backed sales = min(peer sales, actual portfolio surplus) each hour.\n"
              "Grid-backed sales are cleared sales not covered by actual portfolio surplus.\n"
              "Gross clearing volume can exceed net sales when existing agents bid on both sides.\n"
              "Bills include discomfort but no terminal value for remaining battery charge.\n")
    (out / "comparison.txt").write_text(text, encoding="utf-8")
    # Optional visualization; analysis and CSV output need no plotting package.
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    labels = {"original": "Original market", "ownership_control": "Same groups, default agents",
              "new_agents": "Four new strategies"}
    for scenario, label in labels.items():
        daily = pd.read_csv(out / scenario / "daily.csv")
        actual = pd.read_csv(out / scenario / "physical.csv")
        backed = actual.groupby(actual.timestep // 24).physically_backed_sales_kwh.sum()
        axes[0].plot(daily.day + 1, daily.volume_kwh, label=label)
        axes[1].plot(backed.index + 1, backed.values, label=label)
    axes[0].set_ylabel("Cleared trading (kWh/day)")
    axes[1].set_ylabel("Surplus-backed sales (kWh/day)")
    axes[1].set_xlabel("Simulation day (final day may be partial)")
    axes[0].set_title("Vermont: market turnover versus delivered household surplus")
    for ax in axes:
        ax.set_ylim(bottom=0)
        ax.grid(alpha=0.2)
    axes[0].legend()
    fig.tight_layout()
    fig.savefig(out / "daily_trading.png", dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--out", default="results/daily_trading")
    args = parser.parse_args()
    cfg = Config.load(str(ROOT / "config/default.yaml"))
    cfg.days = args.days
    cfg.citylearn_root = str((ROOT / cfg.citylearn_root).resolve())
    cfg.citylearn_schema = str((ROOT / cfg.citylearn_schema).resolve())
    sys.path.insert(0, cfg.citylearn_root)
    import torch
    # Small per-building LSTMs are much faster without thread-pool overhead.
    torch.set_num_threads(1)
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    backend = make_backend(cfg)
    tariff = make_tariff(cfg, backend.n_timesteps)
    summaries = {}
    daily_frames = []
    import pandas as pd

    for scenario in ("original", "ownership_control", "new_agents"):
        print("Starting", scenario, "steps:", backend.n_timesteps, flush=True)
        parts = participants(backend, scenario)
        physical = []

        def record(step):
            for p in parts:
                outcomes = [step["outcomes"][b] for b in p.buildings]
                net = sum(o.net_kwh for o in outcomes)
                sold = max(0.0, -step["clearing"].quantities.get(p.name, 0.0))
                physical.append(dict(timestep=step["timestep"], participant=p.name,
                    comfortable_building_hours=sum(o.degree_hours <= 1e-9 for o in outcomes),
                    building_hours=len(outcomes),
                    physically_backed_sales_kwh=min(sold, max(0.0, -net)),
                    grid_backed_sales_kwh=max(0.0, sold - max(0.0, -net)),
                    battery_soc_kwh=sum(o.battery_soc_kwh for o in outcomes)))

        result = simulate(backend, parts, tariff, recorder=record,
                          call_timeout_seconds=cfg.call_timeout_seconds,
                          run_budget_seconds=cfg.run_budget_seconds)
        assert not result.ledger.check_balance()
        assert all(not p.failures and not p.timeouts and not p.disqualified for p in parts)
        folder = out / scenario
        save(result, str(folder))
        physical = pd.DataFrame(physical)
        physical.to_csv(folder / "physical.csv", index=False)
        (folder / "assignments.json").write_text(json.dumps(
            {p.name: p.buildings for p in parts}, indent=2), encoding="utf-8")
        market = result.ledger.market_frame()
        daily = market.groupby("day").agg(volume_kwh=("volume_kwh", "sum"),
            trading_hours=("volume_kwh", lambda x: int((x > 1e-6).sum())),
            hours=("timestep", "count")).reset_index()
        daily.insert(0, "scenario", scenario)
        daily_frames.append(daily)
        daily.to_csv(folder / "daily.csv", index=False)
        summary = result.summary
        summary.update(days_with_trade=int((daily.volume_kwh > 1e-6).sum()),
            days=int(len(daily)), min_daily_kwh=float(daily.volume_kwh.min()),
            comfortable_pct=float(100 * physical.comfortable_building_hours.sum()
                                  / physical.building_hours.sum()),
            physically_backed_sales_kwh=float(physical.physically_backed_sales_kwh.sum()),
            grid_backed_sales_kwh=float(physical.grid_backed_sales_kwh.sum()),
            failures=sum(p.failures for p in parts), timeouts=sum(p.timeouts for p in parts))
        summaries[scenario] = summary
        (out / "summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
        print(scenario, json.dumps(summary), flush=True)
    pd.concat(daily_frames).to_csv(out / "daily_comparison.csv", index=False)
    (out / "run_config.json").write_text(json.dumps(vars(cfg), indent=2), encoding="utf-8")
    write_comparison(out)
    print("Results:", out, flush=True)


if __name__ == "__main__":
    main()
