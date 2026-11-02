"""Marginal value of stored energy -- the strategy behind two advanced agents.

Used by marginal_value_storage.py and marginal_value_generation.py, which
differ only in the house they run. Not a submission itself.

Every hour, for each house with a battery:

  1. Assume the market clears as it did in the past: in each hour of the day
     at the same share of the grid price, and as often (PriceForecaster).
     A kWh bought then costs the clearing price if the hour clears and the
     grid price if not; a kWh offered earns the clearing price, or nothing.
  2. Optimise the next HORIZON hours from the current state of charge, with
     the battery made to end the horizon TERMINAL full: the least cost of
     the house's forecast load, PV and battery (BatteryDP).
  3. Compare with the same optimisation from a marginally lower and a
     marginally higher starting charge (+-DELTA of capacity). The difference
     in total cost per kWh is the marginal value of stored energy now --
     what one more kWh in the battery is worth to the rest of the horizon.
     (The optimisation is solved backwards in time, which yields the cost
     from every starting charge at once, so the three runs are one solve.)
  4. Buy if the market offers energy cheaper than that value, sell if it
     pays more. A kWh bought puts only 0.95 kWh into the battery and a kWh
     sold takes 1.05 out, so the buying price is the value x 0.95 and the
     selling price the value / 0.95: the round-trip loss is a dead band.

Charging a battery lowers the value of the next kWh, so one gradient is not
enough to say how MUCH to buy. The cost is therefore also read at STEPS
points further up and down, a full hour of battery power each way,
and every slice of that hour is bid at its own marginal value: a stepped
demand curve, each step one finite difference. The house's own load this
hour is bought at up to the grid price and its own surplus sold from $0.00,
as unsold surplus is curtailed; the battery steps sit on top of that.
"""
import numpy as np

from _advanced_common import ETA, BatteryDP, CurveAgent


class MarginalValueAgent(CurveAgent):

    strategy = "marginal value of storage"
    HORIZON = 30            # hours optimised ahead
    TERMINAL = 0.5          # state of charge the horizon must end at, share of capacity
    DELTA = 0.01            # the marginal change of starting charge, share of capacity
    STEPS = 3               # bid steps each way within one hour of battery power

    def reset(self):
        super().reset()
        self.solvers = {}

    def values(self, o, points):
        """Least horizon cost from each starting charge in `points`."""
        dp = self.solvers.get(o.name)
        if dp is None:
            dp = self.solvers[o.name] = BatteryDP(o.battery_capacity_kwh,
                                                  o.battery_max_power_kw)
        ahead = np.arange(1, self.HORIZON + 1)
        net = self.profiles[o.name].net(ahead)
        buy, sell = self.prices.buy_price(ahead), self.prices.sell_price(ahead)
        terminal = self.TERMINAL * o.battery_capacity_kwh
        return dp.costs(points, net, buy, sell, terminal)

    def curve(self, o, mkt, hvac, need):
        prices = mkt.price_grid
        demand = np.full(len(prices), need)
        if not o.has_battery or o.battery_max_power_kw <= 0:
            return demand
        cap, soc, power = o.battery_capacity_kwh, o.battery_soc_kwh, o.battery_max_power_kw
        delta = self.DELTA * cap
        up = power * ETA / self.STEPS          # stored kWh per charging step
        down = power / ETA / self.STEPS        # stored kWh per discharging step
        points = [soc - delta, soc, soc + delta]
        points += [soc + k * up for k in range(1, self.STEPS + 1)]
        points += [soc - k * down for k in range(1, self.STEPS + 1)]
        points = np.unique(np.round(np.clip(points, 0.0, cap), 6))
        cost = self.values(o, points)
        ok = np.isfinite(cost)
        points, cost = points[ok], cost[ok]

        # Marginal value of stored energy in each slice between two points.
        worth = (cost[:-1] - cost[1:]) / np.diff(points)
        lower, upper = points[:-1], points[1:]
        buy_below = sell_above = None
        for a, b, v in zip(lower, upper, worth):
            if a >= soc - 1e-9:                # charging slice
                limit = v * ETA
                demand[prices < limit - 1e-9] += (b - a) / ETA
                buy_below = limit if buy_below is None else buy_below
            elif b <= soc + 1e-9:              # discharging slice
                limit = v / ETA
                demand[prices > limit + 1e-9] -= (b - a) * ETA
                sell_above = limit             # the last one is nearest the current charge

        i = int(np.searchsorted(points, soc))
        here = lambda k: k < len(points) and abs(points[k] - soc) < 1e-6
        gradient = None
        if here(i) and 0 < i < len(points) - 1:
            gradient = (cost[i - 1] - cost[i + 1]) / (points[i + 1] - points[i - 1])
        self.offer_log.append(dict(
            timestep=mkt.timestep, building=o.name, strategy=self.strategy,
            forecast_now=float(self.prices.clearing(0)),
            forecast_ahead=float(self.prices.clearing(1)),
            marginal_value=gradient, buy_below=buy_below, sell_above=sell_above,
            soc_kwh=soc, need_kwh=need, horizon_cost=float(cost[i]) if here(i) else None,
            planned_net_kwh=float(demand[0])))
        return demand
