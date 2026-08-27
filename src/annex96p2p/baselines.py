"""The built-in default agents.

Every building nobody claims is run by `SimpleRBCAgent`. It is intentionally
crude. Read the two methods below carefully, because your job is to beat it,
and its weaknesses are where the easy wins are.

What it does badly, on purpose:

  BIDDING
    * It bids at exactly one price level, so it has no reservation price
      curve at all -- it is all-or-nothing.
    * As a buyer it offers one cent under the grid price. It therefore
      almost always gets filled, and pays nearly the most it possibly could.
    * As a seller it offers its whole surplus at one cent, so it hands
      essentially all of the gains from trade to whoever buys from it.
    * It bids its passive net position and completely ignores the fact that
      it is about to charge or discharge a battery. Bid and dispatch
      disagree with each other every single timestep.
    * It never looks at the previous clearing price.

  DISPATCH
    * The thermostat only ever reacts to the temperature it already has --
      it has no notion that energy is about to get more expensive.
    * It never pre-cools. Cheap overnight energy and a heavy building are
      free storage and it uses neither.
    * The battery runs on a fixed clock -- charge 10:00-15:00, discharge
      18:00-21:00 -- with no reference to the actual price, the actual PV
      output, or its own state of charge.
    * Nothing in dispatch reacts to the price it just cleared at.

A reasonable submission beats it comfortably. A good one beats it by a lot.
"""
from __future__ import annotations

from typing import Dict

import numpy as np

from .api import Action, Agent, BuildingObs, MarketInfo, TradeResult


class SimpleRBCAgent(Agent):
    """Naive rule-based agent. The default owner of unclaimed buildings."""

    #: Battery charging window (hour of day, inclusive).
    charge_hours = (10, 15)
    #: Battery discharging window (hour of day, inclusive).
    discharge_hours = (18, 21)
    #: Fraction of rated battery power used when it does act.
    battery_rate = 0.5
    #: Thermostat deadband, in degrees C.
    deadband = 0.5

    #: Proportional band for the thermostat, in K. The heat pumps in the real
    #: CE1 homes are ~3.5 kW electric at a COP over 4 -- about 15 kW of
    #: cooling on a single house. Bang-bang control on that overshoots the
    #: comfort band in both directions: left off the building runs to 28 degC,
    #: held fully on it reaches 16 degC, against a band of 19.8-23.8. So
    #: modulate rather than switch.
    prop_band = 1.5

    def _hvac(self, o):
        """Proportional cooling, aimed just inside the top of the band."""
        target = o.comfort_high - 0.5
        error = o.indoor_temperature - target
        if error <= 0:
            return 0.0
        return -float(min(1.0, error / self.prop_band))

    def bid(self, obs: Dict[str, BuildingObs], mkt: MarketInfo) -> np.ndarray:
        bid = mkt.new_bid()

        # Net position if the heat pump and battery were to sit still. Note
        # this is wrong by construction: dispatch below will move both.
        net = sum(o.passive_net_kwh for o in obs.values())

        if net > 0:
            # A deficit. Offer one cent under the grid price, which nearly
            # always fills, and nearly always at the worst acceptable price.
            bid.buy(price=mkt.grid_import_price - 0.01, quantity=net)
        elif net < 0:
            # A surplus. Cannot be exported to the grid, so dump it at one
            # cent rather than let it be curtailed. Almost free for the buyer.
            bid.sell(price=0.01, quantity=-net)

        return bid.array

    def dispatch(self, obs: Dict[str, BuildingObs], mkt: MarketInfo,
                 result: TradeResult) -> Dict[str, Action]:
        actions: Dict[str, Action] = {}

        for name, o in obs.items():
            # --- thermostat, with no price awareness whatsoever ------------
            hvac = self._hvac(o)

            # --- battery on a wall clock ----------------------------------
            battery = 0.0
            if o.has_battery:
                if self.charge_hours[0] <= o.hour <= self.charge_hours[1]:
                    battery = +self.battery_rate
                elif self.discharge_hours[0] <= o.hour <= self.discharge_hours[1]:
                    battery = -self.battery_rate

            actions[name] = Action(hvac=hvac, battery=battery)

        return actions


class DoNothingAgent(Agent):
    """Never trades, never stores, never conditions. The absolute floor.

    Useful as a reference point: whatever this scores is what a completely
    passive building costs, and it usually loses badly on comfort.
    """

    def bid(self, obs: Dict[str, BuildingObs], mkt: MarketInfo) -> np.ndarray:
        return mkt.empty_bid()

    def dispatch(self, obs: Dict[str, BuildingObs], mkt: MarketInfo,
                 result: TradeResult) -> Dict[str, Action]:
        return {name: Action() for name in obs}


class PriceTakerAgent(Agent):
    """Honest price-taker: submits a real reservation curve, thermostat only.

    A useful middle reference. It shows what submitting a *curve* rather
    than a single price buys you, with no clever dispatch at all.
    """

    def bid(self, obs: Dict[str, BuildingObs], mkt: MarketInfo) -> np.ndarray:
        bid = mkt.new_bid()
        net = sum(o.passive_net_kwh for o in obs.values())
        if net > 0:
            # Spread the demand across the cheap half of the grid: take a lot
            # if it is cheap, only a little if it is nearly grid price.
            levels = mkt.price_grid
            weights = np.linspace(1.0, 0.2, len(levels))
            weights /= weights.sum()
            for price, weight in zip(levels, weights):
                bid.buy(price, net * float(weight))
        elif net < 0:
            bid.sell(price=max(0.02, 0.4 * mkt.grid_import_price), quantity=-net)
        return bid.array

    def dispatch(self, obs: Dict[str, BuildingObs], mkt: MarketInfo,
                 result: TradeResult) -> Dict[str, Action]:
        actions = {}
        for name, o in obs.items():
            if o.indoor_temperature > o.comfort_high - 0.5:
                hvac = -1.0
            elif o.indoor_temperature < o.comfort_low + 0.5:
                hvac = +1.0
            else:
                hvac = 0.0
            actions[name] = Action(hvac=hvac, battery=0.0)
        return actions


BUILTIN_AGENTS = {
    "rbc": SimpleRBCAgent,
    "nothing": DoNothingAgent,
    "pricetaker": PriceTakerAgent,
}
