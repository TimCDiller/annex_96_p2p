"""Contracts and optional real-device checks for the separate classroom stock."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from annex96p2p.api import Action, MarketInfo, TradeResult
from annex96p2p.altered_stock import make_participants
from annex96p2p.backends.citylearn_backend import CityLearnBackend
from annex96p2p.stock_agents import EvilAgent, StorageServiceAgent
from annex96p2p.stock_dataset import duck_prices, load_recipe, verify
from stub import StubBackend

ROOT = Path(__file__).resolve().parents[1]


def test_duck_tariff_has_two_peaks_and_a_midday_lull():
    recipe = load_recipe(ROOT / "scenarios/altered_stock.yaml")
    p = duck_prices(recipe, 48)
    assert len(p) == 48 and np.isfinite(p).all() and min(p) > 0
    assert p[7] > max(p[6], p[8])
    assert p[18] > max(p[17], p[19])
    assert p[13] < min(p[7], p[18], p[0])


def test_dominant_owner_has_exactly_thirty_percent_without_overlapping_claims():
    manifest = pd.DataFrame([dict(building="S%02d" % i, role="pv" if i%2==0 else "storage",
                                 potential_evil_owner=i//2 < 6) for i in range(20)])
    for mode in ("pv_honest","pv_withhold","storage_honest","storage_withhold","storage_flood"):
        parts = make_participants(manifest, mode)
        assert len(parts[0].buildings) == 6
        assert len(parts) == 15
        claims = [b for p in parts for b in p.buildings]
        assert len(claims) == len(set(claims)) == 20


def test_step_reads_current_battery_soc_not_unused_end_of_episode():
    b = SimpleNamespace(time_step=1, net_electricity_consumption=[1],
        indoor_dry_bulb_temperature=[22],
        electrical_storage=SimpleNamespace(capacity=80, soc=np.array([0, 0.25, 0, 0])))
    backend = CityLearnBackend.__new__(CityLearnBackend)
    backend.building_names = ["house"]
    backend._act_names = [["electrical_storage"]]
    backend._obs_names = [["indoor_dry_bulb_temperature_cooling_set_point", "comfort_band"]]
    backend._batt_scale = [0.25]
    backend._t = 0
    backend._last_net = {}
    backend.env = SimpleNamespace(buildings=[b], step=lambda actions: ([[22, 2]], 0, False, False, {}))
    assert backend.step({"house":Action()})["house"].battery_soc_kwh == 20


@pytest.mark.parametrize("behavior", ["honest","withhold","flood"])
def test_agents_keep_battery_actions_bounded_and_hold_unsold_energy(behavior):
    from dataclasses import replace
    from annex96p2p.market import build_price_grid
    o = replace(StubBackend().observe()["S01"], battery_capacity_kwh=80,
        battery_max_power_kw=80, battery_soc_kwh=70, base_load_kwh=0,
        solar_generation_kwh=0, indoor_temperature=23, hour=18)
    m = MarketInfo(0,18,0,0.45,0,build_price_grid(0.45),None,0)
    a = StorageServiceAgent("x",[o.name]) if behavior=="honest" else EvilAgent("x",[o.name],behavior)
    a.reset()
    bid = a.bid({o.name:o},m)
    assert np.isfinite(bid).all() and bid.min() < 0
    idle = a.dispatch({o.name:o},m,TradeResult(None,0))[o.name]
    assert idle.battery == pytest.approx(0)
    sold = a.dispatch({o.name:o},m,TradeResult(0.4,-1))[o.name]
    assert -1 <= sold.battery < 0


@pytest.fixture(scope="module")
def real_stock(tmp_path_factory):
    import shutil
    import sys
    upstream = ROOT.parent / "annex96_common_exercise_1"
    dataset = ROOT / "generated/altered_stock/balanced"
    if not (dataset / "schema.json").exists() or not upstream.exists():
        pytest.skip("Build the altered stock to enable real-device tests")
    sys.path.insert(0, str(upstream))
    try:
        import torch
        import citylearn.citylearn
    except ImportError:
        pytest.skip("Classroom runtime dependencies are not installed")
    torch.set_num_threads(1)
    relocated = tmp_path_factory.mktemp("portable") / "houses"
    shutil.copytree(dataset, relocated)
    verify(relocated)
    backend = CityLearnBackend(root=str(upstream), schema=str(relocated/"schema.json"), days=1)
    return backend, pd.read_csv(relocated / "buildings.csv")


def test_portable_schema_matches_equipment_manifest(real_stock):
    backend, manifest = real_stock
    assert len(backend.building_names) == 20
    assert (manifest.role == "pv").sum() == 10
    assert sorted(manifest[manifest.role=="storage"].c_rate.tolist()) == [0.25]*5 + [1.0]*5
    for row in manifest.itertuples():
        o = backend.observe()[row.building]
        assert o.battery_capacity_kwh == row.battery_kwh
        assert o.battery_max_power_kw == row.battery_kw
        i = backend.building_names.index(row.building)
        assert backend._encode(i, Action(battery=1))[0] == row.c_rate


@pytest.mark.parametrize("rate", [0.25,1.0])
def test_real_battery_delivers_requested_c_rate_and_accounts_for_losses(real_stock, rate):
    backend, manifest = real_stock
    backend.reset()
    row = manifest[(manifest.role=="storage") & (manifest.c_rate==rate)].iloc[0]
    name = row.building
    b = backend.env.buildings[backend.building_names.index(name)]
    charged = backend.step({name:Action(battery=1)})[name]
    assert b.electrical_storage.energy_balance[b.time_step] == pytest.approx(row.battery_kw, abs=1e-4)
    assert charged.battery_soc_kwh == pytest.approx(row.battery_kw*np.sqrt(0.9), abs=1e-4)
    emptied = backend.step({name:Action(battery=-1)})[name]
    assert -b.electrical_storage.energy_balance[b.time_step] == pytest.approx(row.battery_kw*0.9, abs=1e-4)
    assert emptied.battery_soc_kwh == pytest.approx(0, abs=1e-4)
