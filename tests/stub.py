"""A minimal backend for FORMAL tests only.

WHAT THIS IS FOR
----------------
Checking contracts: that a bid vector comes back the right shape, that the
ledger balances, that the registry hands each participant only its own
buildings, that a crashing agent is substituted, that a timeout fires. Things
that are true or false regardless of physics.

WHAT THIS IS NOT FOR
--------------------
**Anything about how well a strategy performs.** There is no building model
here. The temperature is a first-order decay toward a fixed value, the loads
are a repeating pattern, and the battery is an integrator. None of it is
calibrated against anything, and a number produced by this backend means
nothing outside the test that asserted it.

Performance belongs on the real stock -- see `tests/test_citylearn.py`, which
runs against the actual Annex 96 neighbourhood and skips itself when the
dataset is not installed. If you find yourself wanting to assert that agent A
beats agent B, you are in the wrong file.

It lives in `tests/` rather than in the package because it is test scaffolding,
not something a submission should ever see.
"""
from __future__ import annotations

import math
from typing import Dict, List

from annex96p2p.api import Action, BuildingObs
from annex96p2p.backends.base import Backend, StepOutcome


class StubBackend(Backend):
    """Deterministic, physics-free, fast."""

    hours_per_step = 1.0

    def __init__(self, n_buildings: int = 6, days: int = 2):
        self.building_names = ["S%02d" % (i + 1) for i in range(n_buildings)]
        self.n_timesteps = days * 24
        # Alternate the equipment so tests can exercise both branches: even
        # buildings have a battery and no PV, odd ones the reverse.
        self._spec = {}
        for i, n in enumerate(self.building_names):
            self._spec[n] = dict(
                battery_kwh=10.0 if i % 2 == 0 else 0.0,
                battery_kw=2.5 if i % 2 == 0 else 0.0,
                pv_kw=0.0 if i % 2 == 0 else 3.0,
                hp_kw=1.5,
            )
        self.reset()

    # ------------------------------------------------------------------ core
    def reset(self) -> None:
        self._t = 0
        self._temp = {n: 23.0 for n in self.building_names}
        self._soc = {n: 0.0 for n in self.building_names}
        self._last = {n: 0.0 for n in self.building_names}

    @property
    def timestep(self) -> int:
        return self._t

    def _load(self, i: int, t: int) -> float:
        return 0.6 + 0.4 * ((i + t) % 5) / 4.0

    def _solar(self, n: str, t: int) -> float:
        hour = t % 24
        if not (7 <= hour <= 18):
            return 0.0
        return self._spec[n]["pv_kw"] * math.sin(math.pi * (hour - 7) / 11.0)

    def observe(self) -> Dict[str, BuildingObs]:
        t = self._t
        out = {}
        for i, n in enumerate(self.building_names):
            s = self._spec[n]
            out[n] = BuildingObs(
                name=n, timestep=t, hour=t % 24, day=t // 24,
                outdoor_temperature=25.0 + 3.0 * math.sin(2 * math.pi * (t % 24) / 24),
                solar_irradiance=0.5,
                indoor_temperature=self._temp[n],
                comfort_low=21.0, comfort_high=25.0,
                base_load_kwh=self._load(i, t),
                solar_generation_kwh=self._solar(n, t),
                battery_soc_kwh=self._soc[n],
                battery_capacity_kwh=s["battery_kwh"],
                battery_max_power_kw=s["battery_kw"],
                hp_max_electric_kw=s["hp_kw"], hp_cop=3.0,
                last_net_kwh=self._last[n],
            )
        return out

    def step(self, actions: Dict[str, Action]) -> Dict[str, StepOutcome]:
        t = self._t
        out = {}
        for i, n in enumerate(self.building_names):
            s = self._spec[n]
            a = actions.get(n) or Action()
            hvac = max(-1.0, min(1.0, float(a.hvac or 0.0)))
            batt = max(-1.0, min(1.0, float(a.battery or 0.0)))

            hp_kwh = abs(hvac) * s["hp_kw"]
            if s["battery_kwh"] > 0:
                want = batt * s["battery_kw"]
                if want >= 0:
                    moved = min(want, s["battery_kwh"] - self._soc[n])
                else:
                    moved = -min(-want, self._soc[n])
                self._soc[n] += moved
            else:
                moved = 0.0

            net = self._load(i, t) + hp_kwh + moved - self._solar(n, t)

            # Not a thermal model: a decay toward 24 degC nudged by the heat
            # pump, just so the comfort term is exercised at all.
            self._temp[n] += 0.3 * (24.0 - self._temp[n]) + 1.2 * hvac
            temp = self._temp[n]
            dh = max(0.0, temp - 25.0) + max(0.0, 21.0 - temp)

            self._last[n] = net
            out[n] = StepOutcome(net_kwh=net, degree_hours=dh,
                                 indoor_temperature=temp,
                                 battery_soc_kwh=self._soc[n],
                                 base_load_kwh=self._load(i, t))
        self._t += 1
        return out

    def metadata(self) -> Dict[str, dict]:
        return {n: dict(self._spec[n]) for n in self.building_names}
