"""RC-guided preheating for the altered-stock classroom experiment."""
from __future__ import annotations

import math

import numpy as np

from .rc_thermal import FIT_STEPS, features
from .stock_agents import StorageServiceAgent


class RCPreheatAgent(StorageServiceAgent):
    """Shift heat toward cheap hours with a fitted, validated RC surrogate.

    The 24-hour retail shape is published in the exercise. Day-to-day price
    variation is estimated from the current public price only. Future weather,
    occupancy, setpoints and loads are never supplied. Poorly fitted buildings
    use the ordinary thermostat. Battery and market behavior are unchanged.
    """

    def __init__(self, name, buildings, models, price_shape, enable_preheat=True):
        super().__init__(name, buildings)
        self.models = models
        self.price_shape = tuple(float(v) for v in price_shape)
        self.enable_preheat = bool(enable_preheat)
        if len(self.price_shape) != 24 or min(self.price_shape) <= 0:
            raise ValueError("RC preheating needs the public 24-hour price shape")

    def reset(self):
        super().reset()
        self.predicted = {}
        self.bias = {}
        self.thermal_log = []

    def heat_command(self, o, mkt):
        baseline = self.thermostat(o)
        entry = self.models.get(o.name)
        if o.timestep < FIT_STEPS + 2*24 or entry is None or not o.hp_is_heating:
            return baseline
        if entry["validation_mae_c"] > 1.5:
            return baseline
        coef = np.asarray(entry["coefficients"], dtype=float)
        if coef[0] <= 0 or coef[1] <= 0:
            return baseline

        previous = self.predicted.get(o.name)
        if previous is not None and previous[0] == o.timestep-1:
            residual = float(np.clip(o.indoor_temperature-previous[1], -3.0, 3.0))
            self.bias[o.name] = float(np.clip(0.8*self.bias.get(o.name, 0.0)
                                                +0.2*residual, -2.0, 2.0))

        hour = mkt.hour
        now = mkt.grid_import_price
        scale = now / self.price_shape[hour]
        retention = 1.0 - coef[0]
        attractive = any((self.price_shape[(hour+h)%24]*scale * retention**h
                          > now*1.08 and self.price_shape[(hour+h)%24]*scale >= .20)
                         for h in (1,2,3))
        # Keep at least 0.7 C from either edge to absorb prediction error.
        mid = o.comfort_mid
        upper = min(o.comfort_high-0.7, mid+0.7)
        lower = max(o.comfort_low+0.7, mid-0.7)
        preheat = bool(self.enable_preheat and attractive and now < .20)
        target = upper if preheat else (lower if now >= .20 else mid)

        zero_heat = o.indoor_temperature + float(features(o, 0.0) @ coef)
        zero_heat += self.bias.get(o.name, 0.0)
        full_heat_rise = coef[1] * o.hp_max_electric_kw * max(0.1, o.hp_cop)
        if full_heat_rise <= 0.01 or not math.isfinite(full_heat_rise):
            return baseline
        rc_command = float(np.clip((target-zero_heat)/full_heat_rise, 0.0, 1.0))
        # Conservative trust region: the learned one-hour model steers an
        # established PI loop, never replaces it with an unbounded prediction.
        command = float(np.clip(0.65*baseline+0.35*rc_command,
                                max(0.0, baseline-0.10), min(1.0, baseline+0.10)))
        if preheat and o.indoor_temperature < o.comfort_high-1.0:
            # A higher target alone need not actually charge the building's
            # thermal mass. Request a bounded extra pulse when the predicted
            # end-of-hour temperature still has upper-band headroom.
            safe_ceiling = float(np.clip((o.comfort_high-0.7-zero_heat)/full_heat_rise,
                                         0.0, 1.0))
            command = max(command, min(1.0, baseline+0.08, safe_ceiling))
        if now >= .20:
            # Spend previously stored room heat at the price peaks. The RC
            # floor retains a 0.6 C predicted margin above the comfort edge;
            # its error is corrected from the last measured transition.
            safe_floor = float(np.clip((o.comfort_low+0.6-zero_heat)/full_heat_rise,
                                       0.0, 1.0))
            if safe_floor < baseline:
                command = min(command, max(safe_floor, baseline-0.10))
        estimate = o.indoor_temperature + float(features(o, command) @ coef)
        self.predicted[o.name] = (o.timestep, estimate)
        self.thermal_log.append(dict(timestep=o.timestep, building=o.name,
            baseline_hvac=baseline, rc_hvac=command, preheat=preheat,
            target_c=target, predicted_next_c=estimate,
            model_validation_mae_c=entry["validation_mae_c"]))
        return command

