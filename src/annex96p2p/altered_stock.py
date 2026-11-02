"""Build, run, and compare the separate altered-stock classroom exercise."""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .backends.citylearn_backend import CityLearnBackend
from .fields import DEFAULT_FIELD, field_participants
from .registry import Participant, discover
from .report import save
from .runner import simulate
from .stock_agents import EvilAgent, StorageServiceAgent
from .rc_agents import RCPreheatAgent
from .rc_thermal import load_models
from .stock_dataset import build, verify
from .tariff import Tariff

MODES = ("competitive", "pv_honest", "pv_withhold", "storage_honest",
         "storage_withhold", "storage_flood", "rc_preheat", "rc_no_preheat")


def make_participants(manifest, mode, submissions=None, rc_models=None, price_shape=None,
                      field=DEFAULT_FIELD, agents_dir="agents"):
    """Everyone in the market: the dominant owner (in the matched modes), the
    submissions, then the field on what is left, then the default agent.

    `field` names an entry of <agents_dir>/fields.py; "default" adds no one.
    """
    if mode not in MODES:
        raise ValueError("Unknown mode: " + mode)
    if field != DEFAULT_FIELD and mode != "competitive":
        raise ValueError("--field applies to mode competitive only, not " + mode)
    selected = []
    if mode not in ("competitive", "rc_preheat", "rc_no_preheat"):
        role, behavior = mode.split("_", 1)
        selected = manifest[(manifest.role == role) & manifest.potential_evil_owner].building.tolist()
        if len(selected) / len(manifest) != 0.30:
            raise ValueError("Dominant owner must control exactly 30% of buildings")
    parts = []
    if selected:
        agent = (StorageServiceAgent("dominant", selected) if behavior == "honest" else
                 EvilAgent("dominant", selected, behavior=behavior))
        parts.append(Participant("dominant", agent, selected))
    used = set(selected)
    if submissions:
        students, report = discover(submissions, manifest.building.tolist())
        if report.skipped or report.unknown_buildings:
            raise ValueError(report.render())
        if not students:
            raise ValueError("No student submissions loaded from " + str(submissions))
        seen_names = {p.name for p in parts}
        for p in students:
            if p.name in seen_names or used.intersection(p.buildings):
                raise ValueError("Duplicate name or building claim: " + p.name)
            seen_names.add(p.name)
            used.update(p.buildings)
            parts.append(p)
    if field != DEFAULT_FIELD:
        names = {p.name for p in parts}
        for p in field_participants(agents_dir, field, manifest.building.tolist(), used):
            if p.name in names:
                raise ValueError("A submission and the field are both called " + p.name)
            p.kind = "field"
            used.update(p.buildings)
            parts.append(p)
    for name in manifest.building:
        if name not in used:
            agent = (RCPreheatAgent(name, [name], rc_models, price_shape,
                                   enable_preheat=(mode == "rc_preheat"))
                     if mode in ("rc_preheat", "rc_no_preheat") else StorageServiceAgent(name, [name]))
            parts.append(Participant(name, agent, [name], is_default=True))
    claims = [n for p in parts for n in p.buildings]
    if len(claims) != len(set(claims)) or set(claims) != set(manifest.building):
        raise ValueError("Invalid building ownership partition")
    return parts


