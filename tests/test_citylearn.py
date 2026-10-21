"""Integration and performance tests against the REAL building stock.

Everything here needs CityLearn, PyTorch and the Annex 96 dataset, and the
whole module skips itself if they are not installed. That is deliberate: the
fast suite checks contracts on a stub, and anything that makes a claim about
how a strategy PERFORMS has to be measured on the real buildings, because the
stub has no physics worth the name.

Point it at your clone with the config, or with

    set ANNEX96_CE1=../annex96_common_exercise_1     (Windows)
    export ANNEX96_CE1=../annex96_common_exercise_1  (POSIX)

Expect a few minutes: building the environment is ~30 s, each timestep ~0.15 s,
and the performance tests each run the simulation twice (once for the agent,
once for the all-default reference it is scored against).
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
        schema = (Path(c) / "data" / "datasets" / "annex96_ce1_vt_neighborhood"
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


#: Seven days, not two or three. Two separate warmups have to clear before a
#: score means anything: the LSTM dynamics have `lookback = 13`, so the
#: temperature is pinned for the first 13 timesteps whatever the agent does,
#: and an agent that classifies hours by price percentile needs a day or so of
#: history before it classifies anything. At three days example_team scores
#: above 1.0 purely because most of the run is warmup.
DAYS = 7


@pytest.fixture(scope="module")
def backend():
    if CE1_ROOT is not None:
        sys.path.insert(0, str(CE1_ROOT))
    from annex96p2p.backends.citylearn_backend import CityLearnBackend
    return CityLearnBackend(schema=str(SCHEMA), days=DAYS, root=str(CE1_ROOT))


@pytest.fixture(scope="module")
def tariff(backend):
    return Tariff.synthetic(backend.n_timesteps, comfort_price=0.50)


# --------------------------------------------------------------------------
# the adapter contract -- these are the assumptions that were wrong first time
# --------------------------------------------------------------------------
def test_neighbourhood_shape(backend):
    assert len(backend.building_names) == 25
    assert backend.n_timesteps == DAYS * 24


def test_observations_are_populated(backend):
    obs = backend.observe()
    assert len(obs) == 25
    o = obs[backend.building_names[0]]
    assert o.comfort_low < o.comfort_high, "comfort band is inverted or empty"
    assert 10.0 < o.indoor_temperature < 40.0
    assert o.battery_capacity_kwh > 0 and o.battery_max_power_kw > 0
    assert o.hp_max_electric_kw > 0
    # get_cop(), not `efficiency` -- the latter is ~0.25 and is not a COP.
    # The bound is loose on purpose: a heating COP at -21 degC is about 1.1,
    # barely better than a resistance heater, which is a large part of why the
    # Vermont case is so punishing. In the Texas cooling month it is ~4.3.
    assert 1.0 < o.hp_cop < 8.0,         "hp_cop = %.3f -- looks like an efficiency fraction, not a COP" % o.hp_cop


def test_battery_action_is_scaled_to_capacity(backend):
    """Our Action.battery is a fraction of POWER; CityLearn wants capacity."""
    for i, name in enumerate(backend.building_names[:5]):
        scale = backend._batt_scale[i]
        assert 0 < scale < 1, "%s: implausible battery scale %r" % (name, scale)


def test_the_hvac_action_actually_moves_the_temperature(backend):
    """Guards against a silently disconnected action mapping.

    Vermont actuates a HEATING device, so a positive hvac must warm the
    building. On Texas the sign is the other way round; this asserts whichever
    direction the dataset actuates.
    """
    from annex96p2p.api import Action
    heating = backend._hvac_kind[0] == "heating"
    drive = 1.0 if heating else -1.0

    backend.reset()
    for _ in range(20):                       # clear the lookback=13 warmup
        backend.step({n: Action(hvac=0.0) for n in backend.building_names})
    idle = backend.observe()[backend.building_names[0]].indoor_temperature
    for _ in range(8):
        backend.step({n: Action(hvac=drive) for n in backend.building_names})
    driven = backend.observe()[backend.building_names[0]].indoor_temperature

    if heating:
        assert driven > idle + 0.5, ("full heating did not warm the building "
                                     "(%.2f -> %.2f)" % (idle, driven))
    else:
        assert driven < idle - 0.5, ("full cooling did not cool the building "
                                     "(%.2f -> %.2f)" % (idle, driven))


def test_the_actuated_hvac_device_is_the_powerful_one(backend):
    """The vestigial device must not be the one we read power from.

    A Vermont house has 19.5 kW of heating and 0.09 kW of cooling. Reading the
    wrong one understates the load by two orders of magnitude.
    """
    obs = backend.observe()
    for name in backend.building_names[:5]:
        assert obs[name].hp_max_electric_kw > 1.0, (
            "%s: hp_max_electric_kw is %.3f -- almost certainly the vestigial "
            "device" % (name, obs[name].hp_max_electric_kw))


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
EXAMPLES = [
    ("example_team", "ExampleAgent"),
    ("example_flat50", "Flat50Agent"),
    ("example_inverse_soc", "InverseSocAgent"),
    ("example_surplus", "SurplusAgent"),
]


def _score_one(backend, tariff, module, cls_name):
    import importlib

    from annex96p2p.reference import reference_costs

    sys.path.insert(0, str(ROOT / "examples/original_submissions"))
    mod = importlib.import_module(module)
    agent_cls = getattr(mod, cls_name)
    # Each example on the buildings it actually declares, which is what a real
    # assignment looks like -- not all four on the same five.
    mine = [b for b in mod.BUILDINGS if b in backend.building_names]
    assert mine, "%s declares no buildings that exist in this dataset" % module
    parts = [Participant("student", agent_cls("student", mine), list(mine))]
    parts = fill_unclaimed(parts, backend.building_names, DiscoveryReport())
    result = simulate(backend, parts, tariff, call_timeout_seconds=10.0,
                      run_budget_seconds=900.0)
    ref = reference_costs(backend, parts, tariff)
    frame = result.ledger.to_frame()
    student = next(p for p in parts if p.name == "student")
    return (frame[frame.participant == "student"].total_cost.sum() / ref["student"],
            student)


@pytest.mark.parametrize("module,cls_name", EXAMPLES)
def test_every_example_runs_clean(backend, tariff, module, cls_name):
    """No example may raise or time out.

    This matters more than any score. A failing agent is silently replaced by
    the default for that timestep, so a plausible-looking leaderboard is not
    evidence that a submission ran -- example_team once raised on every one of
    168 timesteps while apparently scoring 0.93.
    """
    score, student = _score_one(backend, tariff, module, cls_name)
    assert student.failures == 0, "%s raised %d times" % (module, student.failures)
    assert student.timeouts == 0, "%s timed out %d times" % (module, student.timeouts)
    assert 0.5 < score < 1.5, "%s scored %.3f, which is implausible" % (module, score)


def test_at_least_one_example_beats_the_default(backend, tariff):
    """Not all of them do, and that is the interesting part.

    `example_inverse_soc` prices its battery as an arbitrage asset, which is
    right in a cooling month where the midday surplus is worth banking. In a
    Vermont January the battery is a pass-through, not a store: the building is
    in deficit almost every hour, so a rule that bids up to 95% of the grid
    price to refill an empty battery just buys expensive energy and pays the
    round-trip losses on it. It scores above 1.0 here. Whether that is a flaw
    in the agent or in the season is exactly the question to put to a student.
    """
    scores = {m: _score_one(backend, tariff, m, c)[0] for m, c in EXAMPLES}
    assert min(scores.values()) < 0.98, scores


def test_submissions_folder_loads_cleanly(backend):
    """The shipped examples must claim buildings that actually exist.

    Building names here are ResStock ids, so a submission written against
    anything else silently controls nothing. Pin it.
    """
    participants, report = build_participants(backend, str(ROOT / "examples/original_submissions"))
    assert not report.unknown_buildings, report.render()
    assert not report.skipped, report.render()
    assert not report.conflicts, report.render()
    # every building ends up owned, the unclaimed ones by the default agent
    owned = [b for p in participants for b in p.buildings]
    assert sorted(owned) == sorted(backend.building_names)
    assert len(owned) == len(set(owned)), "a building was claimed twice"
