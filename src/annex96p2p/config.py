"""Run configuration, loaded from YAML."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class Config:
    # -- what to simulate ---------------------------------------------------
    backend: str = "mock"                  # "mock" or "citylearn"
    days: int = 7
    n_per_archetype: int = 3               # mock backend: 4 archetypes x this
    # Exact building count, overriding n_per_archetype. Set this to 25 to
    # match the real Annex 96 Common Exercise 1 neighbourhood, so a lab
    # designed on the mock backend transfers to CityLearn unchanged.
    n_buildings: Optional[int] = None
    citylearn_dataset: str = "annex96_ce1_tx_neighborhood"
    citylearn_schema: Optional[str] = None

    # -- who plays ----------------------------------------------------------
    submissions: str = "submissions"
    default_agent: str = "rbc"             # rbc | pricetaker | nothing
    unclaimed_mode: str = "individual"     # individual | pooled
    conflict_policy: str = "first"         # first | error
    assignments: Dict[str, List[str]] = field(default_factory=dict)
    strict_assignments: bool = False

    # -- prices -------------------------------------------------------------
    price_csv: Optional[str] = None        # optional real price series
    delivery_adder: float = 0.075          # $/kWh added to wholesale
    price_volatility: float = 0.15         # day-to-day multiplier amplitude
    export_price: float = 0.0              # 0.0 keeps the CE1 no-export rule
    comfort_price: float = 3.0             # $ per degC-hour outside the band
    price_step: float = 0.01               # bid price resolution

    # -- scoring ------------------------------------------------------------
    # Run a second, all-default simulation over the same building groups so
    # every participant can be scored as (my bill / default agent bill).
    # Without this, bills are not comparable across portfolio sizes.
    reference: bool = True
    reference_agent: str = "rbc"

    # -- guard rails --------------------------------------------------------
    max_bid_kwh: float = 500.0             # per price level, per participant

    # How long a single bid() or dispatch() call may take. Overrunning gets
    # you an EMPTY BID (or idle actions) for that timestep, and is flagged in
    # the report. 0 disables the timeout entirely.
    #
    # Set this generously: a torch forward pass is milliseconds, but the first
    # call of a run also pays for lazy imports and any model construction in
    # reset(). 2-5 s is roomy for inference; lower it for a graded tournament
    # once you know what legitimate submissions actually cost.
    call_timeout_seconds: float = 5.0

    # Total wall-clock seconds a single participant may spend across the WHOLE
    # run, summed over every call. Exhausting it disqualifies that participant
    # for the remainder: empty bids, idle actions, agent never invoked again.
    # This is what bounds a submission that hangs, because an abandoned thread
    # cannot be killed and would otherwise be re-entered every timestep.
    # 0 disables the budget.
    run_budget_seconds: float = 120.0

    # Softer than the timeout: calls slower than this are listed in the
    # report without being substituted.
    slow_call_seconds: float = 1.0
    seed: int = 0

    # -- output -------------------------------------------------------------
    out: str = "results"
    save_csv: bool = True

    @classmethod
    def load(cls, path: Optional[str]) -> "Config":
        if path is None:
            return cls()
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError("unknown config keys in %s: %s" % (path, sorted(unknown)))
        return cls(**data)

    def to_dict(self) -> dict:
        return asdict(self)
