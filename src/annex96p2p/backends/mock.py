"""A small self-contained neighbourhood simulator.

This exists so the repo runs the moment it is cloned: pure numpy, no
CityLearn, no PyTorch, no dataset to download, a full week of 12 buildings
in well under a second. It is a teaching model, not a research model.

Each building is a first-order thermal network (one resistance, one
capacitance) plus a heat pump, an optional battery and optional rooftop PV:

    C dT/dt = (T_out - T)/R + solar_gain + internal_gain + q_heatpump

The neighbourhood is deliberately heterogeneous, in four archetypes:

    solar-rich     large PV array, no battery      -> structural seller
    storage-rich   no PV, large battery            -> arbitrageur
    thermal-mass   no PV, no battery, heavy fabric -> flexible load only
    balanced       moderate PV and battery         -> both sides

If every portfolio looked the same, everyone would want to buy and sell at
the same hours and the market would clear almost nothing. The asymmetry is
what creates gains from trade.

Everything here is deterministic. Two students running the same agent get
identical numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from ..api import Action, BuildingObs
from .base import Backend, StepOutcome

HOURS_PER_DAY = 24

#: Non-shiftable load shape, fraction of the peak, by hour of day.
LOAD_SHAPE = np.array([
    0.35, 0.30, 0.28, 0.28, 0.30, 0.40, 0.60, 0.75,
    0.70, 0.60, 0.55, 0.55, 0.55, 0.55, 0.60, 0.70,
    0.85, 1.00, 1.00, 0.95, 0.85, 0.70, 0.55, 0.45,
])


@dataclass
class BuildingSpec:
    """Static properties of one simulated building."""

    name: str
    archetype: str
    load_peak_kw: float
    pv_kwp: float
    battery_capacity_kwh: float
    battery_power_kw: float
    thermal_capacitance: float      # kWh/K
    thermal_resistance: float       # K/kW
    hp_thermal_kw: float
    hp_cop: float
    solar_gain_kw: float
    internal_gain_kw: float
    comfort_low: float
    comfort_high: float

    @property
    def hp_max_electric_kw(self) -> float:
        return self.hp_thermal_kw / self.hp_cop

    @property
    def comfort_mid(self) -> float:
        return 0.5 * (self.comfort_low + self.comfort_high)

    @property
    def phase(self) -> int:
        """Deterministic per-building phase offset.

        Python randomises str hashes between processes, so anything derived
        from hash() would make runs irreproducible. Derive it from the name.
        """
        return sum(ord(c) for c in self.name) % 7


# Heat pumps are deliberately sized so that riding out the hottest hours
# with the compressor off is possible but not free. If they were oversized,
# comfort would never bind and the exercise would collapse to pure battery
# arbitrage; if undersized, everyone would pay the comfort penalty no matter
# how well they controlled and skill would stop mattering.
ARCHETYPES = [
    # archetype,      load, pv,   bat_kwh, bat_kw, C,   hp_kw
    ("solar-rich",    1.4,  6.0,  0.0,     0.0,    4.5, 3.6),
    ("storage-rich",  1.6,  0.0,  13.5,    5.0,    4.5, 3.6),
    ("thermal-mass",  1.1,  0.0,  0.0,     0.0,    9.0, 3.6),
    ("balanced",      1.3,  3.0,  6.5,     3.0,    6.0, 3.6),
]


def default_neighbourhood(n_per_archetype: int = 3) -> List[BuildingSpec]:
    """Build a deterministic, heterogeneous set of buildings."""
    specs: List[BuildingSpec] = []
    index = 0
    for rep in range(n_per_archetype):
        for archetype, load, pv, bat_kwh, bat_kw, cap, hp in ARCHETYPES:
            index += 1
            # Deterministic spread so buildings of one archetype still differ.
            jitter = 1.0 + 0.12 * np.sin(index * 1.7)
            specs.append(BuildingSpec(
                name="B%02d" % index,
                archetype=archetype,
                load_peak_kw=round(load * jitter, 3),
                pv_kwp=round(pv * jitter, 3),
                battery_capacity_kwh=round(bat_kwh * jitter, 3),
                battery_power_kw=round(bat_kw * jitter, 3),
                thermal_capacitance=round(cap * jitter, 3),
                thermal_resistance=6.0,
                hp_thermal_kw=hp,
                hp_cop=3.2,
                solar_gain_kw=round(1.1 * jitter, 3),
                internal_gain_kw=0.3,
                comfort_low=22.0,
                comfort_high=26.0,
            ))
    specs.sort(key=lambda s: s.name)
    return specs


class MockBackend(Backend):
    """Cooling-dominated summer neighbourhood, hourly timesteps."""

    hours_per_step = 1.0

    def __init__(self, n_days: int = 7, specs: Optional[List[BuildingSpec]] = None,
                 battery_efficiency: float = 0.95,
                 outdoor_mean: float = 30.0, outdoor_amplitude: float = 5.0):
        self.specs = specs if specs is not None else default_neighbourhood()
        self.building_names = [s.name for s in self.specs]
        self.n_days = int(n_days)
        self.n_timesteps = self.n_days * HOURS_PER_DAY
        self.battery_efficiency = float(battery_efficiency)
        self.outdoor_mean = float(outdoor_mean)
        self.outdoor_amplitude = float(outdoor_amplitude)
        self._by_name = {s.name: s for s in self.specs}
        self.reset()

    # ------------------------------------------------------------- exogenous
    def outdoor_temperature(self, t: int) -> float:
        hour, day = t % HOURS_PER_DAY, t // HOURS_PER_DAY
        seasonal = self.outdoor_mean + 2.0 * np.sin(2 * np.pi * day / 7.0)
        diurnal = self.outdoor_amplitude * np.sin(2 * np.pi * (hour - 9) / 24.0)
        return float(seasonal + diurnal)

    @staticmethod
    def irradiance(t: int) -> float:
        """Normalised solar irradiance, 0 at night, 1 at solar noon."""
        hour = t % HOURS_PER_DAY
        if hour < 6 or hour > 19:
            return 0.0
        return float(max(0.0, np.sin(np.pi * (hour - 6) / 13.0)) ** 1.1)

    def base_load_kwh(self, spec: BuildingSpec, t: int) -> float:
        hour, day = t % HOURS_PER_DAY, t // HOURS_PER_DAY
        # A gentle day-to-day drift, deterministic, so days are not identical.
        drift = 1.0 + 0.08 * np.sin(2 * np.pi * (day + 1) / 3.0 + spec.phase)
        return float(spec.load_peak_kw * LOAD_SHAPE[hour] * drift * self.hours_per_step)

    def solar_kwh(self, spec: BuildingSpec, t: int) -> float:
        return float(spec.pv_kwp * self.irradiance(t) * self.hours_per_step)

    # ----------------------------------------------------------------- state
    def reset(self) -> None:
        self._t = 0
        self._temperature = {s.name: s.comfort_mid for s in self.specs}
        self._soc = {s.name: 0.5 * s.battery_capacity_kwh for s in self.specs}
        self._last_net = {s.name: 0.0 for s in self.specs}

    @property
    def timestep(self) -> int:
        return self._t

    def observe(self) -> Dict[str, BuildingObs]:
        t = self._t
        t_out, irr = self.outdoor_temperature(t), self.irradiance(t)
        out: Dict[str, BuildingObs] = {}
        for spec in self.specs:
            out[spec.name] = BuildingObs(
                name=spec.name,
                timestep=t,
                hour=t % HOURS_PER_DAY,
                day=t // HOURS_PER_DAY,
                outdoor_temperature=t_out,
                solar_irradiance=irr,
                indoor_temperature=self._temperature[spec.name],
                comfort_low=spec.comfort_low,
                comfort_high=spec.comfort_high,
                base_load_kwh=self.base_load_kwh(spec, t),
                solar_generation_kwh=self.solar_kwh(spec, t),
                battery_soc_kwh=self._soc[spec.name],
                battery_capacity_kwh=spec.battery_capacity_kwh,
                battery_max_power_kw=spec.battery_power_kw,
                hp_max_electric_kw=spec.hp_max_electric_kw,
                hp_cop=spec.hp_cop,
                last_net_kwh=self._last_net[spec.name],
            )
        return out

    # ------------------------------------------------------------------ step
    def step(self, actions: Dict[str, Action]) -> Dict[str, StepOutcome]:
        t = self._t
        dt = self.hours_per_step
        t_out, irr = self.outdoor_temperature(t), self.irradiance(t)
        eff = self.battery_efficiency
        results: Dict[str, StepOutcome] = {}

        for spec in self.specs:
            action = actions.get(spec.name) or Action()
            hvac = float(np.clip(getattr(action, "hvac", 0.0), -1.0, 1.0))
            batt_cmd = float(np.clip(getattr(action, "battery", 0.0), -1.0, 1.0))
            if not np.isfinite(hvac):
                hvac = 0.0
            if not np.isfinite(batt_cmd):
                batt_cmd = 0.0

            # --- heat pump -------------------------------------------------
            q_thermal = hvac * spec.hp_thermal_kw                  # kW, + heat
            hp_kwh = abs(q_thermal) / spec.hp_cop * dt

            # --- battery ---------------------------------------------------
            soc = self._soc[spec.name]
            power = batt_cmd * spec.battery_power_kw
            if spec.battery_capacity_kwh <= 0.0:
                battery_kwh = 0.0
            elif power >= 0.0:
                draw = min(power * dt, max(0.0, spec.battery_capacity_kwh - soc) / eff)
                soc += draw * eff
                battery_kwh = draw
            else:
                delivered = min(-power * dt, soc * eff)
                soc -= delivered / eff
                battery_kwh = -delivered
            self._soc[spec.name] = soc

            # --- energy balance --------------------------------------------
            base = self.base_load_kwh(spec, t)
            solar = self.solar_kwh(spec, t)
            net = base + hp_kwh + battery_kwh - solar

            # --- thermal state ---------------------------------------------
            temp = self._temperature[spec.name]
            gain = spec.solar_gain_kw * irr + spec.internal_gain_kw
            d_temp = dt * ((t_out - temp) / spec.thermal_resistance
                           + gain + q_thermal) / spec.thermal_capacitance
            new_temp = temp + d_temp
            self._temperature[spec.name] = new_temp

            mean_temp = 0.5 * (temp + new_temp)
            exceedance = (max(0.0, mean_temp - spec.comfort_high)
                          + max(0.0, spec.comfort_low - mean_temp)) * dt

            self._last_net[spec.name] = net
            results[spec.name] = StepOutcome(
                net_kwh=net, degree_hours=exceedance, indoor_temperature=new_temp,
                battery_soc_kwh=soc, hp_electric_kwh=hp_kwh, battery_kwh=battery_kwh,
                base_load_kwh=base, solar_kwh=solar,
            )

        self._t += 1
        return results

    # -------------------------------------------------------------- metadata
    def metadata(self) -> Dict[str, dict]:
        return {s.name: dict(
            archetype=s.archetype,
            load_peak_kw=s.load_peak_kw,
            pv_kwp=s.pv_kwp,
            battery_kwh=s.battery_capacity_kwh,
            battery_kw=s.battery_power_kw,
            thermal_capacitance=s.thermal_capacitance,
            hp_electric_kw=round(s.hp_max_electric_kw, 2),
            comfort_band="%g-%g C" % (s.comfort_low, s.comfort_high),
        ) for s in self.specs}
