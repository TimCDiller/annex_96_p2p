"""Transparent controllers for the altered-stock teaching market.

All controllers use only their own observations and public market information.
The EvilAgent changes offers, not physics or the market implementation.
"""
import math

from .api import Action, Agent, Thermostat


class StorageServiceAgent(Agent):
    """Buy cheap energy for storage; offer it at morning and evening peaks.

    Thermal control targets the middle of the observed comfort band. Battery
    efficiency is the recipe's published 90% round trip. The online PV forecast
    blends a local trend with the previous day's same-hour measurements.
    """

    behavior = "honest"

    def reset(self):
        self.thermostat = Thermostat()
        self.history = {}
        self.plan = {}
        self.offer_log = []

    def forecast(self, o):
        history = self.history.setdefault(o.name, {})
        now = o.solar_generation_kwh
        last = history.get(o.timestep - 1, now)
        trend = max(0.0, now + max(-now, min(now + 1.0, now - last)))
        previous = history.get(o.timestep - 24)
        next_previous = history.get(o.timestep - 23)
        if previous is not None and next_previous is not None:
            scale = min(1.5, max(0.5, now / max(previous, 0.1)))
            trend = 0.5 * trend + 0.5 * next_previous * scale
        history[o.timestep] = now
        if len(history) > 72:
            del history[min(history)]
        return o.base_load_kwh - trend

    def heat_command(self, o, mkt):
        return self.thermostat(o)

    @staticmethod
    def limits(o):
        eta = math.sqrt(0.90)
        return (min(o.battery_max_power_kw, max(0.0, o.battery_soc_kwh) * eta),
                min(o.battery_max_power_kw,
                    max(0.0, 0.95 * o.battery_capacity_kwh - o.battery_soc_kwh) / eta))

    def bid(self, obs, mkt):
        self.plan = {}
        peak = mkt.grid_import_price >= 0.20
        midday = 9 <= mkt.hour <= 15
        net = 0.0
        for name, o in obs.items():
            hvac = self.heat_command(o, mkt)
            need = self.forecast(o) + o.hp_draw_kwh(hvac)
            discharge, charge = self.limits(o)
            battery = 0.0
            if peak:
                # Pace energy across a peak window instead of emptying a 1C
                # battery at its first hour. Flooding deliberately removes this.
                remaining = max(1, (9 if mkt.hour < 12 else 21) - mkt.hour + 1)
                available = max(0.0, o.battery_soc_kwh - 0.10 * o.battery_capacity_kwh)
                battery = -min(discharge, available * math.sqrt(0.90) / remaining)
                if self.behavior == "flood":
                    battery = -discharge
            elif midday:
                battery = charge
            elif mkt.grid_import_price <= 0.15:
                # Reserve only a quarter of capacity for the morning; leave
                # most of the battery free for the solar producers at midday.
                battery = min(charge, max(0.0, 0.25 * o.battery_capacity_kwh
                                          - o.battery_soc_kwh) / math.sqrt(0.90))
            elif need < 0:
                battery = min(charge, -need)
            self.plan[name] = dict(hvac=hvac, need=need, battery=battery,
                                   discharge=discharge, charge=charge)
            net += need + battery

        bid = mkt.new_bid()
        offered = abs(net)
        price = mkt.grid_import_price if net >= 0 else (0.10 if peak else 0.01)
        if self.behavior == "withhold":
            if net < 0:
                offered *= 0.40
                price = 0.90 * mkt.grid_import_price
            elif midday:
                # A dominant storage owner shades charging demand to push
                # down the midday price; missing purchases is a real cost.
                offered *= 0.60
                price = 0.45 * mkt.grid_import_price
        elif self.behavior == "flood" and net < 0:
            price = 0.01
        if net >= 0:
            bid.buy(price, offered)
        else:
            bid.sell(price, offered)
        self.offer_log.append(dict(timestep=mkt.timestep, planned_net_kwh=net,
            offered_kwh=offered, offer_price=price, behavior=self.behavior))
        return bid.array

    def dispatch(self, obs, mkt, result):
        # Allocate the trade across the portfolio after internal netting.
        need = sum(p["need"] for p in self.plan.values())
        charge = sum(max(0.0, p["battery"]) for p in self.plan.values())
        discharge = sum(max(0.0, -p["battery"]) for p in self.plan.values())
        if charge > 0:
            energy = min(charge, max(0.0, result.quantity - need))
            if mkt.hour <= 5 and mkt.grid_import_price <= 0.15:
                energy = charge       # grid fallback for morning reserve only
            weights = {n:max(0.0, p["battery"]) for n,p in self.plan.items()}
            total = sum(weights.values())
        else:
            energy = -min(discharge, max(0.0, need - result.quantity))
            weights = {n:max(0.0, -p["battery"]) for n,p in self.plan.items()}
            total = sum(weights.values())
        actions = {}
        for name, o in obs.items():
            share = energy * weights[name] / total if total > 0 else 0.0
            actions[name] = Action(self.plan[name]["hvac"],
                share / o.battery_max_power_kw if o.battery_max_power_kw else 0.0)
        return actions


class EvilAgent(StorageServiceAgent):
    """Large owner that withholds supply/demand or floods with stored energy.

    This is a manipulable-market experiment, not an assumption of profitable
    manipulation. Compare against an honest owner of the identical six homes.
    There is no access to others' bids or private observations, and no collusion.
    """

    def __init__(self, name, buildings, behavior="withhold"):
        super().__init__(name, buildings)
        if behavior not in ("withhold", "flood"):
            raise ValueError("behavior must be withhold or flood")
        self.behavior = behavior
