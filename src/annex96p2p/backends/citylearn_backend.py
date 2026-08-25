"""Adapter for the real Annex 96 Common Exercise 1 neighbourhood.

    #####################################################################
    #  NOT YET VERIFIED AGAINST THE UPSTREAM DATASET.                   #
    #                                                                   #
    #  Everything else in this repo is tested and runs. This file is a  #
    #  best-effort mapping onto CityLearn that has to be checked once   #
    #  against the actual Annex 96 fork, because the exact observation  #
    #  and action names in that fork drive the whole translation.       #
    #  See docs/citylearn.md for the checklist.                         #
    #####################################################################

Why the mapping is done by NAME and not by index: CityLearn hands back a
flat vector per building, and the meaning of each slot depends on which
observations the schema has switched on. Reading `env.observation_names`
and zipping it into a dict is the only version-stable way to do this.

Two things must change in the CE1 dataset for this exercise to work:

  1. The schema ships with `"pricing": null` and every `electricity_pricing`
     observation set to `"active": false`. Prices in this repo come from the
     Tariff object rather than from the schema, so that is harmless -- but
     if you would rather drive prices through CityLearn, author a
     `pricing.csv` and switch those observations on.

  2. CE1 forbids exporting PV to the grid. Keep that rule. It is what makes
     the peer-to-peer market worth having: unsold surplus is curtailed and
     earns nothing, so any peer paying more than zero beats the grid.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from ..api import Action, BuildingObs
from .base import Backend, StepOutcome

INSTALL_HINT = """
CityLearn is not installed, or the Annex 96 dataset was not found.

    pip install -e ".[citylearn]"

then clone the exercise dataset next to this repo:

    git clone https://github.com/kkaspar10/annex96_common_exercise_1

and point at it, either in your config file:

    backend: citylearn
    citylearn_schema: ../annex96_common_exercise_1/data/datasets/annex96_ce1_tx_neighborhood/schema.json

