"""The fields in agents/fields.py, and native solvers inside the runner."""
import threading
from pathlib import Path

import numpy as np
import pytest

from annex96p2p import Action, Agent
from annex96p2p.fields import field_participants, load_fields
from annex96p2p.registry import DiscoveryReport, Participant, fill_unclaimed
from annex96p2p.runner import simulate
from annex96p2p.tariff import Tariff
from stub import StubBackend

AGENTS = Path(__file__).resolve().parents[1] / "agents"
STOCK = ["resstock-amy2018-2021-release-1-%s" % i for i in (
    "112208", "179247", "20199", "223581", "247942", "376570", "409896", "425540", "460412",
    "481052", "199613", "245723", "408344", "4421", "485614", "147002", "216895", "319918",
    "411001", "467125")]


@pytest.mark.parametrize("field", ["default", "basic", "advanced", "diverse"])
def test_every_field_loads_onto_distinct_existing_houses(field):
    parts = field_participants(AGENTS, field, STOCK)
    claimed = [b for p in parts for b in p.buildings]
    assert len(claimed) == len(set(claimed))
    assert set(claimed) <= set(STOCK)
    assert all(not p.is_default for p in parts)
    if field == "advanced":
        assert set(claimed) == set(STOCK), "the advanced field covers every house"


def test_the_field_steps_off_claimed_houses():
    mine = STOCK[:2]                         # two of team_opt_hp's five houses
    parts = field_participants(AGENTS, "advanced", STOCK, taken=mine)
    claimed = {b for p in parts for b in p.buildings}
    assert not claimed & set(mine)
    portfolio = next(p for p in parts if p.name == "team_opt_hp")
    assert len(portfolio.buildings) == 3


def test_an_unknown_field_is_an_error():
    with pytest.raises(ValueError, match="unknown field"):
        field_participants(AGENTS, "nonsense", STOCK)
    assert {"default", "basic", "advanced", "diverse"} <= set(load_fields(AGENTS))


class LinprogAgent(Agent):
    """Solves a small LP in every call, as MPC submissions do."""

    threads = set()

    def bid(self, obs, mkt):
        from scipy.optimize import linprog
        LinprogAgent.threads.add((self.name, threading.get_ident()))
        n = 30
        res = linprog(np.linspace(1, 2, n), A_ub=-np.eye(n), b_ub=-np.ones(n),
                      bounds=[(0, 5)] * n, method="highs")
        assert res.status == 0
        return mkt.empty_bid()

    def dispatch(self, obs, mkt, result):
        return {n: Action() for n in obs}


def test_highs_runs_on_one_thread_per_participant():
    """Regression: SciPy's HiGHS crashed the process (access violation, Windows)
    when the runner called each agent on a fresh thread every hour. Each
    participant now keeps one worker thread for the whole run."""
    pytest.importorskip("scipy")
    LinprogAgent.threads = set()
    backend = StubBackend(n_buildings=4, days=15)
    names = backend.building_names
    parts = [Participant("lp_a", LinprogAgent("lp_a", names[:1]), names[:1]),
             Participant("lp_b", LinprogAgent("lp_b", names[1:2]), names[1:2])]
    parts = fill_unclaimed(parts, names, DiscoveryReport())
    simulate(backend, parts, Tariff.synthetic(backend.n_timesteps),
             call_timeout_seconds=5.0, progress=False)
    assert all(p.failures == 0 for p in parts)
    assert sorted(name for name, _ in LinprogAgent.threads) == ["lp_a", "lp_b"]
