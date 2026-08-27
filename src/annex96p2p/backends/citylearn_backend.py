"""Adapter for the real Annex 96 Common Exercise 1 neighbourhood.

Verified against the upstream dataset (Texas, 25 buildings, 720 timesteps).
The facts it is built on, all read off a live environment rather than assumed:

  buildings                25
  time_steps               720  (30 days, starting June -- month == 6)
  observation_names[0]     29 names, including comfort_band and
                           electrical_storage_soc
  action_names[0]          ['electrical_storage', 'cooling_device']
                           -- the heating device exists but is NOT actuated
  action_space[0]          low  [-0.1667, 0.0]
                           high [+0.1667, 1.0]
  reset()                  returns (obs, info)
  step()                   returns a 5-tuple, ~0.15 s per call

Three of those matter enough to spell out, because getting them wrong gives
plausible-looking nonsense rather than an error:

1. THE ACTION SPACE IS NOT [-1, 1]. The battery action is a fraction of
   CAPACITY per timestep, bounded by the power rating: 2.5 kW over 15 kWh is
   0.1667. Our own Action.battery is a fraction of RATED POWER, so the two
   differ by `nominal_power / capacity` and have to be converted. The cooling
   action is one-sided, [0, 1].

2. `cooling_device.efficiency` IS NOT A COP. It is 0.253 on building 0, a
   fraction-of-Carnot figure. The actual COP comes from `get_cop()` and is
   about 4.25. Reporting `efficiency` as the COP is wrong by a factor of 17.

3. COMFORT IS NOT FIXED. `comfort_band` is an observation (2.0 K here) around
   a per-building, per-timestep cooling setpoint. Do not hardcode 22-26.

4. THE FIRST 13 TIMESTEPS ARE A WARMUP. The buildings are
   LSTMDynamicsBuilding with `lookback = 13`, so the indoor temperature is
   pinned at its initial value until the model has that much history --
   the cooling action provably does nothing before then. Comfort penalties
   in that window are unavoidable and mean nothing. Fine over 720 steps,
   but do not draw conclusions from a two-day run.

Two further notes on the dataset itself:

  * CE1 ships no pricing. Prices come from our Tariff object instead, so
    `"pricing": null` in the schema is harmless.
  * The export ban is CE1's own rule and we keep it -- it is what gives the
    peer-to-peer market its reason to exist.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from ..api import Action, BuildingObs
from .base import Backend, StepOutcome

INSTALL_HINT = """
Could not import CityLearn.

The Annex 96 exercise ships its own fork of CityLearn inside its repository,
so the usual route is to clone that and point this at it:

    git clone https://github.com/kkaspar10/annex96_common_exercise_1

then in your config:

    backend: citylearn
    citylearn_root: ../annex96_common_exercise_1
    citylearn_schema: ../annex96_common_exercise_1/data/datasets/annex96_ce1_tx_neighborhood/schema.json

`citylearn_root` is put on sys.path, so you do not have to pip install the
fork. You do need its dependencies, PyTorch among them, because the CE1
buildings carry LSTM dynamics models:

    pip install torch

