"""Worked example submission. Copy this file, rename it, and edit.

    cp submissions/example_team.py submissions/team_yourname.py

Then check it loads, and run it:

    python -m annex96p2p check submissions/team_yourname.py
    python -m annex96p2p run --config config/smoke.yaml

Every building you do not claim is run by the built-in default agent, so you
always have a market full of opponents to trade against, even on your own
laptop with only your own file present.

THE ONE THING TO TAKE FROM THIS FILE
------------------------------------
Your bid and your dispatch must agree with each other.

You bid first, the market clears, and then you dispatch. Nothing forces the
energy you dispatch to match the energy you bought -- and nothing needs to,
because the arithmetic already punishes you for the mismatch:

  * buy more than you use, and the excess is curtailed. You paid for energy
    that vanished.
  * sell more than you have, and you cover the gap from the grid at the
    full retail price, having been paid the lower clearing price for it.

So this agent decides what it is going to do ONCE, in `_plan()`, and then
both `bid()` and `dispatch()` follow that same plan. An earlier draft of
this file did not, and it paid for 52 kWh it never stored while curtailing
154 kWh of its own solar.
"""
from typing import Dict

import numpy as np

from annex96p2p import (Action, Agent, BuildingObs, MarketInfo, Thermostat,
                        TradeResult)

# --------------------------------------------------------------------------
# Which buildings you control. Run `python -m annex96p2p buildings` to see
# what is available and what each one is made of.
#
# B01 is solar-rich, B02 is storage-rich, B03 is heavy thermal mass. That is
# a deliberately complementary set: B01 generates a midday surplus that B02
# can store, and B03 can shift its cooling load. Look at what you have been
# assigned before you write any strategy.
# --------------------------------------------------------------------------
BUILDINGS = ["B01", "B02", "B03"]

NAME = "example_team"


