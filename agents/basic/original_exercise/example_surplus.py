"""Example agent: the obvious rules, in the obvious order.

No forecast and no reservation curve -- just four rules a homeowner would
recognise, applied in priority order:

  1. self-consume first. Anything the roof produces that the house can use is
     the cheapest energy available, and it never touches the market.
  2. charge whenever there is surplus. Better in the battery than curtailed.
  3. sell cheap when full. There is nowhere left to put it, and unsold surplus
     earns nothing, so take almost any price.
  4. buy at 95% of the grid price when empty. With nothing in storage the
     alternative is the meter, so pay nearly what the meter would charge.

It is a good baseline precisely because it is not clever. It never holds charge
back for the evening peak, never shades a bid, and never asks what energy is
worth later -- so whatever it earns is what the physical flexibility is worth
before any strategy is applied. Read it alongside `example_inverse_soc.py`,
which prices the same battery by how full it is, and `example_flat50.py`, which
prices it off a forecast instead.

DISPATCH, SHARED BY ALL THE EXAMPLE AGENTS
------------------------------------------
Hug the LOWER edge of the comfort band, cover the load from the battery, and
touch the grid only when the battery is flat.
"""
from typing import Dict

import numpy as np

from annex96p2p import Action, Agent, BuildingObs, MarketInfo, Thermostat, TradeResult

# Building names are ResStock ids and they are dataset-specific: the Vermont
# and Texas neighbourhoods share none of them. If you switch datasets, run
#
#     python -m annex96p2p buildings
#
# and paste the ids you were assigned. A submission naming buildings that do
# not exist is reported and skipped -- it does not silently control nothing.
BUILDINGS = [
    "resstock-amy2018-2021-release-1-4421",
    "resstock-amy2018-2021-release-1-460412",
    "resstock-amy2018-2021-release-1-467125",
    "resstock-amy2018-2021-release-1-481052",
    "resstock-amy2018-2021-release-1-485614",
]

NAME = "example_surplus"


class SurplusAgent(Agent):
    #: What to pay, as a fraction of the grid price, when the battery is empty.
    when_empty = 0.95
    #: What to ask when the battery is full. Low: it has to go somewhere.
    when_full = 0.05
    #: Bounds for calling the battery full or empty.
    full_at = 0.95
    empty_at = 0.05
    #: Where to sit in the comfort band: 0 is the centre, -1 the bottom
    #: edge, +1 the top. Dispatch targets the middle.
    aim = 0.0

    def reset(self) -> None:
        self.thermostat = Thermostat()

    # ----------------------------------------------------------------- bid
    def bid(self, obs: Dict[str, BuildingObs], mkt: MarketInfo) -> np.ndarray:
        bid = mkt.new_bid()
        grid = mkt.grid_import_price

        soc = sum(o.battery_soc_kwh for o in obs.values() if o.has_battery)
        cap = sum(o.battery_capacity_kwh for o in obs.values() if o.has_battery)
        power = sum(o.battery_max_power_kw for o in obs.values() if o.has_battery)
        full = (soc / cap) if cap > 0 else 1.0
        available = min(power, soc)
        room = min(power, max(0.0, cap - soc))

        need = sum(o.passive_net_kwh
                   + o.hp_draw_kwh(self.thermostat(o, aim=self.aim))
                   for o in obs.values())

        # Rule 1: self-consumption is implicit. `need` is already net of our
        # own generation, so anything the house can use never appears here.
        if need > 0:
            # Rule 4, and the part storage cannot cover. Both come to the same
            # thing: pay near the grid price rather than pay the grid.
            must_have = max(0.0, need - available)
            if must_have > 0:
                bid.buy(grid - 0.01, must_have)
            if full <= self.empty_at:
                # Empty: buy the rest too, at 95% of grid.
                rest = need - must_have
                if rest > 0:
                    bid.buy(self.when_empty * grid, rest)
        else:
            # Rule 2: surplus goes into the battery first, for free.
            surplus = -need
            absorbed = min(room, surplus)
            spill = surplus - absorbed
            # Rule 3: what will not fit has to be sold cheap or lost.
            if spill > 0:
                bid.sell(max(0.01, self.when_full * grid), spill)

        # Rule 3 again, for a battery that is full even without new surplus:
        # start letting charge go, because nothing more can come in.
        if full >= self.full_at:
            spare = max(0.0, available - max(0.0, need))
            if spare > 0:
                bid.sell(max(0.01, self.when_full * grid), spare)

        return bid.array

    # ------------------------------------------------------------ dispatch
    def dispatch(self, obs: Dict[str, BuildingObs], mkt: MarketInfo,
                 result: TradeResult) -> Dict[str, Action]:
        hvac = {n: self.thermostat(o, aim=self.aim) for n, o in obs.items()}
        need = sum(o.passive_net_kwh + o.hp_draw_kwh(hvac[n])
                   for n, o in obs.items())

        residual = need - result.quantity
        power = sum(o.battery_max_power_kw for o in obs.values() if o.has_battery)
        soc = sum(o.battery_soc_kwh for o in obs.values() if o.has_battery)
        cap = sum(o.battery_capacity_kwh for o in obs.values() if o.has_battery)
        move = (-min(residual, power, soc) if residual > 0
                else min(-residual, power, max(0.0, cap - soc)))
        cmd = float(np.clip(move / power, -1.0, 1.0)) if power > 0 else 0.0

        return {n: Action(hvac=hvac[n], battery=cmd if o.has_battery else 0.0)
                for n, o in obs.items()}


AGENT = SurplusAgent
