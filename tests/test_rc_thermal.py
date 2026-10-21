"""Check the published excitation, model provenance, and controller bounds."""
from dataclasses import replace
import json

import numpy as np
import pytest

from annex96p2p.api import MarketInfo
from annex96p2p.market import build_price_grid
from annex96p2p.rc_agents import RCPreheatAgent
from annex96p2p.rc_thermal import EXCITATION, excitation, load_models, schema_fingerprint
from stub import StubBackend


def test_excitation_is_predefined_and_building_specific():
    assert tuple(excitation(t, 0) for t in range(8)) == EXCITATION
    assert tuple(excitation(t, 1) for t in range(8)) != EXCITATION
    assert all(abs(excitation(t, i)) <= .15 for t in range(24) for i in range(20))


def test_model_rejects_different_schema(tmp_path):
    schema = tmp_path / "schema.json"
    schema.write_text('{"one": 1, "two": 2}')
    model = tmp_path / "model.json"
    model.write_text(json.dumps(dict(source_schema_sha256=schema_fingerprint(schema),
        excitation=list(EXCITATION), models={"building":{}})))
    assert "building" in load_models(model, schema)
    schema.write_text('{"two":2,"one":1}')
    assert "building" in load_models(model, schema)
    schema.write_text('{"two":2,"one":3}')
    with pytest.raises(ValueError, match="different building schema"):
        load_models(model, schema)


def test_rc_preheat_is_bounded_and_uses_thermostat_for_poor_fit():
    original = StubBackend().observe()["S01"]
    o = replace(original, timestep=182, hour=14, indoor_temperature=20,
        comfort_low=18, comfort_high=22, outdoor_temperature=10,
        hp_max_electric_kw=10, hp_cop=2, hp_is_heating=True)
    shape = [.14,.13,.12,.11,.12,.15,.24,.34,.30,.20,.12,.08,
             .06,.055,.07,.12,.22,.36,.45,.40,.29,.22,.18,.16]
    m = MarketInfo(182,14,7,.07,0,build_price_grid(.07),None,0)
    model = dict(coefficients=[.1,.1,0,0,0,0,0], validation_mae_c=.5)
    agent = RCPreheatAgent("x",[o.name],{o.name:model},shape)
    agent.reset()
    command = agent.heat_command(o,m)
    assert np.isfinite(command) and 0 <= command <= 1
    assert agent.thermal_log[-1]["preheat"]
    assert command > agent.thermal_log[-1]["baseline_hvac"]
    assert command-agent.thermal_log[-1]["baseline_hvac"] <= .10001
    poor = RCPreheatAgent("x",[o.name],{o.name:dict(model, validation_mae_c=2.0)},shape)
    poor.reset()
    assert poor.heat_command(o,m) == poor.thermostat(o)
    assert not poor.thermal_log


def test_rc_releases_heat_at_a_peak_only_with_predicted_comfort_margin():
    original = StubBackend().observe()["S01"]
    o = replace(original, timestep=186, hour=18, indoor_temperature=20,
        comfort_low=18, comfort_high=22, outdoor_temperature=10,
        hp_max_electric_kw=10, hp_cop=2, hp_is_heating=True)
    shape = [.14,.13,.12,.11,.12,.15,.24,.34,.30,.20,.12,.08,
             .06,.055,.07,.12,.22,.36,.45,.40,.29,.22,.18,.16]
    m = MarketInfo(186,18,7,.45,0,build_price_grid(.45),None,0)
    model = dict(coefficients=[.1,.1,0,0,0,0,0], validation_mae_c=.5)
    agent = RCPreheatAgent("x",[o.name],{o.name:model},shape)
    agent.reset()
    agent.thermostat._acc[o.name] = 10.0
    command = agent.heat_command(o,m)
    baseline = agent.thermal_log[-1]["baseline_hvac"]
    assert 0 <= command < baseline
    assert baseline-command <= .10001
    assert agent.thermal_log[-1]["predicted_next_c"] >= o.comfort_low+.6


def test_advance_warming_adds_heat_when_model_predicts_headroom():
    original = StubBackend().observe()["S01"]
    o = replace(original, timestep=182, hour=14, indoor_temperature=20,
        comfort_low=18, comfort_high=22, outdoor_temperature=10,
        hp_max_electric_kw=10, hp_cop=2, hp_is_heating=True)
    shape = [.14,.13,.12,.11,.12,.15,.24,.34,.30,.20,.12,.08,
             .06,.055,.07,.12,.22,.36,.45,.40,.29,.22,.18,.16]
    m = MarketInfo(182,14,7,.07,0,build_price_grid(.07),None,0)
    model = dict(coefficients=[.1,.25,0,0,0,0,0], validation_mae_c=.5)
    no = RCPreheatAgent("x",[o.name],{o.name:model},shape,enable_preheat=False)
    yes = RCPreheatAgent("x",[o.name],{o.name:model},shape,enable_preheat=True)
    no.reset()
    yes.reset()
    no_heat = no.heat_command(o,m)
    yes_heat = yes.heat_command(o,m)
    assert yes.thermal_log[-1]["preheat"]
    assert not no.thermal_log[-1]["preheat"]
    assert no_heat < yes_heat <= no_heat+.1
