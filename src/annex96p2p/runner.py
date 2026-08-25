"""The simulation loop.

One timestep, in order:

    1. each participant observes ONLY the buildings it controls
    2. each participant submits a bid vector                     bid()
    3. the market clears centrally, one uniform price            market.clear()
    4. each participant is told its own price and quantity, and dispatches
       its heat pumps and batteries                              dispatch()
    5. the physics advances one step
    6. everything is settled and written to the ledger

Step 2 before step 4 is the important part. The bid is a statement about
what you are willing to pay or accept; the dispatch then happens knowing
what you actually got. If dispatch ran first, the price could not influence
behaviour and the market would be pure book-keeping.

Participant code is called inside a guard. If a submission raises, the
default agent stands in for that participant for that timestep, the failure
is counted and reported, and the run continues.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .api import Action, BuildingObs, MarketInfo, TradeResult
from .backends.base import Backend
from .baselines import SimpleRBCAgent
from .ledger import Ledger
from .market import clear, validate_bid
from .registry import Participant
from .tariff import Tariff


@dataclass
class RunResult:
    ledger: Ledger
    participants: List[Participant]
    tariff: Tariff
    backend: Backend
    wall_seconds: float = 0.0
    slow_calls: List[str] = field(default_factory=list)
    #: participant -> bill the default agent would have paid for the same
    #: buildings. Filled in by reference.attach(); see reference.py.
    reference_costs: Dict[str, float] = field(default_factory=dict)

    @property
    def leaderboard(self):
        board = self.ledger.leaderboard()
        if board.empty or not self.reference_costs:
            return board
        board = board.copy()
        board["reference_cost"] = board.participant.map(self.reference_costs)
        board["score"] = board.total_cost / board.reference_cost
        # Rank on the normalised score, not on the raw bill.
        board = board.sort_values("score").reset_index(drop=True)
        board["rank"] = board.index + 1
        return board

    @property
    def summary(self) -> dict:
        return self.ledger.system_summary()


class _Guard:
    """Calls participant code, substituting the default agent on failure."""

    def __init__(self, ledger: Ledger, slow_call_seconds: float = 1.0):
        self.ledger = ledger
        self.slow_call_seconds = slow_call_seconds
        self.slow: List[str] = []
        self._fallback = SimpleRBCAgent(name="__fallback__", buildings=[])
        self._reported = set()

    def _note(self, participant: Participant, what: str) -> None:
        participant.failures += 1
        key = (participant.name, what)
        if key not in self._reported:          # report each failure mode once
            self._reported.add(key)
            self.ledger.warn("%s: %s failed, default agent substituted\n%s"
                             % (participant.name, what,
                                traceback.format_exc(limit=4)))

    def _timed(self, participant: Participant, what: str, fn, *args):
        started = time.perf_counter()
        value = fn(*args)
        elapsed = time.perf_counter() - started
        if elapsed > self.slow_call_seconds:
            self.slow.append("%s.%s took %.2fs at timestep %s"
                             % (participant.name, what, elapsed, args[-1]))
        return value

    def bid(self, participant: Participant, obs, mkt: MarketInfo,
            max_kwh: float) -> np.ndarray:
        try:
            raw = self._timed(participant, "bid", participant.agent.bid, obs, mkt)
        except Exception:                                        # noqa: BLE001
            self._note(participant, "bid")
            self._fallback.buildings = participant.buildings
            try:
                raw = self._fallback.bid(obs, mkt)
            except Exception:                                    # noqa: BLE001
                raw = mkt.empty_bid()
        array, notes = validate_bid(raw, mkt.n_levels, max_kwh, participant.name)
        for note in notes:
            self.ledger.warn(note)
        return array

    def dispatch(self, participant: Participant, obs, mkt: MarketInfo,
                 result: TradeResult) -> Dict[str, Action]:
        try:
            actions = self._timed(participant, "dispatch", participant.agent.dispatch,
                                  obs, mkt, result)
        except Exception:                                        # noqa: BLE001
            self._note(participant, "dispatch")
            self._fallback.buildings = participant.buildings
            try:
                actions = self._fallback.dispatch(obs, mkt, result)
            except Exception:                                    # noqa: BLE001
                actions = {}

        cleaned: Dict[str, Action] = {}
        if isinstance(actions, dict):
            for name in participant.buildings:
                candidate = actions.get(name)
                if isinstance(candidate, Action):
                    cleaned[name] = candidate
                elif isinstance(candidate, dict):
                    cleaned[name] = Action(hvac=float(candidate.get("hvac", 0.0)),
                                           battery=float(candidate.get("battery", 0.0)))
                else:
                    cleaned[name] = Action()
        else:
            self.ledger.warn("%s: dispatch did not return a dict, buildings idled"
                             % participant.name)
            cleaned = {name: Action() for name in participant.buildings}
        return cleaned


def simulate(backend: Backend, participants: List[Participant], tariff: Tariff,
             max_bid_kwh: float = 500.0, progress: bool = False,
             slow_call_seconds: float = 1.0) -> RunResult:
    """Run the whole simulation and return the ledger."""
    backend.reset()
    for participant in participants:
        try:
            participant.agent.reset()
        except Exception:                                        # noqa: BLE001
            pass

    ledger = Ledger()
    guard = _Guard(ledger, slow_call_seconds=slow_call_seconds)
    started = time.perf_counter()

    last_price: Optional[float] = None
    last_volume = 0.0

    for t in range(backend.n_timesteps):
        observations = backend.observe()
        any_obs = next(iter(observations.values()))
        hour, day = any_obs.hour, any_obs.day

        import_price = tariff.import_price(t)
        market_info = MarketInfo(
            timestep=t, hour=hour, day=day,
            grid_import_price=import_price,
            grid_export_price=tariff.export_price,
            price_grid=tariff.price_grid(t),
            last_clearing_price=last_price,
            last_clearing_volume=last_volume,
        )

        # -- 1 & 2: private observations, then bids ------------------------
        sliced: Dict[str, Dict[str, BuildingObs]] = {}
        bids: Dict[str, np.ndarray] = {}
        for participant in participants:
            # The information firewall: a participant is handed observations
            # for its own buildings and nothing else.
            own = {name: observations[name] for name in participant.buildings
                   if name in observations}
            sliced[participant.name] = own
            bids[participant.name] = guard.bid(participant, own, market_info,
                                               max_bid_kwh)

        # -- 3: central clearing -------------------------------------------
        clearing = clear(bids, market_info.price_grid)
        ledger.record_market(t, day, hour, clearing.price, clearing.volume,
                             import_price, len(participants))

        # -- 4: dispatch, now knowing your own trade -----------------------
        actions: Dict[str, Action] = {}
        for participant in participants:
            result = clearing.result_for(participant.name)
            actions.update(guard.dispatch(participant, sliced[participant.name],
                                          market_info, result))

        # -- 5: physics ----------------------------------------------------
        outcomes = backend.step(actions)

        # -- 6: settlement -------------------------------------------------
        for participant in participants:
            net = sum(outcomes[b].net_kwh for b in participant.buildings
                      if b in outcomes)
            degree_hours = sum(outcomes[b].degree_hours for b in participant.buildings
                               if b in outcomes)
            ledger.settle(
                timestep=t, day=day, hour=hour, participant=participant.name,
                is_default=participant.is_default, net_kwh=net,
                p2p_kwh=clearing.quantities.get(participant.name, 0.0),
                clearing_price=clearing.price, grid_import_price=import_price,
                grid_export_price=tariff.export_price, degree_hours=degree_hours,
                comfort_price=tariff.comfort_price,
            )

        last_price, last_volume = clearing.price, clearing.volume

        if progress and (t + 1) % 24 == 0:
            print("  day %d/%d done" % ((t + 1) // 24, backend.n_timesteps // 24))

    return RunResult(ledger=ledger, participants=participants, tariff=tariff,
                     backend=backend, wall_seconds=time.perf_counter() - started,
                     slow_calls=guard.slow)
