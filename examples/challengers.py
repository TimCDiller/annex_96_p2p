"""Challengers to the default agent, one idea each, for measuring how beatable it is.

Each class changes one thing about StorageServiceAgent and keeps the rest, so
a run against the default on the same houses measures that idea alone. Used by
examples/challenge_default.py; see that script for the experiment.

  CappedBuyer        pays at most STORE_CAP for energy it wants for storage
  RankedDischarge    spends stored energy in the dearest peak hours first
  Challenger         both of the above
  SolarPreheat       stores heat in the building: warms towards the top of
                     the comfort band while it has its own PV surplus, and
                     coasts towards the bottom in the evening peak
  LatePreheat        the same, but preheats in the last hours before the peak
                     (13-16), from PV or cheap grid, so less heat leaks away
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import yaml

from annex96p2p.stock_agents import StorageServiceAgent

ROOT = Path(__file__).resolve().parents[1]
RECIPE = yaml.safe_load((ROOT / "scenarios/altered_stock.yaml").read_text())
SHAPE = [float(p) for p in RECIPE["duck_prices"]]     # the published 24-hour shape
PEAK = 0.20
STORE_CAP = 0.06    # $/kWh: about what the midday grid top-up costs after losses


class CappedBuyer(StorageServiceAgent):
    """Bids the full grid price only for what it must draw anyway."""

    def bid(self, obs, mkt):
        net = self.plan_hour(obs, mkt)
        grid = mkt.grid_import_price
        bid = mkt.new_bid()
        if net >= 0:
            must = min(net, max(0.0, sum(p["floor"] for p in self.plan.values())))
            bid.buy(grid, must)
            bid.buy(min(STORE_CAP, grid), net - must)
        else:
            bid.sell(0.10 if grid >= PEAK else 0.01, -net)
        return bid.array


class RankedDischarge(StorageServiceAgent):
    """Allocates stored energy to the remaining peak hours, dearest first."""

    def plan_hour(self, obs, mkt):
        net = super().plan_hour(obs, mkt)
        grid = mkt.grid_import_price
        if grid < PEAK or self.behavior == "flood":
            return net
        hour = mkt.hour
        end = 9 if hour < 12 else 21
        scale = grid / SHAPE[hour]
        window = [h for h in range(hour, end + 1)]
        for name, p in self.plan.items():
            if p["battery"] >= 0:            # not a pacing hour for this house
                continue
            o = obs[name]
            available = max(0.0, o.battery_soc_kwh - 0.10 * o.battery_capacity_kwh) \
                * math.sqrt(0.90)
            # Still empty the battery over the window, as the default does --
            # what the house does not need is sold to peers -- but weight each
            # remaining hour by its price squared, so the dear hours get more.
            # (Giving each hour only the house's own need, dearest first, was
            # tried and lost: it never sold to peers and left energy unused.)
            weight = {h: (SHAPE[h % 24] * scale) ** 2 for h in window}
            share = available * weight[hour] / sum(weight.values())
            battery = -min(p["discharge"], share)
            net += battery - p["battery"]
            p["battery"] = battery
            p["floor"] = p["need"] + battery
        return net


class Challenger(CappedBuyer, RankedDischarge):
    """Capped storage buying and price-ranked discharge together."""


class SolarPreheat(StorageServiceAgent):
    """Warm the building with own PV surplus; coast through the evening peak."""

    PREHEAT_AIM = 0.8       # 0 = band centre, 1 = top edge
    COAST_AIM = -0.5

    def preheating(self, o, mkt):
        # Own surplus, as last observed: PV above the household load.
        return 8 <= mkt.hour <= 16 and o.passive_net_kwh < -1.0

    def heat_command(self, o, mkt):
        if self.preheating(o, mkt):
            aim = self.PREHEAT_AIM
        elif mkt.grid_import_price >= PEAK and mkt.hour >= 16:
            aim = self.COAST_AIM
        else:
            aim = 0.0
        return self.thermostat(o, aim=aim)


class LatePreheat(SolarPreheat):
    """Preheat in the last cheap hours before the evening peak, PV or grid."""

    def preheating(self, o, mkt):
        return 13 <= mkt.hour <= 15


# --------------------------------------------------------------------------
# Smoother heating. The thermostat used to be a bare PI loop acting once an
# hour on a heat pump that moves these houses about 1 K per kWh, and it
# settled into a two-hour limit cycle. These variants reproduce the study that
# led to Thermostat(smoothing=0.5), the default since: each starts from the
# BARE loop (smoothing=0) and changes only the heat-pump command.
# --------------------------------------------------------------------------
import numpy as np

from annex96p2p.api import Thermostat
from annex96p2p.rc_thermal import features, load_models

MODELS = load_models(ROOT / "scenarios/rc_models/balanced.json",
                     ROOT / "generated/altered_stock/balanced/schema.json")
RC_FROM = 7 * 24          # same start as the RC agent: after the published fit window


class GentlePI(StorageServiceAgent):
    """The default PI thermostat with half its gains."""

    def reset(self):
        super().reset()
        self.thermostat = Thermostat(gain=0.055, integral=0.015, smoothing=0.0)


class FilteredPI(StorageServiceAgent):
    """The default PI command, blended half-and-half with last hour's command."""

    def reset(self):
        super().reset()
        self.thermostat = Thermostat(smoothing=0.0)
        self.last = {}

    def heat_command(self, o, mkt):
        raw = self.thermostat(o)
        u = 0.5 * self.last.get(o.name, raw) + 0.5 * raw
        self.last[o.name] = u
        return u


