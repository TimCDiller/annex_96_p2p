"""Does pre-cooling pay? Find out, rather than taking anyone's word for it.

    python examples/precool_study.py
    python examples/precool_study.py --days 5 --comfort 2.0,0.5,0.2

Pre-cooling means deliberately over-cooling while energy is cheap, so the
building can coast through the expensive hours with the compressor off. The
fabric is a thermal store nobody charges you for, and on these homes it holds
more energy than the battery does.

It is not free, and the argument cuts both ways:

  AGAINST                                   FOR
  a colder building leaks faster, so        the peak/off-peak price ratio
  the round trip is lossy                   a wide expensive window
  the shipped Thermostat already sits       heavy thermal mass
  near the cheap edge of the band           a cheap comfort penalty
  degree-hours outside the band cost        a wide comfort band

Which side wins is a question about numbers, not principle. This sweeps two of
them -- how deep you pre-cool, and how expensive comfort is -- and prints the
resulting bill for each combination.

One risk specific to this stock: the comfort band is the occupant setpoint
plus or minus `comfort_band`, and the ResStock schedules move it by up to 7 K
within a single building. Banking cold against a band that may step down
underneath you is a real hazard, and it is a large part of why the answer here
is not obvious.

Needs CityLearn and the Annex 96 dataset. Every cell is a full simulation, so
keep --days small while exploring.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np

from annex96p2p import Action, Agent, Thermostat
from annex96p2p.config import Config
from annex96p2p.reference import reference_costs
from annex96p2p.registry import DiscoveryReport, Participant, fill_unclaimed
from annex96p2p.runner import simulate
from annex96p2p.tariff import Tariff

ROOT = Path(__file__).resolve().parents[1]


class Precooler(Agent):
    """The default agent, with one change: the thermostat follows the price.

    `depth` is where in the comfort band to sit during the cheapest third of
    hours, as a fraction of the half-band. The first depth in the sweep is the
    control row -- it is where the shipped thermostat sits anyway.
    """

    depth = 0.2
    coast = 0.4          # where to sit during the dearest quarter of hours

    def reset(self):
        self.thermostat = Thermostat()
        self.seen = []
        self._r = "normal"

    def _regime(self, mkt):
        self.seen.append(mkt.grid_import_price)
        if len(self.seen) < 24:
            return "normal"
        if mkt.grid_import_price <= float(np.percentile(self.seen, 33)):
            return "cheap"
        if mkt.grid_import_price >= float(np.percentile(self.seen, 75)):
            return "dear"
        return "normal"

    def _aim(self):
        return {"cheap": self.depth, "dear": self.coast}.get(self._r, 0.2)

    def bid(self, obs, mkt):
        self._r = self._regime(mkt)
        bid = mkt.new_bid()
        # Bid for the compressor too, not just the passive load -- otherwise
        # the pre-cooling energy gets bought from the grid at full retail and
        # the comparison measures the wrong thing.
        need = sum(o.passive_net_kwh
                   + abs(self.thermostat(o, aim=self._aim())) * o.hp_max_electric_kw
                   for o in obs.values())
        if need > 0:
            bid.buy(mkt.grid_import_price - 0.01, need)
        elif need < 0:
            last = mkt.last_clearing_price
            bid.sell(max(0.01, (last - 0.01) if last else 0.02), -need)
        return bid.array

    def dispatch(self, obs, mkt, result):
        return {n: Action(hvac=self.thermostat(o, aim=self._aim()), battery=0.0)
                for n, o in obs.items()}


def build_backend(days):
    cfg = Config.load(str(ROOT / "config" / "default.yaml"))
    root = os.environ.get("ANNEX96_CE1") or cfg.citylearn_root
    if root and not os.path.isabs(root):
        root = str((ROOT / root).resolve())
    if root and root not in sys.path:
        sys.path.insert(0, root)
    schema = cfg.citylearn_schema
    if schema and not os.path.isabs(schema):
        schema = str((ROOT / schema).resolve())
    from annex96p2p.backends.citylearn_backend import CityLearnBackend
    return CityLearnBackend(schema=schema, days=days, root=root)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--depths", default="0.2,0.0,-0.4,-0.8",
                    help="where to sit in the band during cheap hours")
    ap.add_argument("--comfort", default="2.0,0.5,0.2",
                    help="$ per degC-hour, one column each")
    args = ap.parse_args(argv)

    depths = [float(x) for x in args.depths.split(",")]
    comforts = [float(x) for x in args.comfort.split(",")]

    print(__doc__.split("Which side wins")[0].rstrip())
    print()
    backend = build_backend(args.days)
    mine = backend.building_names[:5]
    print("  %d buildings over %d timesteps; the portfolio is the first five."
          % (len(backend.building_names), backend.n_timesteps))
    print()
    print("  score = bill / the default agent's bill on the same buildings.")
    print("  READ IT AGAINST THE FIRST ROW. That row already bids for the")
    print("  compressor load rather than just the passive one, which is worth")
    print("  something by itself. Pre-cooling is the difference below it.")
    print()

    print("  %-18s" % "comfort $/degC-h"
          + "".join("  %14s" % ("$%.2f" % c) for c in comforts))
    print("  " + "-" * (18 + 16 * len(comforts)))

    control = {}
    for d in depths:
        label = "no pre-cool" if d == depths[0] else "aim %+.1f" % d
        row = "  %-18s" % label
        for c in comforts:
            tariff = Tariff.synthetic(backend.n_timesteps, comfort_price=c)
            cls = type("P", (Precooler,), {"depth": d})
            parts = [Participant("s", cls("s", mine), list(mine))]
            parts = fill_unclaimed(parts, backend.building_names, DiscoveryReport())
            res = simulate(backend, parts, tariff, call_timeout_seconds=10.0,
                           run_budget_seconds=900.0)
            ref = reference_costs(backend, parts, tariff)
            frame = res.ledger.to_frame()
            g = frame[frame.participant == "s"]
            score = g.total_cost.sum() / ref["s"]
            if d == depths[0]:
                control[c] = score
                row += "  %14.3f" % score
            else:
                row += "  %9.3f %+3.0f%%" % (
                    score, 100 * (control[c] - score) / control[c])
        print(row)
    print("  " + "-" * (18 + 16 * len(comforts)))
    print("  percentages are versus the no-pre-cool row of the same column")
    print()
    print("  Read along a row and down a column. Cheaper comfort should make")
    print("  deeper pre-cooling more attractive, because the degree-hours you")
    print("  are risking cost less. If it does not, that is worth explaining --")
    print("  a comfort band that steps down underneath you is the usual reason.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
