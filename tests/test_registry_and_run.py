"""Submission discovery, the information firewall, and end-to-end runs.

If these pass, your checkout is working. Run them before you start:

    python -m pytest
"""
import textwrap

import numpy as np
import pytest

from annex96p2p import Action, Agent
from stub import StubBackend
from annex96p2p.baselines import SimpleRBCAgent
from annex96p2p.registry import (SubmissionError, build_participants, discover,
                                 fill_unclaimed, resolve_conflicts)
from annex96p2p.runner import simulate
from annex96p2p.tariff import Tariff

SUBMISSION = textwrap.dedent('''
    from annex96p2p import Action, Agent

    BUILDINGS = {buildings}
    NAME = "{name}"

    class A(Agent):
        def bid(self, obs, mkt):
            return mkt.empty_bid()
        def dispatch(self, obs, mkt, result):
            return {{n: Action() for n in obs}}

    AGENT = A
''')


def write(folder, filename, buildings, name):
    path = folder / filename
    path.write_text(SUBMISSION.format(buildings=buildings, name=name),
                    encoding="utf-8")
    return path


@pytest.fixture
def backend():
    return StubBackend(n_buildings=4, days=1)   # 4 buildings


@pytest.fixture
def tariff(backend):
    return Tariff.synthetic(backend.n_timesteps)


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------
def test_loads_a_submission(tmp_path, backend):
    write(tmp_path, "team_a.py", '["S01"]', "team_a")
    found, report = discover(tmp_path, backend.building_names)
    assert [p.name for p in found] == ["team_a"]
    assert found[0].buildings == ["S01"]


def test_underscore_files_are_ignored(tmp_path, backend):
    write(tmp_path, "_scratch.py", '["S01"]', "scratch")
    found, _ = discover(tmp_path, backend.building_names)
    assert not found


def test_broken_submission_is_skipped_not_fatal(tmp_path, backend):
    (tmp_path / "broken.py").write_text("this is not python(", encoding="utf-8")
    write(tmp_path, "team_a.py", '["S01"]', "team_a")
    found, report = discover(tmp_path, backend.building_names)
    assert [p.name for p in found] == ["team_a"]
    assert any("broken.py" in name for name, _ in report.skipped)


def test_missing_agent_is_skipped(tmp_path, backend):
    (tmp_path / "noagent.py").write_text('BUILDINGS = ["S01"]', encoding="utf-8")
    found, report = discover(tmp_path, backend.building_names)
    assert not found
    assert report.skipped


def test_unknown_building_is_dropped(tmp_path, backend):
    write(tmp_path, "team_a.py", '["S01", "S99"]', "team_a")
    found, report = discover(tmp_path, backend.building_names)
    assert found[0].buildings == ["S01"]
    assert report.unknown_buildings


# --------------------------------------------------------------------------
# claims
# --------------------------------------------------------------------------
def test_double_claim_first_wins(tmp_path, backend):
    write(tmp_path, "a_team.py", '["S01", "S02"]', "a_team")
    write(tmp_path, "b_team.py", '["S02", "S03"]', "b_team")
    found, report = discover(tmp_path, backend.building_names)
    kept = resolve_conflicts(found, report, policy="first")
    owned = {b for p in kept for b in p.buildings}
    assert len(owned) == 3                      # nobody owns S02 twice
    assert report.conflicts


def test_double_claim_can_be_fatal_for_a_tournament(tmp_path, backend):
    write(tmp_path, "a_team.py", '["S01"]', "a_team")
    write(tmp_path, "b_team.py", '["S01"]', "b_team")
    found, report = discover(tmp_path, backend.building_names)
    with pytest.raises(SubmissionError):
        resolve_conflicts(found, report, policy="error")


def test_unclaimed_buildings_get_the_default_agent(tmp_path, backend):
    write(tmp_path, "team_a.py", '["S01"]', "team_a")
    participants, report = build_participants(backend, tmp_path)
    owned = {b for p in participants for b in p.buildings}
    assert owned == set(backend.building_names)     # every building has an owner
    assert len(report.unclaimed) == len(backend.building_names) - 1


def test_no_submissions_at_all_still_runs(tmp_path, backend, tariff):
    participants, _ = build_participants(backend, tmp_path)
    assert all(p.is_default for p in participants)
    result = simulate(backend, participants, tariff)
    assert not result.ledger.to_frame().empty


def test_assignments_can_be_enforced(tmp_path, backend):
    write(tmp_path, "team_a.py", '["S01", "S02"]', "team_a")
    with pytest.raises(SubmissionError):
        build_participants(backend, tmp_path,
                           assignments={"team_a": ["S01"]},
                           strict_assignments=True)