class RCFeedforward(StorageServiceAgent):
    """Model-based heating: ask the fitted RC model what closes half the gap.

    Each hour: predict next hour's temperature with the heat pump off, then
    choose the command that lands halfway between now and the target. A slow
    bias term learns from the model's own errors. Houses whose model fits
    badly, and the first week, use the default thermostat.
    """

    CLOSE = 0.5               # share of the gap to the target closed per hour

    def reset(self):
        super().reset()
        self.predicted, self.bias = {}, {}

    def aim(self, o, mkt):
        return 0.0

    def heat_command(self, o, mkt):
        aim = self.aim(o, mkt)
        baseline = self.thermostat(o, aim=aim)
        entry = MODELS.get(o.name)
        if (o.timestep < RC_FROM or entry is None or not o.hp_is_heating
                or entry["validation_mae_c"] > 1.5):
            return baseline
        coef = np.asarray(entry["coefficients"], dtype=float)
        rise = coef[1] * o.hp_max_electric_kw * max(0.1, o.hp_cop)   # K per hour at full
        if coef[0] <= 0 or rise <= 0.01:
            return baseline
        previous = self.predicted.get(o.name)
        if previous is not None and previous[0] == o.timestep - 1:
            error = float(np.clip(o.indoor_temperature - previous[1], -3, 3))
            self.bias[o.name] = float(np.clip(0.8 * self.bias.get(o.name, 0.0)
                                              + 0.2 * error, -2, 2))
        half = max(0.25, 0.5 * (o.comfort_high - o.comfort_low))
        target = o.comfort_mid + aim * half
        drift = o.indoor_temperature + float(features(o, 0.0) @ coef) \
            + self.bias.get(o.name, 0.0)
        want = o.indoor_temperature + self.CLOSE * (target - o.indoor_temperature)
        u = float(np.clip((want - drift) / rise, 0.0, 1.0))
        self.predicted[o.name] = (o.timestep, drift + u * rise)
        return u


class RCLatePreheat(RCFeedforward):
    """RC heating that preheats 13-15 and coasts through the evening peak."""

    def aim(self, o, mkt):
        if 13 <= mkt.hour <= 15:
            return LatePreheat.PREHEAT_AIM
        if mkt.grid_import_price >= PEAK and mkt.hour >= 16:
            return LatePreheat.COAST_AIM
        return 0.0


class FilteredSolarPreheat(FilteredPI):
    """Filtered heating that preheats on own PV surplus and coasts at the peak."""

    def heat_command(self, o, mkt):
        if SolarPreheat.preheating(self, o, mkt):
            aim = SolarPreheat.PREHEAT_AIM
        elif mkt.grid_import_price >= PEAK and mkt.hour >= 16:
            aim = SolarPreheat.COAST_AIM
        else:
            aim = 0.0
        raw = self.thermostat(o, aim=aim)
        u = 0.5 * self.last.get(o.name, raw) + 0.5 * raw
        self.last[o.name] = u
        return u


class FilteredLatePreheat(FilteredSolarPreheat):
    """Filtered heating that preheats 13-15, PV or grid, and coasts at the peak."""

    def heat_command(self, o, mkt):
        if 13 <= mkt.hour <= 15:
            aim = SolarPreheat.PREHEAT_AIM
        elif mkt.grid_import_price >= PEAK and mkt.hour >= 16:
            aim = SolarPreheat.COAST_AIM
        else:
            aim = 0.0
        raw = self.thermostat(o, aim=aim)
        u = 0.5 * self.last.get(o.name, raw) + 0.5 * raw
        self.last[o.name] = u
        return u


class FilteredCoast(FilteredSolarPreheat):
    """Filtered heating that only coasts at the peak -- no preheating."""

    def heat_command(self, o, mkt):
        aim = SolarPreheat.COAST_AIM if (mkt.grid_import_price >= PEAK and mkt.hour >= 16) else 0.0
        raw = self.thermostat(o, aim=aim)
        u = 0.5 * self.last.get(o.name, raw) + 0.5 * raw
        self.last[o.name] = u
        return u
