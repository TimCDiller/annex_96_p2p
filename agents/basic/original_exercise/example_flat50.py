"""Example agent: assume the market always clears at half the grid price.

    "The clearing price will be 50% of the grid price. Bid around that."

The crudest possible forecast, and worth having in the market for exactly that
reason. It puts its buy/sell divider at half of the price it expects NEXT
timestep and lets the auction sort it out.

WHY NEXT TIMESTEP AND NOT THIS ONE
----------------------------------
You bid before the market clears, and the energy you are bidding for is
consumed over the hour ahead. What you are really asking is "is it worth
holding my charge for another hour?" -- so the price that matters is the one
coming, not the one just gone. `MarketInfo` does not hand you a forecast, so
this agent builds the crudest possible one: the mean price it has seen at that
hour of the day so far. Two days in, that is quite accurate here, because the
tariff has a strong daily shape.

WHAT IT IGNORES
---------------
Its own state, almost entirely. The divider does not move with the battery, so
a full battery still asks the same price as an empty one -- the only concession
is that it stops buying when full and stops selling when empty. Compare it with
`example_inverse_soc.py`, which prices its inventory properly, and see how much
that concession is worth.

DISPATCH, SHARED BY ALL THE EXAMPLE AGENTS
------------------------------------------
Hug the LOWER edge of the comfort band, cover the load from the battery, and
touch the grid only when the battery is flat. Hugging the bottom rather than
the top is a deliberate choice: it keeps the fabric as cold as comfort allows,
which is thermal storage banked for free, at the cost of running the compressor
harder than strictly necessary. Whether that trade is worth it is a good
question to put a number on.
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
    "resstock-amy2018-2021-release-1-216895",
    "resstock-amy2018-2021-release-1-223581",
    "resstock-amy2018-2021-release-1-245723",
    "resstock-amy2018-2021-release-1-247942",
    "resstock-amy2018-2021-release-1-319918",
]

NAME = "example_flat50"


class Flat50Agent(Agent):
    #: Where the divider sits, as a fraction of the expected next-hour price.
    share = 0.50
    #: Half-width of the no-trade zone around the divider, same units.
    deadband = 0.05
    #: Treat the battery as full/empty outside these bounds.
    full_at = 0.97
    empty_at = 0.03
    #: Where to sit in the comfort band: 0 is the centre, -1 the bottom
    #: edge, +1 the top. Dispatch targets the middle.
    aim = 0.0

    def reset(self) -> None:
        self.thermostat = Thermostat()
        self._by_hour: Dict[int, list] = {}

    # ------------------------------------------------------- the forecast
    def _next_price(self, mkt: MarketInfo) -> float:
        """Mean price seen at the next hour of the day, or today's if unseen."""
        self._by_hour.setdefault(mkt.hour, []).append(mkt.grid_import_price)
        seen = self._by_hour.get((mkt.hour + 1) % 24)
        return float(np.mean(seen)) if seen else mkt.grid_import_price

    # ----------------------------------------------------------------- bid
    def bid(self, obs: Dict[str, BuildingObs], mkt: MarketInfo) -> np.ndarray:
        bid = mkt.new_bid()
        grid = mkt.grid_import_price
        divider = self.share * self._next_price(mkt)

        soc = sum(o.battery_soc_kwh for o in obs.values() if o.has_battery)
        cap = sum(o.battery_capacity_kwh for o in obs.values() if o.has_battery)
        power = sum(o.battery_max_power_kw for o in obs.values() if o.has_battery)
        full = (soc / cap) if cap > 0 else 1.0

        need = sum(o.passive_net_kwh
                   + o.hp_draw_kwh(self.thermostat(o, aim=self.aim))
                   for o in obs.values())

        # Whatever storage cannot cover would come off the meter at retail, so
        # buy that at almost any price rather than lose it.
        available = min(power, soc)
        must_have = max(0.0, need - available)
        if must_have > 0:
            bid.buy(min(grid - 0.01, grid), must_have)

        # The discretionary legs, both priced off the divider.
        if full < self.full_at:
            room = min(power, max(0.0, cap - soc))
            if room > 0:
                bid.buy(max(0.01, divider - self.deadband * grid), room)
        if full > self.empty_at:
            spare = max(0.0, available - max(0.0, need))
            if spare > 0:
                bid.sell(min(grid, divider + self.deadband * grid), spare)

        # Surplus generation is perishable -- with no grid export it is
        # curtailed if it does not sell this hour, so it is not priced off the
        # divider at all.
        surplus = max(0.0, -need)
        if surplus > 0:
            spill = max(0.0, surplus - min(power, max(0.0, cap - soc)))
            if spill > 0:
                bid.sell(0.01, spill)

        return bid.array

    # ------------------------------------------------------------ dispatch
    def dispatch(self, obs: Dict[str, BuildingObs], mkt: MarketInfo,
                 result: TradeResult) -> Dict[str, Action]:
        hvac = {n: self.thermostat(o, aim=self.aim) for n, o in obs.items()}
        need = sum(o.passive_net_kwh + o.hp_draw_kwh(hvac[n])
                   for n, o in obs.items())

        # Battery before grid: aim at a zero residual so nothing is imported
        # while there is charge, and nothing is curtailed while there is room.
        residual = need - result.quantity
        power = sum(o.battery_max_power_kw for o in obs.values() if o.has_battery)
        soc = sum(o.battery_soc_kwh for o in obs.values() if o.has_battery)
        cap = sum(o.battery_capacity_kwh for o in obs.values() if o.has_battery)
        move = (-min(residual, power, soc) if residual > 0
                else min(-residual, power, max(0.0, cap - soc)))
        cmd = float(np.clip(move / power, -1.0, 1.0)) if power > 0 else 0.0

        return {n: Action(hvac=hvac[n], battery=cmd if o.has_battery else 0.0)
                for n, o in obs.items()}


AGENT = Flat50Agent