class ExampleAgent(Agent):
    """Price-aware battery, honest bidding, plain thermostat."""

    def reset(self) -> None:
        self.seen_prices = []
        self._plan_cache = None
        # A competent cooling thermostat, provided by the package. Read its
        # docstring: thermal control on the real CE1 buildings is harder than
        # it looks, and getting it wrong costs far more than any bidding
        # mistake. This agent uses it unmodified -- see the note in dispatch()
        # about why moving it with the price did not pay here.
        self.thermostat = Thermostat()

    # ------------------------------------------------------------- planning
    def _plan(self, obs: Dict[str, BuildingObs], mkt: MarketInfo) -> dict:
        """Decide what we are going to do this timestep.

        Called from `bid()`. `dispatch()` reuses the cached answer, so the
        two can never disagree.
        """
        self.seen_prices.append(mkt.grid_import_price)

        # Is this hour cheap or expensive by the standards of what we have
        # seen so far? Quartiles, not the median -- storing energy costs
        # round-trip losses, so it is only worth doing at genuinely low
        # prices, not merely below-average ones.
        history = np.asarray(self.seen_prices)
        if len(history) > 6:
            cheap = mkt.grid_import_price <= float(np.percentile(history, 30))
            expensive = mkt.grid_import_price >= float(np.percentile(history, 75))
        else:
            cheap = expensive = False

        # Net position if we left every heat pump and battery alone.
        passive = sum(o.passive_net_kwh for o in obs.values())
        surplus = max(0.0, -passive)

        # How much the batteries could take in, and give out, this hour.
        headroom = sum(min(o.battery_max_power_kw,
                           max(0.0, o.battery_capacity_kwh - o.battery_soc_kwh))
                       for o in obs.values() if o.has_battery)
        available = sum(min(o.battery_max_power_kw, o.battery_soc_kwh)
                        for o in obs.values() if o.has_battery)

        # 1. Always soak up our own surplus first. Energy we generate and do
        #    not use is curtailed for nothing, so putting it in our own
        #    battery is free. This never touches the market at all -- it is
        #    a transfer between two buildings we already own.
        from_own = min(surplus, headroom)

        # 2. Buy more only when energy is cheap AND the battery is fairly
        #    empty. Filling it overnight feels clever, but it leaves no room
        #    for our own solar at midday, and that solar would otherwise be
        #    curtailed for nothing. Keep space for the free energy.
        soc = sum(o.battery_soc_kwh for o in obs.values())
        capacity = sum(o.battery_capacity_kwh for o in obs.values())
        fairly_empty = capacity > 0 and soc < 0.4 * capacity
        from_market = (headroom - from_own) if (cheap and fairly_empty) else 0.0

        # 3. Discharge only when energy is expensive, and never more than we
        #    can consume ourselves.
        #
        #    This is the important line. A kWh out of our battery that we
        #    use ourselves saves us the full grid price. The same kWh sold
        #    into the market earns only the clearing price, which is capped
        #    at the grid price and in practice well below it. So always
        #    self-consume first and only sell what is genuinely spare.
        discharge = min(available, max(0.0, passive)) if expensive else 0.0

        battery = from_own + from_market - discharge
        return dict(cheap=cheap, expensive=expensive, passive=passive,
                    from_own=from_own, from_market=from_market,
                    discharge=discharge, battery=battery,
                    planned_net=passive + battery)

    # ----------------------------------------------------------------- bid
    def bid(self, obs: Dict[str, BuildingObs], mkt: MarketInfo) -> np.ndarray:
        """Return one signed quantity per price level.

        Positive means BUY at that price, negative means SELL at that price.
        Legal price levels are `mkt.price_grid`: $0.00 up to the current
        grid price, in one cent steps.
        """
        plan = self._plan(obs, mkt)
        self._plan_cache = plan
        bid = mkt.new_bid()

        # What we need after our own battery has been accounted for. This is
        # the part we cannot avoid, so we are willing to pay nearly the grid
        # price for it -- but never more, because the grid is our fallback.
        need = plan["passive"] + plan["from_own"] - plan["discharge"]

        if need > 0:
            bid.buy(price=mkt.grid_import_price - 0.01, quantity=need)
        elif need < 0:
            # Surplus we could not store ourselves. Careful here: energy is
            # perishable. If this does not sell it is curtailed and earns
            # exactly nothing, so the reserve price has to be low enough to
            # actually clear. We use the only feedback the market gives us
            # -- last timestep's clearing price -- and undercut it by a
            # cent. Holding out for a good price and getting nothing is the
            # classic way to lose money in this exercise.
            last = mkt.last_clearing_price
            reserve = max(0.01, (last - 0.01) if last else 0.02)
            bid.sell(price=reserve, quantity=-need)

        # Opportunistic battery charging, bid low because we do not have to
        # do it. Note this quantity is exactly what dispatch will charge.
        if plan["from_market"] > 0:
            bid.buy(price=min(0.06, 0.4 * mkt.grid_import_price),
                    quantity=plan["from_market"])

        return bid.array

    # ------------------------------------------------------------- dispatch
    def dispatch(self, obs: Dict[str, BuildingObs], mkt: MarketInfo,
                 result: TradeResult) -> Dict[str, Action]:
        """Execute the plan we just bid on.

        `result.price` is the clearing price this timestep and
        `result.quantity` is what we actually bought (+) or sold (-).
        """
        plan = self._plan_cache or self._plan(obs, mkt)

        # Scale the battery command to the energy we actually planned for.
        #
        # `battery` is in kWh, but the Action takes a fraction of rated
        # power. Sending 1.0 when the plan only called for 2 kWh out of a
        # 5 kW battery would pull an extra 3 kWh off the grid that we never
        # bid for -- at full retail price, because we did not buy it in the
        # market. Divide by the rated power instead.
        total_power = sum(o.battery_max_power_kw for o in obs.values()
                          if o.has_battery)
        battery_cmd = (float(np.clip(plan["battery"] / total_power, -1.0, 1.0))
                       if total_power > 0 else 0.0)

        # --- ON PRE-COOLING, WHICH IS NOT HERE -----------------------------
        # The obvious next move is to shift the thermostat with the price --
        # `self.thermostat.aim = -0.5 if plan["cheap"] else 0.8 if
        # plan["expensive"] else 0.2` -- banking cold in the fabric when
        # energy is cheap and coasting when it is dear. One line.
        #
        # It was measured, and on this parameterisation it LOSES: it took this
        # agent from 1.00 to 1.15, and a standalone test of the thermal lever
        # alone gave anywhere from +12% to +127% worse depending on the
        # portfolio. Two reasons, both worth understanding before you try it:
        #
        #   * the default thermostat already sits near the top of the comfort
        #     band, which for cooling is the cheap place to be. Pre-cooling is
        #     measured against that, not against a badly tuned baseline, so
        #     the margin is thinner than intuition suggests.
        #   * the round trip is lossy. A colder building leaks harder, and the
        #     expensive window here is only three or four hours wide, so you
        #     hold the cold for a long time to avoid a short peak.
        #
        # It is not a dead end -- it pays when the peak is wide, the price
        # ratio is large, or the fabric is heavy. Work out which of those you
        # have before spending effort on it.

        actions: Dict[str, Action] = {}
        for name, o in obs.items():
            hvac = self.thermostat(o)

            actions[name] = Action(
                hvac=hvac,
                battery=battery_cmd if o.has_battery else 0.0,
            )

        self._plan_cache = None
        return actions


# --------------------------------------------------------------------------
# The runner looks for this name. It may be a class or an instance.
# --------------------------------------------------------------------------
AGENT = ExampleAgent
