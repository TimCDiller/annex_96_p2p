"""Adapter for the real Annex 96 Common Exercise 1 neighbourhood.

Verified against both upstream datasets: Vermont (heating, 743 steps) and
Texas (cooling, 719 steps), 25 buildings each.
The facts it is built on, all read off a live environment rather than assumed:

  buildings                25 in each dataset
  time_steps               744 (Vermont, January) / 720 (Texas, June)
  observation_names[0]     29 names, including comfort_band and
                           electrical_storage_soc
  action_names[0]          ['electrical_storage', 'heating_device']  Vermont
                           ['electrical_storage', 'cooling_device']  Texas
  action_space[0]          battery bounded by nominal_power/capacity,
                           hvac one-sided [0, 1]
  reset()                  returns (obs, info)
  step()                   returns a 5-tuple, ~0.15 s per call

These matter enough to spell out, because getting them wrong gives
plausible-looking nonsense rather than an error:

1. THE ACTION SPACE IS NOT [-1, 1]. The battery action is a fraction of
   CAPACITY per timestep, bounded by the power rating: 2.88 kW over 37.8 kWh
   is 0.0762. Our own Action.battery is a fraction of RATED POWER, so the two
   differ by `nominal_power / capacity` and have to be converted. The hvac
   action is one-sided, [0, 1].

2. ONLY ONE HVAC DEVICE IS ACTUATED, and which one depends on the dataset.
   The other exists but is vestigial -- 0.09 kW of cooling on a Vermont house
   against 19.5 kW of heating -- so reading the wrong one understates the load
   by two orders of magnitude. Take it from the action list, never by assuming.
   The consequence reaches all the way out to the agents: the infeasible half
   of an `hvac` command is silently discarded by the physics, so bidding
   against it buys energy that is then curtailed. Hence
   `BuildingObs.hp_is_heating` and `hp_draw_kwh()`.

3. `efficiency` IS NOT A COP. It is 0.253 on Texas building 0, a
   fraction-of-Carnot figure. The actual COP comes from `get_cop()`. Reporting
   `efficiency` as the COP is wrong by a factor of 17. The COP is also strongly
   weather-dependent: heating at -21 degC gives 1.10, which is physically
   correct and not a bug.

4. COMFORT IS NOT FIXED. `comfort_band` is an observation (2.0 K here) around
   a per-building, per-timestep setpoint, and the setpoint MOVES -- by up to
   7 K within a single building. Do not hardcode a band.

5. THE FIRST 13 TIMESTEPS ARE A WARMUP. The buildings are
   LSTMDynamicsBuilding with `lookback = 13`, so the indoor temperature is
   pinned at its initial value until the model has that much history -- the
   hvac action provably does nothing before then. Comfort penalties in that
   window are unavoidable and mean nothing. Fine over a full month, but do not
   draw conclusions from a two-day run.

6. `env.time_steps` IS ONE MORE THAN THE NUMBER OF LEGAL STEPS. See the note
   on `horizon` below; asking for the full count raises IndexError on the very
   last step, which is a nasty way to lose a long run.

7. THE OBSERVATION DESCRIBES THE STEP BEING LEFT, NOT THE ONE BEING TAKEN.
   CityLearn applies an action to the transition INTO time_step + 1 and bills
   it against that index, so `non_shiftable_load` and `solar_generation` as
   observed are one hour stale. Verified: with heat pump and battery idle, the
   net for step t equals the OBSERVED passive net of step t + 1, exactly.
   This is CityLearn's own convention and is NOT corrected here -- agents are
   bidding on a step whose load and PV they cannot see, which on a sunrise
   ramp reached 8.4 kWh in a single building-hour and is the largest single
   cause of curtailment in a run. Treat it as a forecasting problem -- or
   dispatch with Action.net_target_kwh, which lets the battery absorb the
   error within the step (see `_balancing`), as far as its power allows.

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

    citylearn_root: ../annex96_common_exercise_1
    citylearn_schema: ../annex96_common_exercise_1/data/datasets/annex96_ce1_vt_neighborhood/schema.json

`citylearn_root` is put on sys.path, so you do not have to pip install the
fork. You do need its dependencies, PyTorch among them, because the CE1
buildings carry LSTM dynamics models:

    pip install torch

Building the environment takes about 30 s and each timestep about 0.15 s, so
a full run is roughly two minutes, doubled while `reference: true`. Set
`days:` in the config to something small while you iterate.
""".strip()


def _first(mapping: dict, *keys, default=0.0):
    for key in keys:
        v = mapping.get(key)
        if v is not None:
            return v
    return default


