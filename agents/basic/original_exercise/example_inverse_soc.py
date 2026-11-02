"""Example agent: price the battery inversely to how full it is.

A kWh in the battery is worth a lot when the battery is nearly empty and very
little when it is nearly full. So put the buy/sell divider at

    divider = (1 - state_of_charge) x grid_price

and quote a deadband below it:

    sell at   divider
    buy  at   divider - deadband

    90% full ->  divider 10% of grid:  sells at 10%, buys at  5%
    50% full ->  divider 50% of grid:  sells at 50%, buys at 45%
    10% full ->  divider 90% of grid:  sells at 90%, buys at 85%

Full, and it lets energy go cheaply and only restocks at a bargain. Empty, and
it holds out to sell while paying well to refill. No forecast, no clock, no
model: the inventory does the reasoning, and what falls out is
buy-the-midday-glut, sell-into-the-evening-peak.

Contrast with `example_flat50.py`, whose divider does not move with the battery
at all. The difference between them is the value of pricing your own inventory.

PERISHABLE ENERGY IS NOT PRICED THIS WAY
----------------------------------------
The rule above prices STORABLE energy. Surplus off the roof cannot be held: with
no grid export, whatever does not go into the battery or sell this hour is
curtailed and earns exactly nothing. Its reservation price is therefore near
zero however full the battery is, so it gets its own leg at the floor. Pricing
surplus with the inventory rule is an expensive mistake -- it holds out for a
price the market will not pay and the energy simply evaporates.

IT LOSES ON THE VERMONT WINTER, AND THAT IS THE INTERESTING PART
----------------------------------------------------------------
Measured over a week in January it scores about 1.06 -- worse than the default
agent. The rule treats the battery as an ARBITRAGE asset: something you fill
cheap and empty dear. That is right in a cooling month, where a midday solar
surplus is worth banking for the evening peak.

In a Vermont January it is wrong. The building is in deficit almost every hour,
so the battery is a pass-through rather than a store, and the rule's own logic
turns against it: an empty battery puts the divider near the grid price, so it
bids up to 95% of grid to refill -- buying expensive energy it did not need, and
paying the round-trip loss on it.

Whether that is a flaw in the agent or a fact about the season is precisely the
question to answer in your report. The fix is not obvious and it is not one
line.

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
    "resstock-amy2018-2021-release-1-376570",
    "resstock-amy2018-2021-release-1-408344",
    "resstock-amy2018-2021-release-1-409896",
    "resstock-amy2018-2021-release-1-411001",
    "resstock-amy2018-2021-release-1-425540",
]

NAME = "example_inverse_soc"


class InverseSocAgent(Agent):
    #: Gap between the sell and buy quotes, as a fraction of the grid price.
    deadband = 0.05
    #: Never quote below this -- the price grid is in whole cents, so a
    #: divider near zero would otherwise round away entirely.
    floor = 0.01
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

        divider = (1.0 - full) * grid
        ask = min(grid, max(self.floor, divider))
        offer = max(self.floor, divider - self.deadband * grid)

        need = sum(o.passive_net_kwh
                   + o.hp_draw_kwh(self.thermostat(o, aim=self.aim))
                   for o in obs.values())
        available = min(power, soc)
        room = min(power, max(0.0, cap - soc))

        # Must-have: the part storage cannot cover. Not buying it means paying
        # the utility retail, so it is not priced off the divider.
        must_have = max(0.0, need - available)
        if must_have > 0:
            bid.buy(grid - 0.01, must_have)

        # Perishable: surplus the battery cannot absorb. Near-zero reservation
        # price, whatever the inventory says.
        surplus = max(0.0, -need)
        absorbed = min(room, surplus)
        spill = surplus - absorbed
        if spill > 0:
            last = mkt.last_clearing_price
            bid.sell(max(self.floor, (last - 0.01) if last else 0.02), spill)

        # Storable: this is where the divider applies.
        spare = max(0.0, available - max(0.0, need))
        if spare > 0:
            bid.sell(ask, spare)
        if room - absorbed > 0:
            bid.buy(offer, room - absorbed)

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


AGENT = InverseSocAgent