def test_unknown_team_name_cannot_escape_its_assignment(tmp_path, backend):
    """Renaming yourself must not be a way out of the assignment table."""
    write(tmp_path, "team_a.py", '["S01", "S02", "S03"]', "not_on_the_list")
    with pytest.raises(SubmissionError):
        build_participants(backend, tmp_path,
                           assignments={"team_a": ["S01"]},
                           strict_assignments=True)


def test_unknown_team_name_is_only_a_warning_when_not_strict(tmp_path, backend):
    """A student experimenting locally should not be blocked by this."""
    write(tmp_path, "team_a.py", '["S01"]', "not_on_the_list")
    participants, report = build_participants(
        backend, tmp_path, assignments={"team_a": ["S01"]},
        strict_assignments=False)
    assert "not_on_the_list" in [p.name for p in participants]
    assert report.conflicts


# --------------------------------------------------------------------------
# the information firewall
# --------------------------------------------------------------------------
def test_agent_only_sees_its_own_buildings(backend, tariff):
    seen = {}

    class Spy(Agent):
        def bid(self, obs, mkt):
            seen["bid"] = set(obs)
            return mkt.empty_bid()

        def dispatch(self, obs, mkt, result):
            seen["dispatch"] = set(obs)
            return {n: Action() for n in obs}

    from annex96p2p.registry import Participant
    mine = ["S01", "S02"]
    participants = [Participant("spy", Spy("spy", mine), mine)]
    participants = fill_unclaimed(participants, backend.building_names,
                                  __import__("annex96p2p.registry", fromlist=["x"])
                                  .DiscoveryReport())
    simulate(backend, participants, tariff)

    assert seen["bid"] == set(mine)
    assert seen["dispatch"] == set(mine)


def test_agent_sees_only_last_step_clearing(backend, tariff):
    fields = {}

    class Spy(Agent):
        def bid(self, obs, mkt):
            fields["keys"] = set(vars(mkt))
            return mkt.empty_bid()

        def dispatch(self, obs, mkt, result):
            return {n: Action() for n in obs}

    from annex96p2p.registry import DiscoveryReport, Participant
    participants = fill_unclaimed([Participant("spy", Spy("spy", ["S01"]), ["S01"])],
                                  backend.building_names, DiscoveryReport())
    simulate(backend, participants, tariff)

    # Nothing about other participants, current bids, or the order book.
    assert "last_clearing_price" in fields["keys"]
    assert not any(k in fields["keys"] for k in
                   ("bids", "participants", "quantities", "order_book"))


# --------------------------------------------------------------------------
# robustness: a crashing submission must not take down the run
# --------------------------------------------------------------------------
def test_crashing_agent_is_substituted(backend, tariff):
    class Exploder(Agent):
        def bid(self, obs, mkt):
            raise RuntimeError("boom")

        def dispatch(self, obs, mkt, result):
            raise RuntimeError("boom")

    from annex96p2p.registry import DiscoveryReport, Participant
    bad = Participant("bad", Exploder("bad", ["S01"]), ["S01"])
    participants = fill_unclaimed([bad], backend.building_names, DiscoveryReport())

    result = simulate(backend, participants, tariff)

    assert bad.failures > 0
    assert result.ledger.warnings
    assert not result.ledger.to_frame().empty        # the run still completed


def test_agent_returning_junk_is_survivable(backend, tariff):
    class Junk(Agent):
        def bid(self, obs, mkt):
            return "not a bid"

        def dispatch(self, obs, mkt, result):
            return 42

    from annex96p2p.registry import DiscoveryReport, Participant
    participants = fill_unclaimed([Participant("junk", Junk("junk", ["S01"]), ["S01"])],
                                  backend.building_names, DiscoveryReport())
    result = simulate(backend, participants, tariff)
    assert not result.ledger.to_frame().empty


# --------------------------------------------------------------------------
# invariants of a whole run
# --------------------------------------------------------------------------
def test_market_balances_every_timestep(tmp_path, tariff):
    backend = StubBackend(n_buildings=8, days=3)
    tariff = Tariff.synthetic(backend.n_timesteps)
    participants, _ = build_participants(backend, tmp_path)
    result = simulate(backend, participants, tariff)
    assert result.ledger.check_balance() == []


def test_runs_are_deterministic(tmp_path, tariff):
    def once():
        backend = StubBackend(n_buildings=8, days=2)
        t = Tariff.synthetic(backend.n_timesteps)
        participants, _ = build_participants(backend, tmp_path)
        return simulate(backend, participants, t).ledger.system_summary()["total_bill"]

    assert once() == pytest.approx(once())


