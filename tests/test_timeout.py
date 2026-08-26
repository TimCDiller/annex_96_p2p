"""The per-call timeout and the per-run compute budget."""
import time

import numpy as np
import pytest

from annex96p2p import Action, Agent
from annex96p2p.backends.mock import MockBackend, default_neighbourhood
from annex96p2p.baselines import SimpleRBCAgent
from annex96p2p.registry import DiscoveryReport, Participant, fill_unclaimed
from annex96p2p.runner import simulate
from annex96p2p.tariff import Tariff


class Sleeper(Agent):
    """Overruns the per-call timeout every time it bids."""

    delay = 0.30

    def bid(self, obs, mkt):
        time.sleep(self.delay)
        # If this ever reached the market it would be a big buy order.
        bid = mkt.new_bid()
        bid.buy(mkt.grid_import_price - 0.01, 50.0)
        return bid.array

    def dispatch(self, obs, mkt, result):
        return {n: Action() for n in obs}


class Hog(Agent):
    """Burns real CPU, to exercise the per-run budget."""

    def bid(self, obs, mkt):
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 0.05:
            pass
        return mkt.empty_bid()

    def dispatch(self, obs, mkt, result):
        return {n: Action() for n in obs}


def build(agent_cls, days=1, buildings=("B01", "B02")):
    backend = MockBackend(n_days=days, specs=default_neighbourhood(1))
    tariff = Tariff.synthetic(backend.n_timesteps)
    mine = [b for b in buildings if b in backend.building_names]
    parts = [Participant("victim", agent_cls("victim", mine), mine)]
    parts = fill_unclaimed(parts, backend.building_names, DiscoveryReport())
    return backend, tariff, parts


# --------------------------------------------------------------------------
# per-call timeout
# --------------------------------------------------------------------------
def test_slow_bid_times_out_and_is_flagged():
    backend, tariff, parts = build(Sleeper)
    result = simulate(backend, parts, tariff, call_timeout_seconds=0.05,
                      run_budget_seconds=0.0)
    victim = next(p for p in parts if p.name == "victim")
    assert victim.timeouts > 0, "the slow bid was not timed out"
    assert any("call timeout" in w for w in result.ledger.warnings)
    assert not result.ledger.to_frame().empty, "the run did not complete"


def test_timed_out_bid_is_empty_not_the_default_agents():
    """A timeout must not be rescued into a real bid."""
    backend, tariff, parts = build(Sleeper)
    result = simulate(backend, parts, tariff, call_timeout_seconds=0.05)
    frame = result.ledger.to_frame()
    victim = frame[frame.participant == "victim"]
    # An empty bid can never clear, so the participant never trades.
    assert victim.p2p_kwh.abs().max() < 1e-9, "a timed-out agent still traded"


def test_timeout_disabled_lets_the_slow_call_through():
    backend, tariff, parts = build(Sleeper)
    Sleeper.delay = 0.01           # keep the test quick
    try:
        result = simulate(backend, parts, tariff, call_timeout_seconds=0.0)
    finally:
        Sleeper.delay = 0.30
    victim = next(p for p in parts if p.name == "victim")
    assert victim.timeouts == 0
    assert victim.seconds > 0, "compute time was not accounted"


def test_a_fast_agent_is_never_timed_out():
    backend, tariff, parts = build(SimpleRBCAgent, days=2)
    result = simulate(backend, parts, tariff, call_timeout_seconds=2.0,
                      run_budget_seconds=60.0)
    for p in parts:
        assert p.timeouts == 0
        assert not p.disqualified
    assert result.ledger.check_balance() == []


# --------------------------------------------------------------------------
# per-run budget
# --------------------------------------------------------------------------
def test_run_budget_disqualifies_and_the_run_completes():
    backend, tariff, parts = build(Hog, days=1)
    result = simulate(backend, parts, tariff, call_timeout_seconds=0.0,
                      run_budget_seconds=0.20)
    victim = next(p for p in parts if p.name == "victim")
    assert victim.disqualified, "the budget was not enforced"
    assert victim.seconds > 0.20
    assert any("DISQUALIFIED" in w for w in result.ledger.warnings)
    # 24 timesteps still settled, so the tournament was not derailed
    assert result.ledger.to_frame().timestep.nunique() == backend.n_timesteps


def test_disqualified_agent_stops_being_called():
    calls = {"n": 0}

    class Counter(Hog):
        def bid(self, obs, mkt):
            calls["n"] += 1
            return Hog.bid(self, obs, mkt)

    backend, tariff, parts = build(Counter, days=1)
    simulate(backend, parts, tariff, run_budget_seconds=0.15,
             call_timeout_seconds=0.0)
    # It must be invoked far fewer times than there are timesteps.
    assert calls["n"] < backend.n_timesteps, calls["n"]


def test_budget_is_per_participant_not_shared():
    backend, tariff, parts = build(Hog, days=1)
    simulate(backend, parts, tariff, run_budget_seconds=0.15,
             call_timeout_seconds=0.0)
    victim = next(p for p in parts if p.name == "victim")
    others = [p for p in parts if p.name != "victim"]
    assert victim.disqualified
    assert not any(p.disqualified for p in others), \
        "a well-behaved participant was punished for someone else's compute"


def test_compute_is_reported():
    from annex96p2p.report import compute_text
    backend, tariff, parts = build(Hog, days=1)
    result = simulate(backend, parts, tariff, run_budget_seconds=0.15)
    text = compute_text(result)
    assert "victim" in text and "DISQUALIFIED" in text


# --------------------------------------------------------------------------
# a raise is still handled differently from a timeout
# --------------------------------------------------------------------------
def test_raising_still_gets_the_default_agent_not_an_empty_bid():
    class Boom(Agent):
        def bid(self, obs, mkt):
            raise RuntimeError("boom")

        def dispatch(self, obs, mkt, result):
            return {n: Action() for n in obs}

    backend, tariff, parts = build(Boom, days=1)
    result = simulate(backend, parts, tariff, call_timeout_seconds=1.0)
    victim = next(p for p in parts if p.name == "victim")
    assert victim.failures > 0
    assert victim.timeouts == 0, "a raise was miscounted as a timeout"
