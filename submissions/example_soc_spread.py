"""Second worked example: a market maker that prices its battery by how full it is.

`example_team.py` is a rule stack -- a sequence of if-statements about the
price. This is a different style entirely: it computes a *reservation price*
for stored energy and quotes around it. Read both before you write your own,
because they fail in different ways.

THE IDEA
--------
A kWh in the battery is worth a lot when the battery is nearly empty and very
little when it is nearly full. So quote around a mid-price that falls as the
battery fills:

    mid = (1 - state_of_charge) x grid_price

    90% full ->  mid = 10% of the grid price:  sell at 15%, buy at  5%
    50% full ->  mid = 50% of the grid price:  sell at 55%, buy at 45%
    10% full ->  mid = 90% of the grid price:  sell at 95%, buy at 85%

Full, and it lets energy go cheaply and only restocks at a bargain. Empty, and
it holds out for a high price to sell while paying well to refill. There is no
forecast, no clock and no learned model in here -- the inventory does all the
reasoning, and the behaviour that falls out is buy-the-midday-glut,
sell-into-the-evening-peak.

THREE LEGS, NOT TWO
-------------------
The spread above prices STORABLE energy. A real portfolio has two other
positions that need pricing differently, and leaving either out is expensive:

  must-have   the load your buildings will consume regardless. The grid is
              your fallback, so bid just under the grid price -- you are not
              choosing whether to buy this, only from whom. Omit this leg and
              the agent refuses to buy energy it needs whenever the battery is
              full, then pays the utility full price for it.

  perishable  PV surplus. It CANNOT be stored beyond this hour: with no grid
              export, whatever does not go into your own battery or sell right
              now is curtailed and earns exactly zero. Its reservation price
              is therefore near zero however full the battery is. An earlier
              draft of this file priced surplus with the inventory rule and
              curtailed 680 kWh in a week as a result.

  storable    the battery. This is where the SOC spread applies.

WHAT IT SCORES, AND WHY YOU SHOULD NOT TRUST THAT NUMBER
--------------------------------------------------------
On the mock neighbourhood, one week, against the default agent, this file
scores about 0.79 on the balanced portfolio it is assigned. On the real CE1
homes a five-building portfolio scores about 0.85.

Those numbers come from THIS repo's synthetic 12-building stock and synthetic
price series. They will not carry over to the real Annex 96 neighbourhood, and
the reason is worth understanding rather than ignoring:

Every one of the 25 homes has BOTH a PV array sized to 70-100% of its own
demand AND a battery holding 1.5-3.5 hours of its own peak. That uniformity
matters for this strategy: there is no structural seller to buy cheaply from,
because everyone is long solar at the same hours and everyone has somewhere to
put it. The margin here comes from pricing your own inventory well, not from
finding a counterparty who is short.
"""
import numpy as np

from annex96p2p import Action, Agent, Thermostat

# Five of the real homes. Each has both PV and storage, so all three bidding
# legs below are exercised -- that is the normal case here, not a special one.
BUILDINGS = [
    "resstock-amy2018-2021-release-1-223548",
    "resstock-amy2018-2021-release-1-237652",
    "resstock-amy2018-2021-release-1-249373",
    "resstock-amy2018-2021-release-1-276878",
    "resstock-amy2018-2021-release-1-283562",
]
NAME = "example_soc_spread"