class CityLearnBackend(Backend):
    """Wraps a CityLearnEnv in the `Backend` interface an Agent sees."""

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

        target = str(Path(schema).resolve()) if schema else dataset
        if schema and not Path(schema).exists():
            raise SystemExit(INSTALL_HINT + "\n\n(schema not found: %s)" % schema)

        # Generated classroom schemas need not sit three levels below an
        # upstream checkout. Resolve the schema before changing directory.
        self._cwd = os.getcwd()
        if schema:
            os.chdir(Path(root).resolve() if root else Path(target).parent)
        try:
            self.env = CityLearnEnv(target, central_agent=False)
        finally:
            os.chdir(self._cwd)

        self.building_names = [b.name for b in self.env.buildings]
        self._obs_names = self.env.observation_names
        self._act_names = self.env.action_names

        # `env.time_steps` counts OBSERVATIONS, 0..time_steps-1, and each step()
        # reads the index it is moving to. So the last legal step lands on
        # time_steps-1 and there are only time_steps-1 of them; asking for the
        # full count walks off the end of every per-timestep array with
        # "IndexError: index 744 is out of bounds for axis 0 with size 744".
        horizon = max(1, int(getattr(self.env, "time_steps", 0) or 720) - 1)
        self.n_timesteps = min(horizon, int(days) * 24) if days else horizon

        # WHICH HVAC DEVICE IS ACTUALLY ACTUATED. Texas exposes
        # `cooling_device` and Vermont `heating_device`, and the other one is
        # present but vestigial -- 0.09 kW of cooling on a Vermont house
        # against 19.5 kW of heating. Reading the wrong one understates the
        # load by two orders of magnitude, so take it from the action list.
        self._hvac_kind = []
        for names in self._act_names:
            kind = None
            for a in names:
                if "heating" in a:
                    kind = "heating"
                    break
                if "cooling" in a:
                    kind = "cooling"
            self._hvac_kind.append(kind)

        # Conversion factor per building: our Action.battery is a fraction of
        # rated POWER, CityLearn wants a fraction of CAPACITY.
        self._batt_scale = []
        for b in self.env.buildings:
            st = getattr(b, "electrical_storage", None)
            cap = float(getattr(st, "capacity", 0.0) or 0.0)
            power = float(getattr(st, "nominal_power", 0.0) or 0.0)
            self._batt_scale.append((power / cap) if cap > 0 else 0.0)

        # Real-time battery balancing for Action.net_target_kwh. CityLearn
        # updates the battery LAST within a step, after the heat pump and the
        # household load for that hour, so at that moment the hour's actual
        # demand and PV are known. For a building with a target, replace the
        # fixed-rate battery update with one that charges or discharges the
        # difference between the target and that demand; Battery.charge()
        # still enforces power, capacity and depth-of-discharge limits.
        # Pinned by tests/test_citylearn.py::test_net_target_is_met.
        self._targets: List[Optional[float]] = [None] * len(self.env.buildings)
        for i, b in enumerate(self.env.buildings):
            if getattr(b, "electrical_storage", None) is not None:
                b.update_electrical_storage = self._balancing(i, b,
                                                              b.update_electrical_storage)

        self._t = 0
        self._raw: List = []
        self._last_net = {n: 0.0 for n in self.building_names}
        self.reset()

    def _balancing(self, i, building, fixed_rate):
        def update(action: float):
            target = self._targets[i]
            if target is None:
                return fixed_rate(action)
            t = building.time_step
            demand = sum(float(d.electricity_consumption[t]) for d in (
                building.cooling_device, building.heating_device,
                building.dhw_device, building.non_shiftable_load_device))
            surplus = abs(float(building.solar_generation[t]))
            building.electrical_storage.charge(target - (demand - surplus))
        return update

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

            heating = self._hvac_kind[i] == "heating"
            cd = getattr(b, "heating_device" if heating else "cooling_device", None)
            outdoor = float(_first(v, "outdoor_dry_bulb_temperature", default=25.0))
            cop = 3.0
            if cd is not None and hasattr(cd, "get_cop"):
                try:
                    cop = float(np.ravel(cd.get_cop(outdoor, heating))[0])
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
                hp_is_heating=heating,
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
        chosen = [actions.get(n) or Action() for n in self.building_names]
        for i, a in enumerate(chosen):
            self._targets[i] = getattr(a, "net_target_kwh", None)
            if self._targets[i] is not None:
                # A zero rate keeps the battery last in CityLearn's update
                # order; a negative one would move it ahead of the loads.
                chosen[i] = Action(hvac=a.hvac, battery=0.0)
        encoded = [self._encode(i, a) for i, a in enumerate(chosen)]
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
                # CityLearn preallocates the full episode array: [-1] is the
                # unfilled final slot until the last hour, not current SOC.
                soc = float(st.soc[b.time_step])
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
            cd = getattr(b, "heating_device" if self._hvac_kind[i] == "heating"
                         else "cooling_device", None)
            pv = getattr(b, "pv", None)
            out[name] = dict(
                battery_kwh=round(float(getattr(st, "capacity", 0.0) or 0.0), 2),
                battery_kw=round(float(getattr(st, "nominal_power", 0.0) or 0.0), 2),
                pv_kw=round(float(getattr(pv, "nominal_power", 0.0) or 0.0), 2),
                hp_electric_kw=round(float(getattr(cd, "nominal_power", 0.0) or 0.0), 2),
                actions=len(self._act_names[i]),
            )
        return out