Building the environment takes about 30 s and each timestep about 0.15 s, so
a 720-step run is roughly two minutes, doubled while `reference: true`. Set
`days:` in the config to something small while you iterate.
""".strip()


def _first(mapping: dict, *keys, default=0.0):
    for key in keys:
        v = mapping.get(key)
        if v is not None:
            return v
    return default


class CityLearnBackend(Backend):
    """Wraps a CityLearnEnv so it looks like the mock backend to an Agent."""

    hours_per_step = 1.0

    def __init__(self, dataset: str = "annex96_ce1_tx_neighborhood",
                 schema: Optional[str] = None, days: Optional[int] = None,
                 root: Optional[str] = None):
        if root:
            r = str(Path(root).resolve())
            if r not in sys.path:
                sys.path.insert(0, r)
        try:
            from citylearn.citylearn import CityLearnEnv
        except Exception as exc:                                 # noqa: BLE001
            raise SystemExit(INSTALL_HINT + "\n\n(import error: %s)" % exc)

        target = schema or dataset
        if schema and not Path(schema).exists():
            raise SystemExit(INSTALL_HINT + "\n\n(schema not found: %s)" % schema)

        # CityLearn resolves dataset-relative paths against the working
        # directory, so run from the schema's own tree.
        self._cwd = os.getcwd()
        if schema:
            os.chdir(Path(schema).resolve().parents[3])
        try:
            self.env = CityLearnEnv(target, central_agent=False)
        finally:
            os.chdir(self._cwd)

        self.building_names = [b.name for b in self.env.buildings]
        self._obs_names = self.env.observation_names
        self._act_names = self.env.action_names

        horizon = int(getattr(self.env, "time_steps", 0) or 720)
        self.n_timesteps = min(horizon, int(days) * 24) if days else horizon

        # Conversion factor per building: our Action.battery is a fraction of
        # rated POWER, CityLearn wants a fraction of CAPACITY.
        self._batt_scale = []
        for b in self.env.buildings:
            st = getattr(b, "electrical_storage", None)
            cap = float(getattr(st, "capacity", 0.0) or 0.0)
            power = float(getattr(st, "nominal_power", 0.0) or 0.0)
            self._batt_scale.append((power / cap) if cap > 0 else 0.0)

        self._t = 0
        self._raw: List = []
        self._last_net = {n: 0.0 for n in self.building_names}
        self.reset()

    # ------------------------------------------------------------------ core
    def reset(self) -> None:
        out = self.env.reset()
        self._raw = out[0] if isinstance(out, tuple) else out
        self._t = 0
        self._last_net = {n: 0.0 for n in self.building_names}

    @property
    def timestep(self) -> int:
        return self._t

    def _named(self, i: int) -> dict:
        return dict(zip(self._obs_names[i], list(self._raw[i])))

    def observe(self) -> Dict[str, BuildingObs]:
        result: Dict[str, BuildingObs] = {}
        for i, name in enumerate(self.building_names):
            v = self._named(i)
            b = self.env.buildings[i]

            # `hour` is 1-based in CityLearn.
            hour = int(_first(v, "hour", default=(self._t % 24) + 1)) - 1

            st = getattr(b, "electrical_storage", None)
            capacity = float(getattr(st, "capacity", 0.0) or 0.0)
            power = float(getattr(st, "nominal_power", 0.0) or 0.0)
            # electrical_storage_soc is a FRACTION of capacity.
            soc_kwh = float(_first(v, "electrical_storage_soc")) * capacity

            # Comfort is a band around a per-timestep setpoint, both observed.
            setpoint = float(_first(
                v, "indoor_dry_bulb_temperature_cooling_set_point", default=24.0))
            band = float(_first(v, "comfort_band", default=2.0))

            # Domestic hot water is NOT controllable in this schema -- the
            # action list is only [electrical_storage, cooling_device] -- so it
            # belongs in the fixed load the agent has to cover.
            base = (float(_first(v, "non_shiftable_load"))
                    + float(_first(v, "dhw_electricity_consumption")))

            cd = getattr(b, "cooling_device", None)
            outdoor = float(_first(v, "outdoor_dry_bulb_temperature", default=25.0))
            cop = 3.0
            if cd is not None and hasattr(cd, "get_cop"):
                try:
                    cop = float(np.ravel(cd.get_cop(outdoor, False))[0])
                except Exception:                                # noqa: BLE001
                    pass

            result[name] = BuildingObs(
                name=name, timestep=self._t,
                hour=max(0, min(23, hour)), day=self._t // 24,
                outdoor_temperature=outdoor,
                # W/m2 in CityLearn; normalise to roughly 0..1
                solar_irradiance=float(_first(v, "direct_solar_irradiance")) / 1000.0,
                indoor_temperature=float(_first(
                    v, "indoor_dry_bulb_temperature", default=setpoint)),
                comfort_low=setpoint - band,
                comfort_high=setpoint + band,
                base_load_kwh=base,
                solar_generation_kwh=abs(float(_first(v, "solar_generation"))),
                battery_soc_kwh=soc_kwh,
                battery_capacity_kwh=capacity,
                battery_max_power_kw=power,
                hp_max_electric_kw=float(getattr(cd, "nominal_power", 0.0) or 0.0),
                hp_cop=cop,
                last_net_kwh=self._last_net[name],
            )
        return result

    # ------------------------------------------------------------------ step
    def _encode(self, i: int, action: Action) -> List[float]:
        out = []
        for act_name in self._act_names[i]:
            if "electrical_storage" in act_name:
                # fraction of rated power -> fraction of capacity
                out.append(float(np.clip(action.battery, -1.0, 1.0))
                           * self._batt_scale[i])
            elif "cooling" in act_name:
                # one-sided [0, 1]; our hvac is negative for cooling
                out.append(float(np.clip(-action.hvac, 0.0, 1.0)))
            elif "heating" in act_name:
                out.append(float(np.clip(action.hvac, 0.0, 1.0)))
            else:
                out.append(0.0)
        return out

    def step(self, actions: Dict[str, Action]) -> Dict[str, StepOutcome]:
        encoded = [self._encode(i, actions.get(n) or Action())
                   for i, n in enumerate(self.building_names)]
        out = self.env.step(encoded)
        self._raw = out[0]

        results: Dict[str, StepOutcome] = {}
        for i, name in enumerate(self.building_names):
            b = self.env.buildings[i]
            v = self._named(i)
            net = float(b.net_electricity_consumption[-1])
            temp = float(b.indoor_dry_bulb_temperature[-1])

            setpoint = float(_first(
                v, "indoor_dry_bulb_temperature_cooling_set_point", default=24.0))
            band = float(_first(v, "comfort_band", default=2.0))
            exceed = (max(0.0, temp - (setpoint + band))
                      + max(0.0, (setpoint - band) - temp))

            st = getattr(b, "electrical_storage", None)
            soc = 0.0
            if st is not None and getattr(st, "soc", None) is not None:
                soc = float(st.soc[-1])
                cap = float(getattr(st, "capacity", 0.0) or 0.0)
                if cap and soc <= 1.0001:      # stored as a fraction
                    soc *= cap

            self._last_net[name] = net
            results[name] = StepOutcome(
                net_kwh=net, degree_hours=exceed, indoor_temperature=temp,
                battery_soc_kwh=soc,
                base_load_kwh=float(_first(v, "non_shiftable_load")),
            )

        self._t += 1
        return results

    # -------------------------------------------------------------- metadata
    def metadata(self) -> Dict[str, dict]:
        out = {}
        for i, name in enumerate(self.building_names):
            b = self.env.buildings[i]
            st = getattr(b, "electrical_storage", None)
            cd = getattr(b, "cooling_device", None)
            pv = getattr(b, "pv", None)
            out[name] = dict(
                battery_kwh=round(float(getattr(st, "capacity", 0.0) or 0.0), 2),
                battery_kw=round(float(getattr(st, "nominal_power", 0.0) or 0.0), 2),
                pv_kw=round(float(getattr(pv, "nominal_power", 0.0) or 0.0), 2),
                hp_electric_kw=round(float(getattr(cd, "nominal_power", 0.0) or 0.0), 2),
                actions=len(self._act_names[i]),
            )
        return out