Note that the CE1 buildings carry LSTM dynamics models, so this backend also
needs PyTorch and is far slower than the mock backend. Develop against the
mock backend, then confirm here.
""".strip()


def _first(mapping: dict, *keys, default=0.0):
    """First present key, so one adapter survives small naming differences."""
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


class CityLearnBackend(Backend):
    """Wraps a CityLearnEnv so it looks like the mock backend to an Agent."""

    hours_per_step = 1.0

    def __init__(self, dataset: str = "annex96_ce1_tx_neighborhood",
                 schema: Optional[str] = None, days: Optional[int] = None,
                 comfort_low: float = 20.0, comfort_high: float = 26.0):
        try:
            from citylearn.citylearn import CityLearnEnv
        except Exception as exc:                                 # noqa: BLE001
            raise SystemExit(INSTALL_HINT + "\n\n(import error: %s)" % exc)

        target = schema or dataset
        if schema and not Path(schema).exists():
            raise SystemExit(INSTALL_HINT + "\n\n(schema not found: %s)" % schema)

        self.env = CityLearnEnv(target, central_agent=False)
        self.building_names = [b.name for b in self.env.buildings]
        self._obs_names = self.env.observation_names
        self._act_names = self.env.action_names
        self.comfort_low = comfort_low
        self.comfort_high = comfort_high

        horizon = getattr(self.env, "time_steps", None) or 24
        self.n_timesteps = min(horizon, days * 24) if days else horizon

        self._t = 0
        self._raw: List[np.ndarray] = []
        self._last_net = {name: 0.0 for name in self.building_names}
        self.reset()

    # ------------------------------------------------------------------ core
    def reset(self) -> None:
        out = self.env.reset()
        self._raw = out[0] if isinstance(out, tuple) else out
        self._t = 0
        self._last_net = {name: 0.0 for name in self.building_names}

    @property
    def timestep(self) -> int:
        return self._t

    def _named(self, index: int) -> dict:
        return dict(zip(self._obs_names[index], list(self._raw[index])))

    def observe(self) -> Dict[str, BuildingObs]:
        result: Dict[str, BuildingObs] = {}
        for i, name in enumerate(self.building_names):
            values = self._named(i)
            building = self.env.buildings[i]
            hour = int(_first(values, "hour", default=(self._t % 24) + 1)) - 1

            capacity = float(getattr(getattr(building, "electrical_storage", None),
                                     "capacity", 0.0) or 0.0)
            soc_fraction = float(_first(values, "electrical_storage_soc", default=0.0))

            result[name] = BuildingObs(
                name=name,
                timestep=self._t,
                hour=max(0, min(23, hour)),
                day=self._t // 24,
                outdoor_temperature=float(_first(values, "outdoor_dry_bulb_temperature")),
                solar_irradiance=float(_first(values, "direct_solar_irradiance")) / 1000.0,
                indoor_temperature=float(_first(values, "indoor_dry_bulb_temperature",
                                                default=22.0)),
                comfort_low=float(_first(values, "indoor_dry_bulb_temperature_cooling_set_point",
                                         default=self.comfort_low)) - 2.0,
                comfort_high=float(_first(values, "indoor_dry_bulb_temperature_cooling_set_point",
                                          default=self.comfort_high)) + 2.0,
                base_load_kwh=float(_first(values, "non_shiftable_load")),
                solar_generation_kwh=float(_first(values, "solar_generation")),
                battery_soc_kwh=soc_fraction * capacity,
                battery_capacity_kwh=capacity,
                battery_max_power_kw=float(getattr(
                    getattr(building, "electrical_storage", None),
                    "nominal_power", 0.0) or 0.0),
                hp_max_electric_kw=float(getattr(
                    getattr(building, "cooling_device", None),
                    "nominal_power", 0.0) or 0.0),
                hp_cop=float(getattr(getattr(building, "cooling_device", None),
                                     "efficiency", 3.0) or 3.0),
                last_net_kwh=self._last_net[name],
            )
        return result

    def _encode(self, name: str, index: int, action: Action) -> List[float]:
        """Map our two-number Action onto whatever actions this schema exposes."""
        encoded = []
        for action_name in self._act_names[index]:
            if "electrical_storage" in action_name:
                encoded.append(float(np.clip(action.battery, -1.0, 1.0)))
            elif "cooling" in action_name:
                # our hvac is signed; cooling takes the negative half
                encoded.append(float(np.clip(-action.hvac, 0.0, 1.0)))
            elif "heating" in action_name:
                encoded.append(float(np.clip(action.hvac, 0.0, 1.0)))
            else:
                encoded.append(0.0)
        return encoded

    def step(self, actions: Dict[str, Action]) -> Dict[str, StepOutcome]:
        encoded = [self._encode(name, i, actions.get(name) or Action())
                   for i, name in enumerate(self.building_names)]

        out = self.env.step(encoded)
        self._raw = out[0]

        results: Dict[str, StepOutcome] = {}
        for i, name in enumerate(self.building_names):
            building = self.env.buildings[i]
            net = float(building.net_electricity_consumption[-1])
            temp = float(building.indoor_dry_bulb_temperature[-1])
            exceed = (max(0.0, temp - self.comfort_high)
                      + max(0.0, self.comfort_low - temp))
            self._last_net[name] = net
            results[name] = StepOutcome(
                net_kwh=net, degree_hours=exceed, indoor_temperature=temp,
                battery_soc_kwh=float(getattr(
                    getattr(building, "electrical_storage", None), "soc",
                    [0.0])[-1]) if getattr(building, "electrical_storage", None) else 0.0,
                hp_electric_kwh=float(
                    building.cooling_electricity_consumption[-1]
                    if hasattr(building, "cooling_electricity_consumption") else 0.0),
                battery_kwh=0.0,
                base_load_kwh=float(building.non_shiftable_load[-1]),
                solar_kwh=float(abs(building.solar_generation[-1])),
            )

        self._t += 1
        return results

    def metadata(self) -> Dict[str, dict]:
        out = {}
        for i, name in enumerate(self.building_names):
            building = self.env.buildings[i]
            storage = getattr(building, "electrical_storage", None)
            out[name] = dict(
                battery_kwh=round(float(getattr(storage, "capacity", 0.0) or 0.0), 2),
                pv_kw=round(float(getattr(getattr(building, "pv", None),
                                          "nominal_power", 0.0) or 0.0), 2),
                actions=len(self._act_names[i]),
            )
        return out
