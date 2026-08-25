"""The reference run, which is what makes scores comparable.

Raw bills cannot be compared between participants. A team with five
buildings will always pay more than a team with one, and a team that
happens to own a lot of PV will always pay less than one that does not.
Ranking on the raw bill would mostly rank the portfolio, not the agent.

So every participant is scored against the same portfolio run by the
built-in default agent:

    score = your total bill / the default agent's total bill
            for exactly the same buildings

    score < 1.00   you beat the default agent
    score = 1.00   you matched it
    score > 1.00   you did worse than doing nothing clever

This has three properties worth knowing:

  * it normalises portfolio size and portfolio quality away;
  * it is the same number whether a student runs alone on their laptop or
    the instructor runs everyone together, so a student can predict their
    own score before submitting;
  * it is not affected by how good or bad the other submissions are, which
    a raw ranking very much is.

The reference simulation puts the default agent in charge of every
participant, keeping the building groups identical. So the reference market
is an all-default market -- a fixed, deterministic opponent field.
"""
from __future__ import annotations

from typing import Dict, List

from .backends.base import Backend
from .baselines import BUILTIN_AGENTS, SimpleRBCAgent
from .registry import Participant
from .runner import RunResult, simulate
from .tariff import Tariff


def build_reference_participants(participants: List[Participant],
                                 agent_key: str = "rbc") -> List[Participant]:
    """Same names, same buildings, all driven by the default agent."""
    factory = BUILTIN_AGENTS.get(agent_key, SimpleRBCAgent)
    return [Participant(name=p.name,
                        agent=factory(name=p.name, buildings=list(p.buildings)),
                        buildings=list(p.buildings),
                        is_default=True)
            for p in participants]


def reference_costs(backend: Backend, participants: List[Participant],
                    tariff: Tariff, agent_key: str = "rbc",
                    max_bid_kwh: float = 500.0) -> Dict[str, float]:
    """Total bill each participant would have paid using the default agent."""
    mirror = build_reference_participants(participants, agent_key=agent_key)
    result = simulate(backend, mirror, tariff, max_bid_kwh=max_bid_kwh)
    board = result.ledger.leaderboard()
    if board.empty:
        return {}
    return dict(zip(board.participant, board.total_cost))


def attach(result: RunResult, costs: Dict[str, float]) -> RunResult:
    result.reference_costs = dict(costs)
    return result
