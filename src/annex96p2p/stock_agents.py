"""Transparent controllers for the altered-stock teaching market.

All controllers use only their own observations and public market information.
The EvilAgent changes offers, not physics or the market implementation.
"""
import math

from .api import Action, Agent, Thermostat


class StorageServiceAgent(Agent):
    """Buy cheap energy for storage; offer it at morning and evening peaks.

    Before each peak the battery is filled from the grid in the cheapest hours
    if peers did not supply enough: overnight to cover yesterday's morning
    shortfall, and at 12-15 to 95% for the evening (see `topup`).

    Dispatch sets a grid target per house (Action.net_target_kwh) rather than a
    battery rate, so the battery absorbs forecast error in real time: unsold
    surplus is stored instead of curtailed, and an unforecast load is served
    from storage before the grid. Two consequences for planning: a house with
    its own surplus never discharges, even at peak, and once the morning sun
    is up the houses stop exporting stored energy and buy peers' solar
    instead, covering their own load from the battery. Thermal control targets the middle of the observed comfort band. Battery
    efficiency is the recipe's published 90% round trip. The online PV forecast
    blends a local trend with the previous day's same-hour measurements.
    """

    behavior = "honest"

    def reset(self):
        self.thermostat = Thermostat()
        self.history = {}
        self.plan = {}
        self.offer_log = []
        self.peak_log = {}

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

    def peak_need(self, o, morning, need, peak):
        """Yesterday's own shortfall over this house's morning or evening peak.

        Accumulates today's planned shortfall during peak hours and returns the
        last completed day's total, so the agent learns from its own history
        only. None until one full day has been seen.
        """
        log = self.peak_log.setdefault(o.name, {})
        key = (o.day, morning)
        if peak:
            log[key] = log.get(key, 0.0) + max(0.0, need)
        return log.get((o.day - 1, morning))

    def topup(self, o, mkt, need, peak):
        """Grid charge this hour to arrive full at the next peak, or 0.

        Morning: top up overnight (00-05) to cover yesterday's morning
        shortfall, never less than a quarter of capacity -- not to full, which
        would leave no room for peers' midday solar.
        Evening: after the morning peak, charge in the cheap hours 12-15 to 95%
        of capacity. Peers' solar bought at midday counts first; the grid
        covers only what is still missing. Energy beyond the house's own
        evening need is sold to peers at the peak, when the market is short.
        The shortfall still to charge is spread over the hours left before the
        peak, so a fast battery does not fill at noon and crowd out solar.
        """
        eta = math.sqrt(0.90)
        if mkt.hour <= 5 and mkt.grid_import_price <= 0.15:
            morning = self.peak_need(o, True, need, False) or 0.0
            target = max(0.25 * o.battery_capacity_kwh,
                         0.10 * o.battery_capacity_kwh + morning / eta)
            hours_left = 6 - mkt.hour
        elif 12 <= mkt.hour <= 15 and not peak:
            target = 0.95 * o.battery_capacity_kwh
            hours_left = 16 - mkt.hour
        else:
            return 0.0
        target = min(target, 0.95 * o.battery_capacity_kwh)
        missing = max(0.0, target - o.battery_soc_kwh) / eta
        return missing / hours_left

    def plan_hour(self, obs, mkt):
        """Decide each house's heat pump and battery plan; return the planned net.

        Fills self.plan, which dispatch() carries out. Kept apart from the
        pricing in bid() so a subclass can change prices without re-planning.
        """
        self.plan = {}
        peak = mkt.grid_import_price >= 0.20
        midday = 9 <= mkt.hour <= 15
        net = 0.0
        for name, o in obs.items():
            hvac = self.heat_command(o, mkt)
            need = self.forecast(o) + o.hp_draw_kwh(hvac)
            discharge, charge = self.limits(o)
            self.peak_need(o, mkt.hour < 12, need, peak)    # log today's peak shortfall
            firm = min(charge, self.topup(o, mkt, need, peak))
            battery = 0.0
            floor = None            # least grid draw; None = need + planned battery
            sun_up = mkt.hour < 12 and o.solar_generation_kwh > 0.01
            if peak and need < 0 and self.behavior != "flood":
                # Own surplus: sell it; what does not sell is stored, not
                # replaced by discharging the battery on top of it.
                battery = 0.0
            elif peak and sun_up and self.behavior != "flood":
                # Morning peak with the sun up: peers' solar is on offer. Buy it
                # for storage rather than competing with it, and cover this
                # house's own load from the battery meanwhile.
                battery = charge
                floor = need - min(discharge, max(0.0, need))
            elif peak:
                # Pace energy across a peak window instead of emptying a 1C
                # battery at its first hour. Flooding deliberately removes this.
                remaining = max(1, (9 if mkt.hour < 12 else 21) - mkt.hour + 1)
                available = max(0.0, o.battery_soc_kwh - 0.10 * o.battery_capacity_kwh)
                battery = -min(discharge, available * math.sqrt(0.90) / remaining)
                if self.behavior == "flood":
                    battery = -discharge
            elif midday:
                battery = charge
            elif firm > 0:
                # Overnight morning reserve; leaves most of the battery free
                # for the solar producers at midday.
                battery = firm
            elif need < 0:
                battery = min(charge, -need)
            firm = min(firm, max(0.0, battery))
            if floor is None:
                floor = need + (battery if battery < 0 else firm)
            self.plan[name] = dict(hvac=hvac, need=need, battery=battery, firm=firm,
                                   floor=floor, top=need + charge,
                                   discharge=discharge, charge=charge)
            net += need + battery
        return net

    def bid(self, obs, mkt):
        net = self.plan_hour(obs, mkt)
        peak = mkt.grid_import_price >= 0.20
        midday = 9 <= mkt.hour <= 15
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
        """Turn the portfolio's cleared quantity into a grid target per house.

        Each house draws at least its `floor` (its load less any planned
        discharge, plus any planned grid top-up). Energy bought beyond the
        floors is spread over the houses that planned to charge; a sale is
        delivered by drawing less. The battery then meets the target in real
        time, absorbing whatever the forecast got wrong.
        """
        floors = {n: p["floor"] for n, p in self.plan.items()}
        total = max(result.quantity, sum(floors.values()))
        extra = total - sum(floors.values())
        room = {n: max(0.0, p["top"] - p["floor"]) for n, p in self.plan.items()}
        space = sum(room.values())
        actions = {}
        for name in obs:
            p = self.plan[name]
            share = (extra * room[name] / space if space > 0
                     else extra / max(1, len(self.plan)))
            actions[name] = Action(hvac=p["hvac"], net_target_kwh=floors[name] + share)
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