def run(backend, folder, mode, out, submissions=None, rc_model_path=None,
        field=DEFAULT_FIELD, agents_dir="agents"):
    provenance = verify(folder)
    manifest = pd.read_csv(folder / "buildings.csv")
    tariff = Tariff.from_csv(str(folder / "duck_prices.csv"),
        comfort_price=provenance["recipe"]["comfort_price"])
    models = (load_models(rc_model_path, folder/"schema.json")
              if mode in ("rc_preheat", "rc_no_preheat") else None)
    recipe = provenance["recipe"]
    parts = make_participants(manifest, mode, submissions, models, recipe["duck_prices"],
                              field, agents_dir)
    by_name = manifest.set_index("building")
    records = []
    bid_rows = []

    def recorder(step):
        grid = step["market"].price_grid
        for participant, levels in step["bids"].items():
            levels = np.asarray(levels, dtype=float)
            for j in np.flatnonzero(np.abs(levels) > 1e-9):
                bid_rows.append((step["timestep"], participant, round(float(grid[j]), 4),
                                 float(levels[j])))
        for i, name in enumerate(backend.building_names):
            outcome = step["outcomes"][name]
            b = backend.env.buildings[i]
            owner = next(p.name for p in parts if name in p.buildings)
            records.append(dict(timestep=step["timestep"], hour=step["market"].hour,
                day=step["market"].day, building=name, participant=owner,
                role=by_name.loc[name,"role"], c_rate=by_name.loc[name,"c_rate"],
                net_kwh=outcome.net_kwh, pv_kwh=abs(float(b.solar_generation[-1])),
                battery_kwh=float(b.electrical_storage.energy_balance[b.time_step]),
                battery_soc_kwh=outcome.battery_soc_kwh,
                degree_hours=outcome.degree_hours, indoor_temperature=outcome.indoor_temperature,
                hvac_kwh=step["observations"][name].hp_draw_kwh(step["actions"][name].hvac)))

    result = simulate(backend, parts, tariff, recorder=recorder, call_timeout_seconds=5.0,
                      run_budget_seconds=120.0, progress=False)
    if result.ledger.check_balance():
        raise AssertionError(result.ledger.check_balance())
    if any(p.failures or p.timeouts or p.disqualified for p in parts):
        raise AssertionError("An agent failed or timed out")
    out.mkdir(parents=True, exist_ok=True)
    save(result, str(out))
    raw = pd.DataFrame(records)
    raw.to_csv(out / "buildings_hourly.csv", index=False)
    (out / "assignments.json").write_text(json.dumps({p.name:p.buildings for p in parts}, indent=2))
    # Who is who, for the hourly page: a submission, the field, or the default agent.
    kinds = {p.name: ("default" if p.is_default else getattr(p, "kind", "submission"))
             for p in parts}
    (out / "participants.json").write_text(json.dumps(dict(field=field, kinds=kinds), indent=2))
    bids = []
    for p in parts:
        if hasattr(p.agent, "offer_log"):
            bids.extend(dict(participant=p.name, **r) for r in p.agent.offer_log)
    pd.DataFrame(bids).to_csv(out / "offers.csv", index=False)
    # Every bid as submitted: one row per participant, hour and price level
    # with a non-zero quantity (+ buy at that price or cheaper, - sell at that
    # price or dearer). Enough to rebuild each participant's bid curve.
    pd.DataFrame(bid_rows, columns=["timestep", "participant", "price", "quantity_kwh"]
                 ).to_csv(out / "bids.csv", index=False)
    thermal = [dict(participant=p.name, **r) for p in parts
               for r in getattr(p.agent, "thermal_log", [])]
    if thermal:
        pd.DataFrame(thermal).to_csv(out / "thermal_plan.csv", index=False)
    ledger = result.ledger.to_frame()
    ledger["sold_kwh"] = (-ledger.p2p_kwh).clip(lower=0)
    ledger["backed_kwh"] = np.minimum(ledger.sold_kwh, (-ledger.net_kwh).clip(lower=0))
    market = result.ledger.market_frame()
    totals = ledger.groupby("timestep")[["sold_kwh", "backed_kwh"]].sum()
    market = market.merge(totals, on="timestep")
    market.to_csv(out / "hourly_audit.csv", index=False)
    daily = market.groupby("day").agg(sold_kwh=("sold_kwh","sum"),
        backed_kwh=("backed_kwh","sum"), trading_hours=("sold_kwh",lambda v:int((v>1e-6).sum())))
    daily.to_csv(out / "daily.csv")
    s = result.summary
    sold = float(market.sold_kwh.sum())
    s.update(net_sold_kwh=sold, backed_kwh=float(market.backed_kwh.sum()),
        weighted_price=float((market.clearing_price.fillna(0)*market.sold_kwh).sum()/sold) if sold else 0,
        trading_hours=int((market.sold_kwh>1e-6).sum()), days=int(len(daily)),
        days_with_trade=int((daily.sold_kwh>1e-6).sum()),
        days_with_backed_sales=int((daily.backed_kwh>1e-6).sum()),
        min_daily_backed_kwh=float(daily.backed_kwh.min()),
        pv_kwh=float(raw.pv_kwh.sum()), battery_charge_kwh=float(raw.battery_kwh.clip(lower=0).sum()),
        battery_discharge_kwh=float(-raw.battery_kwh.clip(upper=0).sum()),
        final_soc_kwh=float(raw.groupby("building").battery_soc_kwh.last().sum()),
        comfortable_pct=float(100*(raw.degree_hours<=1e-9).mean()),
        hvac_kwh=float(raw.hvac_kwh.sum()),
        evaluation_comfortable_pct=float(100*(raw.loc[raw.timestep>=168,"degree_hours"]<=1e-9).mean()),
        evaluation_degree_hours=float(raw.loc[raw.timestep>=168,"degree_hours"].sum()),
        dominant_bill=float(ledger.loc[ledger.participant=="dominant","total_cost"].sum()),
        dominant_sold_kwh=float(ledger.loc[ledger.participant=="dominant","sold_kwh"].sum()),
        failures=sum(p.failures for p in parts), timeouts=sum(p.timeouts for p in parts))
    # Physical energy accounting sanity check for the configured idealized battery.
    eta = np.sqrt(provenance["recipe"]["round_trip_efficiency"])
    initial = float((manifest.battery_kwh*provenance["recipe"]["initial_soc"]).sum())
    expected = initial + s["battery_charge_kwh"]*eta - s["battery_discharge_kwh"]/eta
    if not np.isclose(s["final_soc_kwh"], expected, atol=0.05):
        raise AssertionError("Battery energy accounting mismatch: %s vs %s" % (s["final_soc_kwh"], expected))
    (out / "summary.json").write_text(json.dumps(s, indent=2))
    return s


