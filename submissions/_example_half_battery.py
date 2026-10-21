"""A simple complete agent: hold the battery near half full, trade the rest.

Copy it to start from it instead of the template:

    cp submissions/_example_half_battery.py submissions/team_yourname.py

Each hour, for every building:

  1. the shared thermostat sets the heat pump, aiming at the middle of the band;
  2. the battery is steered towards 50% state of charge -- charging when below,
     discharging when above, never faster than its rated power;
  3. what is left over is netted across the portfolio and bid into the market:
       surplus  -> SELL it, accepting anything from 20% of the grid price up;
       shortfall -> BUY it, paying at most 80% of the grid price.
     Whatever the market does not fill comes from the grid at the full price,
     and unsold surplus is curtailed.

The plan is made ONCE, in bid(), and dispatch() carries out that same plan,
so what the agent bids and what it physically does always agree. That also
matters for the thermostat: it is a PI controller with memory, so it must be
called exactly once per hour.

Deliberately simple. Things it ignores, each one a place to improve it:
  * the price shape -- it holds 50% all day instead of filling the battery
    in the cheap midday hours and emptying it into the 07:00 and 18:00 peaks;
  * the one-hour lag in observed PV (see the README) -- it bids this hour's
    observation as if it were next hour's output;
  * battery losses -- the 90% round trip is not in its arithmetic.
"""
from annex96p2p import Action, Agent, Thermostat

NAME = "half_battery"
# Replace with your assigned buildings. Placeholder: one PV-rich, one storage-rich home.
BUILDINGS = ["resstock-amy2018-2021-release-1-112208",
             "resstock-amy2018-2021-release-1-147002"]

TARGET_SOC = 0.50     # fraction of capacity to hold the battery at
SELL_AT = 0.20        # lowest price accepted for surplus, as a fraction of grid price
BUY_AT = 0.80         # highest price paid for a shortfall, as a fraction of grid price


class HalfBatteryAgent(Agent):

    def reset(self):
        self.thermostat = Thermostat()
        self.plan = {}

    def _battery(self, o):
        """Battery command (-1..+1) and the kWh it moves, steering towards TARGET_SOC."""
        if not o.has_battery or o.battery_max_power_kw <= 0:
            return 0.0, 0.0
        gap_kwh = TARGET_SOC * o.battery_capacity_kwh - o.battery_soc_kwh
        kwh = max(-o.battery_max_power_kw, min(o.battery_max_power_kw, gap_kwh))
        return kwh / o.battery_max_power_kw, kwh       # one-hour step: kW == kWh

    def bid(self, obs, mkt):
        self.plan = {}
        net_kwh = 0.0
        for name, o in obs.items():
            hvac = self.thermostat(o)
            battery, battery_kwh = self._battery(o)
            self.plan[name] = Action(hvac=hvac, battery=battery)
            net_kwh += o.passive_net_kwh + o.hp_draw_kwh(hvac) + battery_kwh

        bid = mkt.new_bid()
        if net_kwh < 0:
            bid.sell(price=SELL_AT * mkt.grid_import_price, quantity=-net_kwh)
        elif net_kwh > 0:
            bid.buy(price=BUY_AT * mkt.grid_import_price, quantity=net_kwh)
        return bid.array

    def dispatch(self, obs, mkt, result):
        return {name: self.plan.get(name, Action(hvac=0.0, battery=0.0)) for name in obs}


AGENT = HalfBatteryAgent
