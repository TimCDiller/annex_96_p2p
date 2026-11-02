"""Worked example for the ORIGINAL 25-home exercise (docs/original_exercise.md).

The course runs on the altered Vermont stock instead -- start from
submissions/_template.py for that. This file is kept as a reading example:
its bid/dispatch structure carries over unchanged, its BUILDINGS do not.

To run it in the original exercise, alongside the other files in this folder:

    python -m annex96p2p check agents/basic/original_exercise/example_team.py
    python -m annex96p2p run --config config/default.yaml

Every building you do not claim is run by the built-in default agent, so you
always have a market full of opponents to trade against.

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
# Which buildings you control. Run `python -m annex96p2p buildings` for the
# full list and what each one is made of.
#
# These are ResStock ids, not friendly names. The neighbourhood is fairly
# uniform -- every home has a PV array sized to most of its own demand AND a
# battery holding a few hours of its own peak -- so there is no complementary
# set to hunt for. What differs between them is scale, and how volatile the
# occupant setpoint schedule is.
# --------------------------------------------------------------------------
# Building names are ResStock ids and they are dataset-specific: the Vermont
# and Texas neighbourhoods share none of them. If you switch datasets, run
#
#     python -m annex96p2p buildings
#
# and paste the ids you were assigned. A submission naming buildings that do
# not exist is reported and skipped -- it does not silently control nothing.
BUILDINGS = [
    "resstock-amy2018-2021-release-1-112208",
    "resstock-amy2018-2021-release-1-147002",
    "resstock-amy2018-2021-release-1-179247",
    "resstock-amy2018-2021-release-1-199613",
    "resstock-amy2018-2021-release-1-20199",
]

NAME = "example_team"


class ExampleAgent(Agent):
    """Price-aware battery, honest bidding, plain thermostat."""

    #: Where to sit in the comfort band: 0 is the centre, -1 the bottom
    #: edge, +1 the top. Dispatch targets the MIDDLE of the band.
    #:
    #: The middle is the neutral choice, and it is the one to beat. Aiming at
    #: an edge looks cheaper -- in a heating month every degree warmer than
    #: required leaks straight back out, so the bottom edge is the cheap end --
    #: but it leaves no margin: a target 0.3 K above the floor puts you under
    #: the floor on any ripple, and comfort is PRICED here. Sweep it yourself
    #: and find where the two effects balance.
    #:
    #: All four example agents use the same value, so the difference between
    #: them is bidding and nothing else.
    aim = 0.0

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

        # What we will physically consume: the passive load, PLUS the heat
        # pump we are about to run.
        #
        # Leaving that second term out is the mistake this whole file is about,
        # and it is easy to make because `passive_net_kwh` looks like the
        # answer. It is not: these compressors draw up to 19.5 kW electric on
        # the Vermont stock, so bidding without them means buying a fraction of
        # what you use and taking the rest from the grid at full retail. An
        # earlier version of this file did exactly that and scored WORSE than
        # the default agent.
        #
        # Getting the term in is not enough -- it has to be the RIGHT term.
        # `o.hp_draw_kwh(cmd)` is doing real work here, and the obvious
        # `abs(cmd) * o.hp_max_electric_kw` is wrong. Each building actuates
        # exactly one device, so the infeasible half of the command is dropped
        # by the physics: on these heating buildings a negative command is just
        # "off". It draws nothing. Bidding as though it drew full power buys
        # energy that is then curtailed rather than refunded. That error was
        # worth 20,020 kWh of imaginary demand over five days here, because
        # the thermostat spends 62% of its hours on the infeasible sign.
        #
        # `Thermostat` is idempotent per timestep, so asking it here and again
        # in dispatch() returns the same command both times.
        passive = sum(o.passive_net_kwh
                      + o.hp_draw_kwh(self.thermostat(o, aim=self.aim))
                      for o in obs.values())
        surplus = max(0.0, -passive)

        # How much the batteries could take in, and give out, this hour.
        headroom = sum(min(o.battery_max_power_kw,
                           max(0.0, o.battery_capacity_kwh - o.battery_soc_kwh))
                       for o in obs.values() if o.has_battery)
        available = sum(min(o.battery_max_power_kw, o.battery_soc_kwh)
                        for o in obs.values() if o.has_battery)

        return dict(cheap=cheap, expensive=expensive, need=passive,
                    surplus=surplus, headroom=headroom, available=available)

    # ----------------------------------------------------------------- bid
    def bid(self, obs: Dict[str, BuildingObs], mkt: MarketInfo) -> np.ndarray:
        """Return one signed quantity per price level.

        Positive means BUY at that price, negative means SELL at that price.
        Legal price levels are `mkt.price_grid`: $0.00 up to the current grid
        price, in one cent steps.

        The shape of this bid follows directly from what `dispatch()` will do.
        Since the battery covers anything the market does not, our demand
        splits into two parts with very different reservation prices.
        """
        plan = self._plan(obs, mkt)
        self._plan_cache = plan
        bid = mkt.new_bid()
        grid = mkt.grid_import_price
        need, available = plan["need"], plan["available"]

        # --- NEVER BID A POSITION YOU CANNOT PHYSICALLY HONOUR --------------
        # Once the thermostat command is fixed, `need` is exactly what the
        # buildings will consume, and the battery can move anywhere in
        # [-available, +headroom]. So the physical net has to land somewhere in
        #
        #     [need - available,  need + headroom]
        #
        # and settlement is `residual = physical net - cleared position`. Buy
        # past the top of that interval and the excess is CURTAILED -- you paid
        # for it and it vanished. Sell past the bottom and you buy the gap back
        # from the grid at full retail, having been paid less for it.
        #
        # So treat the interval as a budget and spend it in priority order.
        # Measured on 14 days of the Vermont January, over-buying past this
        # limit was 62% of all the energy this portfolio shed.
        buy_budget = max(0.0, need + plan["headroom"])
        sell_budget = max(0.0, available - need)

        if need > 0:
            # The part storage CANNOT cover. If we do not buy this it comes
            # off the meter at the full retail price, so we will pay almost
            # anything short of that.
            must_have = max(0.0, min(need - available, buy_budget))
            if must_have > 0:
                bid.buy(grid - 0.01, must_have)
                buy_budget -= must_have

            # The part storage CAN cover. We do not need this at all -- the
            # battery will do it for free. Buying is only worthwhile if it is
            # cheap enough to be worth sparing the charge for later, so bid
            # low and let it go if nobody sells at that price.
            optional = max(0.0, min(need - must_have, buy_budget))
            if optional > 0:
                bid.buy(min(0.5 * grid, grid - 0.02), optional)
                buy_budget -= optional
        else:
            # Surplus. Whatever our own battery can take is free and never
            # touches the market; the rest is perishable, because with no
            # grid export it is curtailed if it does not sell this hour. So
            # price it low enough to clear -- undercut the last clearing
            # price, which is the only feedback the market gives us.
            storable = min(plan["surplus"], plan["headroom"])
            perishable = min(plan["surplus"] - storable, sell_budget)
            if perishable > 0:
                last = mkt.last_clearing_price
                bid.sell(max(0.01, (last - 0.01) if last else 0.02), perishable)

        # Top the battery up when energy is genuinely cheap, but leave room
        # for our own solar later -- that solar is free and would otherwise be
        # curtailed, so filling the battery overnight can cost more than it saves.
        # Note this comes LAST and spends only what the budget has left: it is
        # the lowest-priority leg, so it is the one that gets trimmed.
        if plan["cheap"]:
            soc = sum(o.battery_soc_kwh for o in obs.values() if o.has_battery)
            cap = sum(o.battery_capacity_kwh for o in obs.values() if o.has_battery)
            if cap > 0 and soc < 0.4 * cap:
                room = min(plan["headroom"], 0.4 * cap - soc, buy_budget)
                if room > 0:
                    bid.buy(min(0.4 * grid, grid - 0.02), room)

        return bid.array

    # ------------------------------------------------------------- dispatch
    def dispatch(self, obs: Dict[str, BuildingObs], mkt: MarketInfo,
                 result: TradeResult) -> Dict[str, Action]:
        """Execute the plan we just bid on.

        `result.price` is the clearing price this timestep and
        `result.quantity` is what we actually bought (+) or sold (-).
        """
        plan = self._plan_cache or self._plan(obs, mkt)

        # --- THE BATTERY GOES BEFORE THE GRID, ALWAYS ----------------------
        # Work out what the buildings are actually about to consume, subtract
        # whatever the market gave us, and cover the remainder from storage
        # before touching the meter. Settlement makes the reason plain:
        #
        #     residual = physical net - cleared position
        #     residual > 0  ->  bought from the grid at the FULL retail price
        #     residual < 0  ->  curtailed, and earns nothing
        #
        # So while the battery holds charge there is never a reason to import:
        # a kWh out of storage is already paid for, and a kWh off the grid is
        # the most expensive kWh available. The reverse holds too -- if the
        # market handed us more than we need, store it rather than throw it
        # away. Aim the battery at making the residual zero and both cases
        # fall out of one line.
        #
        # Note this ignores the market position deliberately: whether we
        # bought or sold this hour does not change the fact that stored energy
        # beats grid energy right now.
        #
        # It is a simple rule, not an optimal one, and the gap is worth
        # understanding. Draining the battery to avoid a $0.09 import in a
        # cheap hour spends a kWh that would have offset $0.39 at the evening
        # peak. A better agent holds some charge back for the dear hours. That
        # is your move, not this file's.
        need = plan["need"]                          # already includes the heat pump
        residual = need - result.quantity

        # --- SPLIT THE MOVE BY EACH BUILDING'S OWN ROOM, NOT BY ITS SIZE ----
        # The portfolio move has to be shared out across the batteries, and the
        # obvious way -- one command sent to all of them -- is wrong. A single
        # `battery_cmd` gives each building a share proportional to its rated
        # POWER, but what a building can actually take is limited by its own
        # remaining CAPACITY. Send the same command to a full battery and a
        # half-empty one and the full one silently clips, so the portfolio
        # absorbs less than it bid for and the difference is curtailed.
        #
        # So weight each building by the room it actually has this hour. The
        # shares then sum to exactly the move, and no building is asked for
        # more than it can do.
        room = {}
        for name, o in obs.items():
            if not o.has_battery:
                continue
            room[name] = (min(o.battery_max_power_kw, o.battery_soc_kwh)
                          if residual > 0 else
                          min(o.battery_max_power_kw,
                              max(0.0, o.battery_capacity_kwh - o.battery_soc_kwh)))
        total_room = sum(room.values())

        # The move is capped by the room available, which is the whole point:
        # `bid()` already limited the position to this same interval, so in the
        # ordinary case nothing is clipped here at all.
        magnitude = min(abs(residual), total_room)
        sign = -1.0 if residual > 0 else 1.0                # -1 discharge

        # `Action.battery` is a fraction of rated POWER, not an energy. Sending
        # 1.0 when the plan called for 2 kWh out of a 5 kW battery would pull
        # the extra 3 kWh off the grid at full retail.
        battery_cmd = {}
        for name, r in room.items():
            share = magnitude * (r / total_room) if total_room > 0 else 0.0
            power = obs[name].battery_max_power_kw
            battery_cmd[name] = (float(np.clip(sign * share / power, -1.0, 1.0))
                                 if power > 0 else 0.0)

        # --- PRE-COOLING: THE HOOK IS HERE, THE STRATEGY IS NOT ------------
        # The thermostat takes an `aim`: where inside the comfort band to sit,
        # from -1 at the bottom edge through 0 at the centre to +1 at the top.
        # Passing it per call costs one line:
        #
        #     hvac = self.thermostat(o, aim=-0.6 if plan["cheap"] else 0.4)
        #
        # Sitting low banks cold in the fabric while energy is cheap; sitting
        # high lets the building coast through the expensive hours with the
        # compressor off. The fabric is a thermal store nobody bills you for,
        # and on most of these buildings it holds more than the battery does.
        #
        # Whether it pays here is an open question and the answer is not
        # obvious. Working against you: a colder building leaks faster, so the
        # round trip is lossy; the baseline thermostat already sits near the
        # cheap edge of the band, so you are not measuring against a bad
        # controller; and every degree-hour outside the band is charged.
        # Working for you: the peak/off-peak price ratio, the width of the
        # expensive window, the thermal mass, and how much slack the comfort
        # band leaves.
        #
        # All of those are measurable. Sweep `aim` over a few values, compare
        # the bills, and find out. One thing to watch on this stock: the
        # comfort band moves by up to 7 K within a single building, so cold
        # banked against a band that then steps down under you is wasted.

        actions: Dict[str, Action] = {}
        for name, o in obs.items():
            hvac = self.thermostat(o, aim=self.aim)

            actions[name] = Action(
                hvac=hvac,
                battery=battery_cmd.get(name, 0.0),
            )

        self._plan_cache = None
        return actions


# --------------------------------------------------------------------------
# The runner looks for this name. It may be a class or an instance.
# --------------------------------------------------------------------------
AGENT = ExampleAgent
