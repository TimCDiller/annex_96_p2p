"""Auditable one-zone RC surrogates for the altered-stock thermal models.

Identification uses a fixed excitation added to the ordinary thermostat, so
every building receives the same published experiment on the same seven days.
Only days 1-5 are fitted; days 6-7 measure out-of-sample prediction error.
The model never reads future weather, loads, setpoints, or opponent data.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.optimize import lsq_linear

from .api import Action, Thermostat

EXCITATION = (-0.12, 0.08, 0.15, -0.05, 0.11, -0.10, 0.04, -0.07)
FIT_STEPS = 5 * 24
VALIDATION_STEPS = 2 * 24


def schema_fingerprint(path: str | Path) -> str:
    """Hash schema meaning, independent of whitespace and OS line endings."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def excitation(timestep: int, building_index: int) -> float:
    """Fixed, bounded power perturbation independent of observed outcomes."""
    return EXCITATION[(timestep + 3 * building_index) % len(EXCITATION)]


def features(obs, hvac: float) -> np.ndarray:
    """RC heat balance: outdoor exchange, heat-pump heat, gains, intercept."""
    hour = obs.hour * 2 * np.pi / 24
    heat_kw = obs.hp_draw_kwh(hvac) * obs.hp_cop
    return np.array((obs.outdoor_temperature - obs.indoor_temperature,
        heat_kw if obs.hp_is_heating else -heat_kw,
        obs.solar_irradiance, obs.base_load_kwh,
        np.sin(hour), np.cos(hour), 1.0), dtype=float)


def identify(backend, schema: str, output: str | Path) -> dict:
    """Run the predefined experiment and fit each building separately."""
    if backend.n_timesteps < FIT_STEPS + VALIDATION_STEPS:
        raise ValueError("RC identification needs seven complete days")
    backend.reset()
    thermostat = Thermostat()
    names = list(backend.building_names)
    samples = {name: [] for name in names}
    for t in range(FIT_STEPS + VALIDATION_STEPS):
        obs = backend.observe()
        actions = {}
        for i, name in enumerate(names):
            o = obs[name]
            base = thermostat(o)
            if o.hp_is_heating:
                hvac = float(np.clip(base + excitation(t, i), 0.0, 1.0))
            else:
                hvac = -float(np.clip(-base + excitation(t, i), 0.0, 1.0))
            actions[name] = Action(hvac=hvac)
        outcome = backend.step(actions)
        for name in names:
            o = obs[name]
            samples[name].append((features(o, actions[name].hvac),
                                  outcome[name].indoor_temperature - o.indoor_temperature))

    models = {}
    for name in names:
        x = np.stack([row[0] for row in samples[name]])
        y = np.asarray([row[1] for row in samples[name]])
        # Heat exchange and pump gain retain their physical signs. Other gains
        # absorb unmodelled occupancy and the upstream LSTM's nonlinearities.
        lower = [0.0, 0.0, -10.0, -5.0, -10.0, -10.0, -10.0]
        upper = [1.0, 2.0, 10.0, 5.0, 10.0, 10.0, 10.0]
        fit = lsq_linear(x[13:FIT_STEPS], y[13:FIT_STEPS], bounds=(lower, upper),
                         lsmr_tol="auto", max_iter=200)
        if not fit.success:
            raise RuntimeError("RC fit failed for " + name)
        predictions = x[FIT_STEPS:] @ fit.x
        actual = y[FIT_STEPS:]
        models[name] = dict(coefficients=[float(v) for v in fit.x],
            train_mae_c=float(np.mean(np.abs(x[13:FIT_STEPS] @ fit.x-y[13:FIT_STEPS]))),
            validation_mae_c=float(np.mean(np.abs(predictions-actual))),
            validation_bias_c=float(np.mean(predictions-actual)),
            validation_samples=len(actual))
    source_hash = schema_fingerprint(schema)
    document = dict(version=1, source_schema_sha256=source_hash,
        excitation=list(EXCITATION), training_steps=FIT_STEPS,
        validation_steps=VALIDATION_STEPS, warmup_excluded=13,
        equation="T_next = T + c dot [Tout-T, thermal_pump_kWh, irradiance, base_load, sin(hour), cos(hour), 1]",
        models=models)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(document, indent=2), encoding="utf-8")
    return document


def load_models(path: str | Path, schema: str | Path) -> dict:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    source_hash = schema_fingerprint(schema)
    if document["source_schema_sha256"] != source_hash:
        raise ValueError("RC model was fitted against a different building schema")
    if document["excitation"] != list(EXCITATION):
        raise ValueError("RC model uses an unknown excitation schedule")
    return document["models"]
