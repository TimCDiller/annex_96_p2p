"""How beatable is the default agent? Run challengers against it for a month.

    python examples/challenge_default.py capped ranked challenger
    python examples/challenge_default.py --list

Each scenario hands some houses to one challenger from examples/challengers.py
-- one participant per house, exactly as in the all-default baseline -- and
runs the balanced configuration, mode competitive, through the ordinary
course runner. Results go to results/challenge/<scenario>/. Compare with an
all-default run on the same houses, e.g.

    python -m annex96p2p.altered_stock run --configurations balanced \
        --modes competitive --out results/challenge/baseline
"""
import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STOCK = pd.read_csv(ROOT / "generated/altered_stock/balanced/buildings.csv")
PV = STOCK[STOCK.role == "pv"].building.tolist()
ALL = STOCK.building.tolist()

SCENARIOS = {
    # Storage-rich home 199613 (80 kWh, 1C) and 147002 (80 kWh, 0.25C).
    "capped": ("CappedBuyer", ["resstock-amy2018-2021-release-1-199613"]),
    "ranked": ("RankedDischarge", ["resstock-amy2018-2021-release-1-199613"]),
    "challenger": ("Challenger", ["resstock-amy2018-2021-release-1-199613"]),
    "challenger_slow": ("Challenger", ["resstock-amy2018-2021-release-1-147002"]),
    # All ten PV-rich homes, each its own participant.
    "solar_preheat": ("SolarPreheat", PV),
    "late_preheat": ("LatePreheat", PV),
    # Smoother heating, all 20 houses.
    "gentle_pi": ("GentlePI", ALL),
    "filtered_pi": ("FilteredPI", ALL),
    "rc_ff": ("RCFeedforward", ALL),
    # Model-based preheating on the PV-rich homes, and its matched control.
    "rc_ff_pv": ("RCFeedforward", PV),
    "rc_late_preheat": ("RCLatePreheat", PV),
    # Preheating on top of the filtered thermostat, PV-rich homes, and its control.
    "filtered_pv": ("FilteredPI", PV),
    "filtered_solar_preheat": ("FilteredSolarPreheat", PV),
    "filtered_late_preheat": ("FilteredLatePreheat", PV),
    "filtered_coast": ("FilteredCoast", PV),
}

WRAPPER = '''import sys
sys.path.insert(0, {examples!r})
from challengers import {cls}
NAME = {name!r}
BUILDINGS = [{building!r}]
AGENT = {cls}
'''


def prepare(scenario):
    cls, houses = SCENARIOS[scenario]
    folder = ROOT / "results/challenge" / scenario / "submissions"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.py"):
        old.unlink()
    for b in houses:
        short = b.rsplit("-", 1)[-1]
        (folder / ("%s_%s.py" % (scenario, short))).write_text(WRAPPER.format(
            examples=str(ROOT / "examples"), cls=cls, name="%s_%s" % (scenario, short),
            building=b))
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenarios", nargs="*")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--days", type=int)
    args = parser.parse_args()
    if args.list or not args.scenarios:
        for name, (cls, houses) in SCENARIOS.items():
            print("%-16s %-16s %d house(s)" % (name, cls, len(houses)))
        return
    for scenario in args.scenarios:
        folder = prepare(scenario)
        command = [sys.executable, "-m", "annex96p2p.altered_stock", "run",
                   "--configurations", "balanced", "--modes", "competitive",
                   "--submissions", str(folder), "--out", str(folder.parent)]
        if args.days:
            command += ["--days", str(args.days)]
        subprocess.run(command, check=True, cwd=ROOT)


if __name__ == "__main__":
    main()