class SocSpreadAgent(Agent):
    #: Half-spread, as a fraction of the grid price.
    spread = 0.05
    #: Never quote below this. Note the price grid is in 1 cent steps, so at a
    #: grid price of $0.10 the whole spread is one level wide -- the quotes get
    #: coarse at cheap hours whether you like it or not.
    floor = 0.01
    #: How far below the top of the comfort band to sit.
    deadband = 0.5

    # ------------------------------------------------------------------ bid
    def bid(self, obs, mkt):
        bid = mkt.new_bid()
        grid = mkt.grid_import_price

        soc = sum(o.battery_soc_kwh for o in obs.values())
        cap = sum(o.battery_capacity_kwh for o in obs.values())
        power = sum(o.battery_max_power_kw for o in obs.values())
        full = (soc / cap) if cap > 0 else 1.0

        # --- the reservation price for stored energy -------------------
        mid = (1.0 - full) * grid
        ask = min(grid, mid + self.spread * grid)
        offer = max(self.floor, mid - self.spread * grid)

        # --- what the buildings will physically consume this hour ------
        # abs(), not the signed value. `hvac` is -1 for full cooling and +1
        # for full heating, and BOTH consume electricity. Using the signed
        # value subtracts the compressor load instead of adding it, so the
        # agent believes it has a surplus while it is actually consuming --
        # it then sells energy it does not have and buys it back from the grid
        # at full price. Getting this one character wrong was worth more than
        # every other line in this file put together.
        need = sum(o.passive_net_kwh + abs(self._hvac(o)) * o.hp_max_electric_kw
                   for o in obs.values())

        # --- leg 1: must-have -----------------------------------------
        if need > 0:
            bid.buy(grid - 0.01, need)

        can_charge = min(power, max(0.0, cap - soc)) if cap > 0 else 0.0
        can_discharge = min(power, soc) if cap > 0 else 0.0

        # --- leg 2: perishable ----------------------------------------
        surplus = max(0.0, -need)
        absorbed = min(can_charge, surplus)      # free, never touches the market
        perishable = surplus - absorbed
        if perishable > 0:
            last = mkt.last_clearing_price
            bid.sell(max(self.floor, (last - 0.01) if last else 0.02), perishable)

        # --- leg 3: storable, at the SOC spread -----------------------
        if cap > 0:
            # Never offer to sell what we are about to consume ourselves:
            # self-consumption saves the full grid price, selling earns the
            # clearing price, which is capped below it.
            spare = max(0.0, can_discharge - max(0.0, need))
            if spare > 0:
                bid.sell(ask, spare)
            from_market = can_charge - absorbed
            if from_market > 0:
                bid.buy(offer, from_market)

        return bid.array

    # ------------------------------------------------------------- dispatch
    def dispatch(self, obs, mkt, result):
        q = result.quantity                      # + bought, - sold
        hvac = {name: self._hvac(o) for name, o in obs.items()}
        need = sum(o.passive_net_kwh + abs(hvac[n]) * o.hp_max_electric_kw
                   for n, o in obs.items())

        # Settlement is residual = physical_net - q, where a positive residual
        # is bought from the grid and a negative one is curtailed. Both cost
        # money, so aim the battery at making the residual zero:
        #
        #     physical_net = need + charge - discharge  ==  q
        #
        # A positive target means store the surplus, negative means drain.
        # This single line is what "drain the battery before buying from the
        # grid" actually means, and it delivers whatever was sold as well.
        target = q - need

        power = sum(o.battery_max_power_kw for o in obs.values() if o.has_battery)
        soc = sum(o.battery_soc_kwh for o in obs.values() if o.has_battery)
        cap = sum(o.battery_capacity_kwh for o in obs.values() if o.has_battery)

        if power > 0:
            if target > 0:
                move = min(target, power, max(0.0, cap - soc))
            else:
                move = -min(-target, power, soc)
            # The Action takes a FRACTION of rated power, not an energy.
            command = float(np.clip(move / power, -1.0, 1.0))
        else:
            command = 0.0

        return {n: Action(hvac=hvac[n], battery=command if o.has_battery else 0.0)
                for n, o in obs.items()}

    # -------------------------------------------------------------- helpers
    def reset(self) -> None:
        # Deliberately the same thermostat as the default agent, so the
        # difference in the result comes from bidding and storage rather than
        # from thermal control.
        self.thermostat = Thermostat()

    def _hvac(self, o):
        if not hasattr(self, "thermostat"):
            self.thermostat = Thermostat()
        return self.thermostat(o)


AGENT = SocSpreadAgent
