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

from .api import (Action, Agent, BuildingObs, MarketInfo, Thermostat,
                  TradeResult)


class SimpleRBCAgent(Agent):
    """Naive rule-based agent. The default owner of unclaimed buildings."""

    #: Battery charging window (hour of day, inclusive).
    charge_hours = (10, 15)
    #: Battery discharging window (hour of day, inclusive).
    discharge_hours = (18, 21)
    #: Fraction of rated battery power used when it does act.
    battery_rate = 0.5
    def reset(self) -> None:
        self._thermostat = Thermostat()

    def _hvac(self, o):
        """Delegate to the shared thermostat.

        The default agent is meant to be beaten on PRICE and on the MARKET,
        which is what this exercise is about -- not on being unable to hold a
        thermostat. So its thermal control is competent; everything else about
        it is naive.
        """
        if not hasattr(self, "_thermostat"):
            self._thermostat = Thermostat()
        return self._thermostat(o)

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


BUILTIN_AGENTS = {"rbc": SimpleRBCAgent}
