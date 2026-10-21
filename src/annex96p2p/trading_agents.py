"""Four small-household strategies for the daily-trading experiment.

Agents use only their own observations and public price history. Battery
limits include a conservative 90% delivery allowance; actual CityLearn
efficiency and the one-hour observation lag still cause forecast errors.
"""
import numpy as np

from .api import Action, Agent, Thermostat


class NaiveSpotAgent(Agent):
    """Centre thermostat, idle battery, buy demand or sell surplus at any price."""

    aim = 0.0

    def reset(self):
        self.thermostat = Thermostat()
        self.prices = []
        self.actions = {}
        self.need = 0.0

    def battery_energy(self, o, mkt, need):
        return 0.0

    @staticmethod
    def limits(o):
        return (min(o.battery_max_power_kw, 0.90 * o.battery_soc_kwh),
                min(o.battery_max_power_kw,
                    max(0.0, 0.90 * o.battery_capacity_kwh - o.battery_soc_kwh)))

    def bid(self, obs, mkt):
        self.prices.append(mkt.grid_import_price)
        self.actions = {}
        self.need = 0.0
        for name, o in obs.items():
            # For a typical +/-2 K band, -0.75 means 0.5 K above its floor.
            aim = (max(-1.0, -1.0 + 0.5 / max(0.25,
                   (o.comfort_high - o.comfort_low) / 2)) if self.aim < 0 else 0.0)
            hvac = self.thermostat(o, aim=aim)
            need = o.passive_net_kwh + o.hp_draw_kwh(hvac)
            discharge, charge = self.limits(o)
            energy = float(np.clip(self.battery_energy(o, mkt, need),
                                   -discharge, charge))
            self.actions[name] = Action(hvac=hvac, battery=(
                energy / o.battery_max_power_kw if o.battery_max_power_kw else 0.0))
            self.need += need + energy
        bid = mkt.new_bid()
        if self.need > 0:
            bid.buy(mkt.grid_import_price, self.need)
        elif self.need < 0:
            bid.sell(self.sell_price(mkt), -self.need)
        return bid.array

    def sell_price(self, mkt):
        return 0.01

    def dispatch(self, obs, mkt, result):
        return dict(self.actions)


class NaiveClockAgent(NaiveSpotAgent):
    """Unconditional half-power charging at midday, discharge in the evening."""

    def battery_energy(self, o, mkt, need):
        if 10 <= mkt.hour <= 15:
            return 0.5 * o.battery_max_power_kw
        if 18 <= mkt.hour <= 21:
            return -0.5 * o.battery_max_power_kw
        return 0.0


class MinimumTariffAgent(NaiveSpotAgent):
    """Low temperature target; buy below $0.12, discharge above $0.22/kWh.

    Sell stored surplus only above a $0.15 reservation price. If that offer
    does not clear, retain the excess instead of dumping it to the grid.
    Fixed thresholds are a deliberately simple tariff-arbitrage policy.
    """

    aim = -0.75

    def thresholds(self):
        return 0.12, 0.22

    def battery_energy(self, o, mkt, need):
        low, high = self.thresholds()
        discharge, charge = self.limits(o)
        if mkt.grid_import_price <= low:
            return charge
        if mkt.grid_import_price >= high:
            return -discharge
        return min(charge, max(0.0, -need))

    def sell_price(self, mkt):
        low, high = self.thresholds()
        return max(0.01, low / 0.81) if mkt.grid_import_price >= high else 0.01

    def dispatch(self, obs, mkt, result):
        actions = dict(self.actions)
        # Undo planned battery export which did not sell. This preserves
        # energy for a later opportunity rather than manufacturing volume.
        unwanted = max(0.0, result.quantity - self.need)
        for name, o in obs.items():
            a = actions[name]
            undo = min(unwanted, max(0.0, -a.battery * o.battery_max_power_kw))
            if o.battery_max_power_kw:
                actions[name] = Action(a.hvac, a.battery + undo / o.battery_max_power_kw)
            unwanted -= undo
        return actions


class MinimumAdaptiveAgent(MinimumTariffAgent):
    """Learn cheap/dear thresholds from a rolling week of observed prices.

    Outside the cheap window, also buy optional battery charging at a price
    below the observed evening value, then charge only the amount that clears.
    Demand and supply offers are never submitted together by this agent.
    """

    def thresholds(self):
        if len(self.prices) < 24:
            return 0.12, 0.22
        history = self.prices[-168:]
        return float(np.percentile(history, 30)), float(np.percentile(history, 75))

    def bid(self, obs, mkt):
        bid = super().bid(obs, mkt)
        low, high = self.thresholds()
        self.optional_charge = {}
        if self.need >= 0 and low < mkt.grid_import_price < high:
            for name, o in obs.items():
                planned = self.actions[name].battery * o.battery_max_power_kw
                self.optional_charge[name] = max(0.0, self.limits(o)[1] - max(0.0, planned))
            reserve = min(mkt.grid_import_price - 0.02, high * 0.65)
            if reserve > 0:
                bid[mkt.level(reserve)] += sum(self.optional_charge.values())
        return bid

    def dispatch(self, obs, mkt, result):
        actions = super().dispatch(obs, mkt, result)
        extra = min(max(0.0, result.quantity - max(0.0, self.need)),
                    sum(self.optional_charge.values()))
        total = sum(self.optional_charge.values())
        for name, room in self.optional_charge.items():
            o, a = obs[name], actions[name]
            if total and o.battery_max_power_kw:
                actions[name] = Action(a.hvac, a.battery + extra * room / total / o.battery_max_power_kw)
        return actions
