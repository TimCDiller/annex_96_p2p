"""Integration and performance tests against the REAL building stock.

Everything here needs CityLearn, PyTorch and the Annex 96 dataset, and the
whole module skips itself if they are not installed. That is deliberate: the
fast suite checks contracts on a stub, and anything that makes a claim about
how a strategy PERFORMS has to be measured on the real buildings, because the
stub has no physics worth the name.

Point it at your clone with the config, or with

    set ANNEX96_CE1=../annex96_common_exercise_1     (Windows)
    export ANNEX96_CE1=../annex96_common_exercise_1  (POSIX)

Expect it to take a couple of minutes: building the environment is ~30 s and
each timestep ~0.15 s.
"""
import os
import sys
from pathlib import Path

import pytest

from annex96p2p.config import Config
from annex96p2p.registry import DiscoveryReport, Participant, build_participants, \
    fill_unclaimed
from annex96p2p.runner import simulate
from annex96p2p.tariff import Tariff

ROOT = Path(__file__).resolve().parents[1]


def _locate():
    """Find a CE1 checkout, from the env, the config, or a sibling directory."""
    candidates = []
    if os.environ.get("ANNEX96_CE1"):
        candidates.append(Path(os.environ["ANNEX96_CE1"]))
    try:
        cfg = Config.load(str(ROOT / "config" / "default.yaml"))
        if cfg.citylearn_root:
            candidates.append(ROOT / cfg.citylearn_root)
    except Exception:                                            # noqa: BLE001
        pass
    candidates.append(ROOT.parent / "annex96_common_exercise_1")
    for c in candidates:
        schema = (Path(c) / "data" / "datasets" / "annex96_ce1_tx_neighborhood"
                  / "schema.json")
        if schema.exists():
            return Path(c).resolve(), schema.resolve()
    return None, None


CE1_ROOT, SCHEMA = _locate()


def _importable():
    """A dataset on disk is not enough -- CityLearn and torch must import."""
    if CE1_ROOT is None:
        return False
    if str(CE1_ROOT) not in sys.path:
        sys.path.insert(0, str(CE1_ROOT))
    try:
        import citylearn.citylearn  # noqa: F401
        return True
    except Exception:                                            # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _importable(),
    reason="CityLearn or the Annex 96 dataset is unavailable; "
           "set ANNEX96_CE1 and install torch to enable")


@pytest.fixture(scope="module")
def backend():
    if CE1_ROOT is not None:
        sys.path.insert(0, str(CE1_ROOT))
    from annex96p2p.backends.citylearn_backend import CityLearnBackend
    return CityLearnBackend(schema=str(SCHEMA), days=3, root=str(CE1_ROOT))


@pytest.fixture(scope="module")
def tariff(backend):
    return Tariff.synthetic(backend.n_timesteps, comfort_price=0.50)


# --------------------------------------------------------------------------
# the adapter contract -- these are the assumptions that were wrong first time
# --------------------------------------------------------------------------
def test_neighbourhood_shape(backend):
    assert len(backend.building_names) == 25
    assert backend.n_timesteps == 72          # days=3


def test_observations_are_populated(backend):
    obs = backend.observe()
    assert len(obs) == 25
    o = obs[backend.building_names[0]]
    assert o.comfort_low < o.comfort_high, "comfort band is inverted or empty"
    assert 10.0 < o.indoor_temperature < 40.0
    assert o.battery_capacity_kwh > 0 and o.battery_max_power_kw > 0
    assert o.hp_max_electric_kw > 0
    # get_cop(), not `efficiency` -- the latter is ~0.25 and is not a COP
    assert 2.0 < o.hp_cop < 8.0, "hp_cop looks like an efficiency, not a COP"


def test_battery_action_is_scaled_to_capacity(backend):
    """Our Action.battery is a fraction of POWER; CityLearn wants capacity."""
    for i, name in enumerate(backend.building_names[:5]):
        scale = backend._batt_scale[i]
        assert 0 < scale < 1, "%s: implausible battery scale %r" % (name, scale)


def test_the_cooling_action_actually_moves_the_temperature(backend):
    """Guards against a silently disconnected action mapping."""
    from annex96p2p.api import Action
    backend.reset()
    for _ in range(20):                       # clear the lookback=13 warmup
        backend.step({n: Action(hvac=0.0) for n in backend.building_names})
    warm = backend.observe()[backend.building_names[0]].indoor_temperature
    for _ in range(6):
        backend.step({n: Action(hvac=-1.0) for n in backend.building_names})
    cool = backend.observe()[backend.building_names[0]].indoor_temperature
    assert cool < warm - 0.5, ("full cooling did not lower the temperature "
                               "(%.2f -> %.2f)" % (warm, cool))


# --------------------------------------------------------------------------
# a whole run
# --------------------------------------------------------------------------
def test_run_balances_and_completes(backend, tariff):
    parts = fill_unclaimed([], backend.building_names, DiscoveryReport())
    result = simulate(backend, parts, tariff, call_timeout_seconds=10.0,
                      run_budget_seconds=600.0)
    assert result.ledger.check_balance() == []
    frame = result.ledger.to_frame()
    assert frame.timestep.nunique() == backend.n_timesteps
    for p in parts:
        assert p.failures == 0 and p.timeouts == 0


# --------------------------------------------------------------------------
# performance -- the only place a score assertion belongs
# --------------------------------------------------------------------------
@pytest.mark.parametrize("module,cls_name", [
    ("example_soc_spread", "SocSpreadAgent"),
    ("example_team", "ExampleAgent"),
])
def test_shipped_examples_beat_the_default_agent(backend, tariff, module, cls_name):
    """Every shipped template must beat the default agent on the REAL stock.

    This is the only place such a claim can honestly be made, and it has
    caught a real regression: example_team once bid its PASSIVE position and
    left the heat pump out, which is invisible on a stub with small
    compressors and cost it 1.15 here.
    """
    import importlib

    from annex96p2p.reference import reference_costs

    sys.path.insert(0, str(ROOT / "submissions"))
    agent_cls = getattr(importlib.import_module(module), cls_name)

    mine = backend.building_names[:5]
    parts = [Participant("student", agent_cls("student", mine), list(mine))]
    parts = fill_unclaimed(parts, backend.building_names, DiscoveryReport())

    result = simulate(backend, parts, tariff, call_timeout_seconds=10.0,
                      run_budget_seconds=600.0)
    ref = reference_costs(backend, parts, tariff)
    frame = result.ledger.to_frame()
    score = (frame[frame.participant == "student"].total_cost.sum()
             / ref["student"])

    student = next(p for p in parts if p.name == "student")
    assert student.failures == 0, "%s raised during the run" % module
    assert student.timeouts == 0, "%s timed out" % module
    assert score < 1.0, "%s lost to the default agent (%.3f)" % (module, score)


def test_submissions_folder_loads_cleanly(backend):
    """The shipped examples must claim buildings that actually exist.

    Building names here are ResStock ids, so a submission written against
    anything else silently controls nothing. Pin it.
    """
    participants, report = build_participants(backend, str(ROOT / "submissions"))
    assert not report.unknown_buildings, report.render()
    assert not report.skipped, report.render()
    assert not report.conflicts, report.render()
    # every building ends up owned, the unclaimed ones by the default agent
    owned = [b for p in participants for b in p.buildings]
    assert sorted(owned) == sorted(backend.building_names)
    assert len(owned) == len(set(owned)), "a building was claimed twice"