def compare(out):
    rows = []
    for path in sorted(out.glob("*/*/summary.json")):
        row = json.loads(path.read_text())
        rows.append(dict(configuration=path.parent.parent.name, mode=path.parent.name, **row))
    table = pd.DataFrame(rows)
    table.to_csv(out / "comparison.csv", index=False)
    effects = []
    for cfg, group in table.groupby("configuration"):
        g = group.set_index("mode")
        for evil, honest in (("pv_withhold","pv_honest"),("storage_withhold","storage_honest"),
                             ("storage_flood","storage_honest")):
            if evil not in g.index or honest not in g.index:
                continue
            e,h = g.loc[evil],g.loc[honest]
            benefit = h.dominant_bill-e.dominant_bill
            effects.append(dict(configuration=cfg, strategy=evil,
                owner_benefit=benefit, others_extra_cost=(e.total_bill-e.dominant_bill)-(h.total_bill-h.dominant_bill),
                system_extra_cost=e.total_bill-h.total_bill,
                weighted_price_change=e.weighted_price-h.weighted_price,
                backed_volume_change=e.backed_kwh-h.backed_kwh))
    effect_table = pd.DataFrame(effects)
    effect_table.to_csv(out / "strategic_effects.csv", index=False)
    cols = ["configuration","mode","net_sold_kwh","backed_kwh","weighted_price","days_with_trade",
            "total_bill","curtailed_kwh","comfortable_pct","dominant_bill"]
    text = ("ALTERED STOCK EXPERIMENT\n\n" + table[cols].round(3).to_string(index=False)
        + "\n\nMatched large-owner comparisons (positive owner_benefit = manipulation paid):\n"
        + effect_table.round(3).to_string(index=False)
        + "\n\nPrice is volume weighted. Quantities are net settled sales, counted once.\n"
          "Backed sales require actual same-hour portfolio surplus; oversold energy is covered by the grid.\n"
          "All modes preserve the same thermostat; bills include discomfort but no terminal battery credit.\n"
          "Two synthetic equipment configurations; one winter month, not a universal market-power result.\n")
    (out / "comparison.txt").write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build_p = sub.add_parser("build")
    build_p.add_argument("--recipe", default="scenarios/altered_stock.yaml")
    build_p.add_argument("--upstream", default="../annex96_common_exercise_1")
    build_p.add_argument("--out", default="generated/altered_stock")
    run_p = sub.add_parser("run")
    run_p.add_argument("--dataset", default="generated/altered_stock")
    run_p.add_argument("--upstream", default="../annex96_common_exercise_1")
    run_p.add_argument("--configurations", nargs="+", default=["balanced","solar_abundant"])
    run_p.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES[:-1]))
    run_p.add_argument("--days", type=int)
    run_p.add_argument("--submissions")
    run_p.add_argument("--field", default=DEFAULT_FIELD,
                       help="who runs the houses no submission claims: default (the "
                            "default agent everywhere; graded), basic, advanced, diverse "
                            "-- defined in <agents>/fields.py")
    run_p.add_argument("--agents", default="agents", help="folder holding fields.py")
    run_p.add_argument("--out", default="results/altered_stock")
    run_p.add_argument("--rc-models", default="scenarios/rc_models")
    args = parser.parse_args()
    upstream = Path(args.upstream).resolve()
    sys.path.insert(0, str(upstream))
    import torch
    torch.set_num_threads(1)
    if args.command == "build":
        build(upstream, args.recipe, args.out)
        return
    out = Path(args.out).resolve()
    for cfg in args.configurations:
        folder = (Path(args.dataset)/cfg).resolve()
        verify(folder)
        backend = CityLearnBackend(root=str(upstream), schema=str(folder/"schema.json"), days=args.days)
        for mode in args.modes:
            print("Starting", cfg, mode, "steps", backend.n_timesteps, flush=True)
            rc_path = (Path(args.rc_models)/(cfg+".json")
                       if mode in ("rc_preheat", "rc_no_preheat") else None)
            summary = run(backend, folder, mode, out/cfg/mode, args.submissions, rc_path,
                          args.field, args.agents)
            print(cfg, mode, json.dumps(summary), flush=True)
            compare(out)
    print("Results:", out, flush=True)


if __name__ == "__main__":
    main()
