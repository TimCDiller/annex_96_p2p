"""The study behind agents/advanced/README.md: each advanced agent alone, together, everywhere.

    python examples/advanced_study.py --list
    python examples/advanced_study.py forecast_h1 marginal_storage --days 7
    python examples/advanced_study.py trio --submission submissions/team_yourname.py
    python examples/advanced_study.py --report forecast_h1 forecast_h3 trio

Each scenario is a field (see agents/fields.py) that puts advanced agents on
some houses; every other house runs the default agent. The scenario's field
is written to results/advanced/<scenario>/fields.py and run through the
ordinary course runner (--field), balanced configuration, mode competitive,
into results/advanced/<scenario>/, with the hourly page (hourly_trades.html).

--submission adds your own file. The field steps off the houses you claim.

--report compares each scenario with an all-default run of the same length
(the scenario "baseline", run first if missing) and prints, per group of
houses and per advanced agent, the bill and its change.

To play against these agents in a ready-made market, you do not need this
script: run the course market with --field advanced or --field diverse.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"
OUT = ROOT / "results/advanced"
STOCK = pd.read_csv(ROOT / "generated/altered_stock/balanced/buildings.csv")
PV = STOCK[STOCK.role == "pv"].building.tolist()
FAST = STOCK[(STOCK.role == "storage") & (STOCK.c_rate == 1.0)].building.tolist()
SLOW = STOCK[(STOCK.role == "storage") & (STOCK.c_rate < 1.0)].building.tolist()

FAST_HOME = "resstock-amy2018-2021-release-1-199613"      # 80 kWh / 80 kW
SLOW_HOME = "resstock-amy2018-2021-release-1-147002"      # 80 kWh / 20 kW
PV_HOME = "resstock-amy2018-2021-release-1-112208"        # 40 kW PV, 4 kWh / 1 kW

FORECAST = "advanced/market_forecast_battery.py"
MARGINAL = "advanced/marginal_value_storage.py"
OPT_HP = "advanced/team_opt_hp.py"          # a team's MPC submission, run unchanged
MFBM = "market forecast battery management"


def houses(*ids):
    return ["resstock-amy2018-2021-release-1-%s" % i for i in ids]


def agent(path, houses, name, together=False, **params):
    """One field entry: an agent file from agents/ on `houses`."""
    return dict(agent=str(AGENTS / path), houses=houses, name=name, together=together,
                params=params)


# The mixed market of agents/README section 4: default, forecast, marginal-value
# and team_opt_hp agents spread evenly over the house types.
#            PV-rich (10)                fast storage (5)   slow storage (5)
# opt_hp     112208 179247 425540        199613             147002     one portfolio
# marginal   20199 223581 247942         4421               411001
# forecast   --                          245723 408344      216895 319918
# default    376570 409896 460412 481052 485614             467125
MIXED_OPT_HP = houses("112208", "179247", "425540", "199613", "147002")
MIXED_MARGINAL = houses("20199", "223581", "247942", "4421", "411001")
MIXED_FORECAST = houses("245723", "408344", "216895", "319918")

SCENARIOS = {
    "baseline": [],
    # Each agent alone among default opponents.
    "forecast_h1": [agent(FORECAST, [FAST_HOME], MFBM, FORECAST_HOURS=1)],
    "forecast_h3": [agent(FORECAST, [FAST_HOME], MFBM, FORECAST_HOURS=3)],
    "forecast_h6": [agent(FORECAST, [FAST_HOME], MFBM, FORECAST_HOURS=6)],
    "forecast_h1_spread": [agent(FORECAST, [FAST_HOME], MFBM, FORECAST_HOURS=1, SPREAD=0.1)],
    "marginal_storage": [agent(MARGINAL, [SLOW_HOME], "marginal value storage")],
    "marginal_generation": [agent(MARGINAL, [PV_HOME], "marginal value generation")],
    # The other strategy on the same fast house, for a like-for-like comparison.
    "marginal_fast": [agent(MARGINAL, [FAST_HOME], "marginal value storage")],
    # The three together, each on its own house.
    "trio": [agent(FORECAST, [FAST_HOME], MFBM, FORECAST_HOURS=1),
             agent(MARGINAL, [SLOW_HOME], "marginal value storage"),
             agent(MARGINAL, [PV_HOME], "marginal value generation")],
    "trio_h3": [agent(FORECAST, [FAST_HOME], MFBM, FORECAST_HOURS=3),
                agent(MARGINAL, [SLOW_HOME], "marginal value storage"),
                agent(MARGINAL, [PV_HOME], "marginal value generation")],
    # Every house strategic: forecast agents on the five fast batteries,
    # marginal-value agents on the slow ones and on the PV-rich homes.
    "everyone": [agent(FORECAST, FAST, MFBM, FORECAST_HOURS=1),
                 agent(MARGINAL, SLOW, "marginal value storage"),
                 agent(MARGINAL, PV, "marginal value generation")],
    "everyone_h3": [agent(FORECAST, FAST, MFBM, FORECAST_HOURS=3),
                    agent(MARGINAL, SLOW, "marginal value storage"),
                    agent(MARGINAL, PV, "marginal value generation")],
    # Basic, advanced and a team's MPC agent side by side (see MIXED_* above).
    "mixed": [agent(OPT_HP, MIXED_OPT_HP, "team_opt_hp", together=True),
              agent(MARGINAL, MIXED_MARGINAL, "marginal value"),
              agent(FORECAST, MIXED_FORECAST, MFBM, FORECAST_HOURS=3)],
    # Its control: team_opt_hp on the same houses, every other house default.
    "opt_hp_alone": [agent(OPT_HP, MIXED_OPT_HP, "team_opt_hp", together=True)],
}


def run(scenario, days=None, submission=None, html=True):
    folder = OUT / scenario
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "fields.py").write_text("FIELDS = %r
" % {scenario: SCENARIOS[scenario]})
    command = [sys.executable, "-m", "annex96p2p.altered_stock", "run",
               "--configurations", "balanced", "--modes", "competitive",
               "--out", str(folder), "--agents", str(folder), "--field", scenario]
    if submission:
        mine = folder / "submissions"
        if mine.exists():
            shutil.rmtree(mine)
        mine.mkdir()
        shutil.copy(submission, mine / Path(submission).name)
        command += ["--submissions", str(mine)]
    if days:
        command += ["--days", str(days)]
    subprocess.run(command, check=True, cwd=ROOT)
    if html:
        subprocess.run([sys.executable, str(ROOT / "examples/hourly_trades.py"), "--run",
                        str(folder / "balanced/competitive"), "--plotlyjs", "cdn"],
                       check=True, cwd=ROOT)


# --------------------------------------------------------------------------
# Comparison with the all-default run
# --------------------------------------------------------------------------
def load(scenario):
    folder = OUT / scenario / "balanced/competitive"
    ledger = pd.read_csv(folder / "settlements.csv")
    owners = json.loads((folder / "assignments.json").read_text())
    house_of = {p: b for p, bs in owners.items() for b in bs}
    homes = pd.read_csv(folder / "buildings_hourly.csv")
    market = pd.read_csv(folder / "hourly_audit.csv")
    return ledger, owners, house_of, homes, market


def house_bills(ledger, owners):
    """Bill per house; a participant with several houses is split evenly."""
    bills = ledger.groupby("participant").total_cost.sum()
    out = {}
    for p, houses in owners.items():
        for b in houses:
            out[b] = bills.get(p, 0.0) / len(houses)
    return pd.Series(out)


def market_stats(ledger, market):
    sold = (-ledger.p2p_kwh).clip(lower=0).groupby(ledger.timestep).sum()
    m = market.set_index("timestep")
    m["sold"] = sold.reindex(m.index).fillna(0.0)
    m["share"] = m.clearing_price / m.grid_import_price
    peak = m.grid_import_price >= 0.20
    midday = m.hour.between(9, 15)

    def weighted(mask):
        part = m[mask & (m.sold > 1e-9)]
        return float((part.share * part.sold).sum() / part.sold.sum()) if len(part) else float("nan")

    return dict(traded_kwh=float(m.sold.sum()),
                peak_share=weighted(peak), midday_share=weighted(midday),
                peak_traded_kwh=float(m.sold[peak].sum()),
                grid_import_kwh=float(ledger.grid_import_kwh.sum()),
                curtailed_kwh=float(ledger.curtailed_kwh.sum()),
                total_bill=float(ledger.total_cost.sum()))


def compare(scenario, baseline="baseline"):
    ledger, owners, house_of, homes, market = load(scenario)
    base_ledger, base_owners, _, base_homes, base_market = load(baseline)
    if ledger.timestep.max() != base_ledger.timestep.max():
        raise SystemExit("%s and %s have different lengths" % (scenario, baseline))
    bills, base = house_bills(ledger, owners), house_bills(base_ledger, base_owners)
    rows = []
    strategic = sorted(p for p in ledger.loc[~ledger.is_default.astype(bool), "participant"].unique())
    for p in strategic:
        houses = owners[p]
        rows.append(dict(who=p, houses=len(houses), bill=bills[houses].sum(),
                         default=base[houses].sum()))
    for label, houses in (("PV-rich homes", PV), ("fast storage homes", FAST),
                          ("slow storage homes", SLOW), ("all 20 houses", PV + FAST + SLOW)):
        rows.append(dict(who=label, houses=len(houses), bill=bills[houses].sum(),
                         default=base[houses].sum()))
    table = pd.DataFrame(rows)
    table["change"] = table.bill - table.default
    table["change_pct"] = 100 * table.change / table.default
    stats = pd.DataFrame({scenario: market_stats(ledger, market),
                          baseline: market_stats(base_ledger, base_market)})
    return table, stats


def report(scenarios, days=None):
    if not (OUT / "baseline/balanced/competitive/settlements.csv").exists():
        run("baseline", days, html=False)
    lines = []
    for scenario in scenarios:
        table, stats = compare(scenario)
        lines += ["== %s ==" % scenario,
                  table.to_string(index=False, float_format=lambda v: "%.2f" % v), "",
                  stats.to_string(float_format=lambda v: "%.3f" % v), ""]
    text = "\n".join(lines)
    print(text)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenarios", nargs="*")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--days", type=int)
    parser.add_argument("--submission", help="your own submission file to add")
    parser.add_argument("--no-html", action="store_true")
    parser.add_argument("--report", action="store_true",
                        help="compare already-run scenarios with the baseline; run nothing")
    args = parser.parse_args()
    if args.list or not args.scenarios:
        for name, specs in SCENARIOS.items():
            print("%-20s %s" % (name, "; ".join("%s x%d %s" % (Path(s["agent"]).stem, len(s["houses"]),
                  s["params"] or "") for s in specs) or "every house on the default agent"))
        return
    if args.report:
        report(args.scenarios, args.days)
        return
    for scenario in args.scenarios:
        run(scenario, args.days, args.submission, html=not args.no_html)


if __name__ == "__main__":
    main()
