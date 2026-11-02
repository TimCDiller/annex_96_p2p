"""Building blocks shared by the advanced agents in this folder.

Not a submission itself: the leading underscore makes the runner skip it.

  PriceForecaster   the internal market's clearing price, learned per hour of
                    day as a share of the grid price, from the one public
                    signal there is: last hour's clearing price
  LoadProfile       a house's own load, PV and heat-pump draw by hour of day,
                    for looking further ahead than the current hour
  BatteryDP         the least cost of the next T hours as a function of the
                    starting state of charge -- dynamic programming
  CurveAgent        bids a demand curve -- net kWh wanted at every legal price
                    -- and dispatches whatever the market and the grid make of
                    it, so a subclass only has to say what it would do at
                    each price

Everything here uses only the agent's own observations and public market
information, like any student submission.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import yaml

from annex96p2p import Action
from annex96p2p.stock_agents import StorageServiceAgent

ROOT = Path(__file__).resolve().parents[2]
RECIPE = yaml.safe_load((ROOT / "scenarios/altered_stock.yaml").read_text())
#: The published 24-hour price shape. Each day's grid price is this shape
#: times one daily factor, so the current price gives today's factor.
SHAPE = np.array([float(p) for p in RECIPE["duck_prices"]])
ETA = math.sqrt(float(RECIPE["round_trip_efficiency"]))     # one way


# --------------------------------------------------------------------------
# Forecasts
# --------------------------------------------------------------------------
class PriceForecaster:
    """Forecast the internal clearing price as a share of the grid price.

    "Clearing will happen as it did in the past": for each hour of the day it
    keeps an exponentially weighted average of clearing price / grid price,
    over the days that hour cleared, and separately how often it cleared at
    all. The grid price ahead is the published shape times today's factor.

    Call `observe(mkt)` exactly once per hour, before forecasting.
    """

    def __init__(self, prior_share=0.5, alpha=0.3):
        self.alpha = alpha
        self.share = np.full(24, float(prior_share))   # clearing / grid, when it cleared
        self.cleared = np.ones(24)                     # how often each hour cleared
        self.seen = np.zeros(24, dtype=int)
        self.seen_cleared = np.zeros(24, dtype=int)
        self.last = None                               # (timestep, hour, grid price)
        self.hour, self.factor = 0, 1.0

    def observe(self, mkt):
        if self.last is not None and self.last[0] == mkt.timestep - 1:
            _, h, grid = self.last
            ok = mkt.last_clearing_price is not None and mkt.last_clearing_volume > 1e-9
            a = 1.0 if self.seen[h] == 0 else self.alpha
            self.cleared[h] += a * (float(ok) - self.cleared[h])
            self.seen[h] += 1
            if ok:
                a = 1.0 if self.seen_cleared[h] == 0 else self.alpha
                self.share[h] += a * (mkt.last_clearing_price / grid - self.share[h])
                self.seen_cleared[h] += 1
        self.last = (mkt.timestep, mkt.hour, mkt.grid_import_price)
        self.hour = mkt.hour
        self.factor = mkt.grid_import_price / SHAPE[mkt.hour]

    def grid(self, ahead):
        """Grid price `ahead` hours from now (0 = this hour); array in, array out."""
        return SHAPE[(self.hour + np.asarray(ahead)) % 24] * self.factor

    def clearing(self, ahead):
        """Clearing price `ahead` hours from now, assuming the hour clears."""
        return self.share[(self.hour + np.asarray(ahead)) % 24] * self.grid(ahead)

    def buy_price(self, ahead):
        """Expected cost of a kWh bought: the market if it clears, else the grid."""
        q = self.cleared[(self.hour + np.asarray(ahead)) % 24]
        return q * self.clearing(ahead) + (1 - q) * self.grid(ahead)

    def sell_price(self, ahead):
        """Expected revenue of a kWh offered: nothing in hours that do not clear."""
        q = self.cleared[(self.hour + np.asarray(ahead)) % 24]
        return q * self.clearing(ahead)


class LoadProfile:
    """A house's own base load, PV and heat-pump draw, by hour of day.

    The forecast for an hour ahead is the last value seen at that hour of day,
    or the current value before a full day has been seen. PV is read one hour
    later than the hour it is for: the observation precedes the physical step
    by an hour (see the README), the same correction the default agent makes.
    """

    def __init__(self):
        self.load = {}          # hour of day -> kWh
        self.pv = {}
        self.hp = {}
        self.now = (0, 0.0, 0.0, 0.0)

    def record(self, o, hp_kwh):
        h = o.hour
        self.load[h] = o.base_load_kwh
        self.pv[h] = o.solar_generation_kwh
        self.hp[h] = hp_kwh
        self.now = (h, o.base_load_kwh, o.solar_generation_kwh, hp_kwh)

    def net(self, ahead):
        """Forecast net kWh (load + heat pump - PV) for each hour in `ahead`."""
        h0, load, pv, hp = self.now
        out = []
        for k in ahead:
            h = (h0 + k) % 24
            out.append(self.load.get(h, load) + self.hp.get(h, hp)
                       - self.pv.get((h + 1) % 24, pv))
        return np.array(out)


# --------------------------------------------------------------------------
# The optimisation
# --------------------------------------------------------------------------
class BatteryDP:
    """Least cost of the next hours for one house, from any starting charge.

    The battery's charge is a grid of LEVELS points from empty to full. Each
    hour it may move from one point to any other within its power: the kWh
    at the meter are the stored change / 0.95 when charging and x 0.95 when
    discharging. The house's net that hour is its forecast net plus that; a
    positive net is bought at the buy price, a negative one sold at the sell
    price. The horizon must end at the `terminal` charge.

    Solved backwards by dynamic programming, which gives the least cost from
    EVERY starting charge in one pass -- so the cost of starting a little
    lower or higher is read off the same solution, interpolated between grid
    points, rather than solved for again. (A linear programme does the same
    job and gives the same gradient; this needs nothing but numpy.)
    """

    LEVELS = 81
    UNREACHABLE = 1e6       # $ -- a finite stand-in for infinity keeps interpolation safe

    def __init__(self, capacity, power, eta=ETA):
        self.levels = np.linspace(0.0, capacity, self.LEVELS)
        stored = self.levels[None, :] - self.levels[:, None]        # from i (row) to j
        self.meter = np.where(stored > 0, stored / eta, stored * eta)
        self.blocked = np.abs(self.meter) > power + 1e-9

    def costs(self, starts, net, buy, sell, terminal):
        """Least total cost over len(net) hours from each charge in `starts`."""
        end = int(np.argmin(np.abs(self.levels - terminal)))
        value = np.full(self.LEVELS, self.UNREACHABLE)
        value[end] = 0.0
        for t in range(len(net) - 1, -1, -1):
            flow = net[t] + self.meter
            step = np.where(flow > 0, buy[t] * flow, sell[t] * flow)
            step[self.blocked] = self.UNREACHABLE
            value = np.min(step + value[None, :], axis=1)
        return np.interp(starts, self.levels, value)


# --------------------------------------------------------------------------
# Bidding a demand curve
# --------------------------------------------------------------------------
def curve_to_bid(mkt, demand):
    """Turn a demand curve into the market's bid vector.

    `demand[j]` is the net kWh wanted if the hour clears at price_grid[j]:
    positive buys, negative sells, never rising with the price. The market
    counts a buy at its price and every cheaper one and an ask at its price
    and every dearer one, so the curve is split into those increments.
    """
    demand = np.minimum.accumulate(np.asarray(demand, dtype=float))
    buy = np.maximum(demand, 0.0)
    sell = np.maximum(-demand, 0.0)
    bid = np.zeros(mkt.n_levels)
    bid += buy - np.r_[buy[1:], 0.0]
    bid -= sell - np.r_[0.0, sell[:-1]]
    bid[np.abs(bid) < 1e-9] = 0.0
    return bid


class CurveAgent(StorageServiceAgent):
    """Base for agents that bid a demand curve per house.

    A subclass implements `curve(o, mkt, hvac, need)` and returns, for one
    house, its net kWh wanted at every price level. The curves of all houses
    are summed into one bid. Dispatch then picks the net that the cleared
    quantity and the grid make best: buy more from the grid only if the curve
    wants more even at the grid price, use less than was bought only if the
    curve would at a price of zero. That net is shared out between the houses
    at the price where their summed curves give it, and each house's battery
    meets its share in real time (Action.net_target_kwh).

    Reuses the default agent's thermostat and current-hour PV forecast.
    """

    #: Shown in the hourly page's bid panel as dashed lines, if the subclass
    #: logs them: the price below which it buys for storage, above which it
    #: sells from storage, and its forecast of this hour's clearing price.
    strategy = "curve"

    def reset(self):
        super().reset()
        self.prices = PriceForecaster()
        self.profiles = {}
        self.curves = {}
        self.hvac = {}

    def curve(self, o, mkt, hvac, need):
        raise NotImplementedError

    def bid(self, obs, mkt):
        self.prices.observe(mkt)
        self.curves, self.hvac = {}, {}
        for name, o in obs.items():
            hvac = self.heat_command(o, mkt)
            hp = o.hp_draw_kwh(hvac)
            self.profiles.setdefault(name, LoadProfile()).record(o, hp)
            need = self.forecast(o) + hp
            self.hvac[name] = hvac
            self.curves[name] = np.minimum.accumulate(
                np.asarray(self.curve(o, mkt, hvac, need), dtype=float))
        total = sum(self.curves.values())
        return curve_to_bid(mkt, total)

    def dispatch(self, obs, mkt, result):
        total = sum(self.curves.values())
        target = min(max(result.quantity, total[-1]), total[0])
        # The price level at which the summed curve gives `target`.
        j = int(np.searchsorted(-total, -target, side="right")) - 1
        j = min(max(j, 0), len(total) - 2) if len(total) > 1 else 0
        span = total[j] - total[j + 1] if len(total) > 1 else 0.0
        w = (total[j] - target) / span if span > 1e-9 else 0.0
        actions = {}
        for name in obs:
            c = self.curves[name]
            share = c[j] - w * (c[j] - c[j + 1]) if len(c) > 1 else c[0]
            actions[name] = Action(hvac=self.hvac[name], net_target_kwh=float(share))
        return actions


def battery_room(o, low=0.0, high=None):
    """kWh at the meter the battery can take and give this hour, between
    `low` and `high` kWh of stored energy."""
    high = o.battery_capacity_kwh if high is None else high
    charge = min(o.battery_max_power_kw, max(0.0, high - o.battery_soc_kwh) / ETA)
    discharge = min(o.battery_max_power_kw, max(0.0, o.battery_soc_kwh - low) * ETA)
    return charge, discharge
