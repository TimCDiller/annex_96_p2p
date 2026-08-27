"""Does pre-cooling pay? Find out, rather than taking anyone's word for it.

    python examples/precool_study.py
    python examples/precool_study.py --days 7 --mass 2.0

Pre-cooling means deliberately over-cooling while energy is cheap, so the
building can coast through the expensive hours with the compressor off. The
fabric is a thermal store nobody charges you for, and on most of these
buildings it holds more energy than the battery does.

It is not free, though, and the argument cuts both ways:

  AGAINST                                  FOR
  a colder building leaks faster, so       the peak/off-peak price ratio
  the round trip is lossy                  a wide expensive window
  the baseline already sits near the       heavy thermal mass
  cheap edge of the band                   a wide comfort band
  degree-hours outside the band cost       cheap comfort penalties

Which side wins is a question about numbers, not about principle. This script
sweeps two of them at once -- how deep you pre-cool, and how large the price
spread is -- and prints the resulting bill for each combination. Somewhere in
that table the sign flips.

Read the table, then go and look for the other levers: `--mass` scales the
thermal capacitance of every building, `--band` widens the comfort band, and
`--comfort` changes the penalty. Each of them moves the boundary.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

from annex96p2p import Action, Agent, Thermostat
from annex96p2p.backends.mock import MockBackend, default_neighbourhood
from annex96p2p.baselines import SimpleRBCAgent
from annex96p2p.reference import reference_costs
from annex96p2p.registry import DiscoveryReport, Participant, fill_unclaimed
from annex96p2p.runner import simulate
from annex96p2p.tariff import Tariff


class Precooler(Agent):
    """The default agent, with one change: the thermostat follows the price.

    `depth` is how far below the centre of the comfort band to sit during the
    cheapest third of hours. 0.2 means "do not pre-cool at all" -- it is where
    the default thermostat sits anyway.
    """

    depth = 0.2
    coast = 0.4          # where to sit during the dearest quarter of hours

    def reset(self):
        self.thermostat = Thermostat()
        self.seen = []

    def _regime(self, mkt):
        self.seen.append(mkt.grid_import_price)
        if len(self.seen) < 24:
            return "normal"
        lo = float(np.percentile(self.seen, 33))
        hi = float(np.percentile(self.seen, 75))
        if mkt.grid_import_price <= lo:
            return "cheap"
        if mkt.grid_import_price >= hi:
            return "dear"
        return "normal"

    def bid(self, obs, mkt):
        self._r = self._regime(mkt)
        bid = mkt.new_bid()
        need = sum(o.passive_net_kwh for o in obs.values())
        # bid for the compressor too, not just the passive load
        need += sum(abs(self._aim_hvac(o)) * o.hp_max_electric_kw
                    for o in obs.values())
        if need > 0:
            bid.buy(mkt.grid_import_price - 0.01, need)
        elif need < 0:
            last = mkt.last_clearing_price
            bid.sell(max(0.01, (last - 0.01) if last else 0.02), -need)
        return bid.array

    def _aim(self):
        return {"cheap": self.depth, "dear": self.coast}.get(
            getattr(self, "_r", "normal"), 0.2)

    def _aim_hvac(self, o):
        return self.thermostat(o, aim=self._aim())

    def dispatch(self, obs, mkt, result):
        return {n: Action(hvac=self._aim_hvac(o), battery=0.0)
                for n, o in obs.items()}


def run(depth, adder, args):
    specs = default_neighbourhood(3)
    for s in specs:                       # scale the fabric if asked
        s.thermal_capacitance *= args.mass
        if args.band != 4.0:
            mid = 0.5 * (s.comfort_low + s.comfort_high)
            s.comfort_low, s.comfort_high = mid - args.band / 2, mid + args.band / 2
    backend = MockBackend(n_days=args.days, specs=specs)
    tariff = Tariff.synthetic(backend.n_timesteps, delivery_adder=adder,
                              comfort_price=args.comfort)

    mine = ["B03", "B07", "B11"]          # thermal-mass only: no battery, no PV,
    cls = type("P", (Precooler,), {"depth": depth})   # so this isolates the fabric
    parts = [Participant("student", cls("student", mine), mine)]
    parts = fill_unclaimed(parts, backend.building_names, DiscoveryReport())

    result = simulate(backend, parts, tariff)
    ref = reference_costs(backend, parts, tariff)
    frame = result.ledger.to_frame()
    g = frame[frame.participant == "student"]
    return (g.total_cost.sum() / ref["student"], g.energy_cost.sum(),
            g.comfort_cost.sum(), g.degree_hours.sum())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--mass", type=float, default=1.0,
                    help="scale every building's thermal capacitance")
    ap.add_argument("--band", type=float, default=4.0,
                    help="comfort band width in K (default 4)")
    ap.add_argument("--comfort", type=float, default=3.0,
                    help="$ per degC-hour outside the band")
    args = ap.parse_args(argv)

    depths = [0.2, 0.0, -0.3, -0.6, -0.9]
    adders = [0.075, 0.040, 0.020, 0.000]

    print(__doc__.split("Which side wins")[0].rstrip())
    print()
    print("  thermal mass x%.1f, comfort band %.1f K, comfort $%.2f/degC-h, %d days"
          % (args.mass, args.band, args.comfort, args.days))
    print("  portfolio: the three thermal-mass buildings -- no PV, no battery, so")
    print("  the ONLY flexibility on offer is the building fabric itself.")
    print()
    print("  score = bill / the default agent's bill, so lower is better.")
    print()
    print("  READ IT AGAINST THE FIRST ROW, not against 1.000. The whole table")
    print("  already bids for the compressor load rather than just the passive")
    print("  one, which is worth about 11% on its own -- that is why even the")
    print("  no-pre-cool row sits below 1.0. What pre-cooling adds is the")
    print("  difference between the first row and the ones under it.")
    print()
    header = "  %-22s" % "delivery adder ($/kWh)"
    for a in adders:
        header += "  %10s" % ("$%.3f" % a)
    print(header)
    print("  %-22s" % "peak/off-peak ratio" + "".join(
        "  %10s" % ("%.1fx" % ((0.310 + a) / (0.019 + a))) for a in adders))
    print("  " + "-" * (22 + 12 * len(adders)))

    best = (9, None)
    control = {}
    for d in depths:
        label = "no pre-cool" if d == 0.2 else "aim %+.1f" % d
        row = "  %-22s" % label
        for a in adders:
            score, energy, comfort, dh = run(d, a, args)
            if d == depths[0]:
                control[a] = score
                row += "  %10.3f" % score
            else:
                gain = 100 * (control[a] - score) / control[a]
                row += "  %6.3f %+3.0f%%" % (score, gain)
            if score < best[0]:
                best = (score, (d, a))
        print(row)
    print("  " + "-" * (22 + 12 * len(adders)))
    print("  %-22s  percentages are versus the no-pre-cool row"
          % "")

    print()
    d, a = best[1] if best[1] else (None, None)
    deepest = {aa: control[aa] for aa in adders}
    improved = any(best[0] < deepest[aa] - 1e-9 for aa in adders)
    if improved:
        print("  Best here: aim %+.1f at a delivery adder of $%.3f, scoring %.3f"
              % (d, a, best[0]))
        print("  against %.3f for the same agent without pre-cooling." % control[a])
        print()
        print("  Look along a row and down a column. The gain grows as the")
        print("  delivery adder shrinks, because that is what sets the ratio")
        print("  between peak and off-peak. Then ask what ELSE would move it,")
        print("  and test that too -- the flags below change the fabric, the")
        print("  band and the penalty.")
    else:
        print("  Nothing in this table beats the default. That is a real result,")
        print("  not a bug -- but it is a result about THESE numbers. Try")
        print("  --mass 3 (heavier fabric), --band 6 (more room to bank cold),")
        print("  or --comfort 1 (a cheaper penalty), and watch the boundary move.")
    print()
    print("  The delivery adder is the interesting axis. It is the flat part of")
    print("  the retail price, and it compresses the ratio the arbitrage lives")
    print("  on: wholesale swings 16x here, retail only 4x once $0.075 is added")
    print("  to every hour. Real tariffs do exactly this, which is a large part")
    print("  of why demand response is harder in practice than on paper.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
