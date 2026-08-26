"""Finding submissions and deciding who controls which buildings.

Drop a `.py` file in `submissions/`. It must define two module-level names:

    BUILDINGS = ["B01", "B02", "B03"]     # the buildings you control
    AGENT = MyAgent                       # your Agent subclass

Optionally:

    NAME = "team_kappa"                   # defaults to the file name

Every building that no submission claims is run by the built-in default
agent, one participant per unclaimed building. That is what makes the same
code work in both modes:

  * a student clones the repo, writes one file, and immediately has a
    market full of default opponents to trade against;
  * the instructor drops every submitted file into the same folder and the
    market fills up with real participants instead.

Files whose name starts with `_` are ignored, so `_scratch.py` is a safe
place to keep experiments.
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .api import Agent
from .baselines import BUILTIN_AGENTS, SimpleRBCAgent


class SubmissionError(Exception):
    """A submission is malformed in a way that cannot be repaired."""


@dataclass
class Participant:
    """One market participant: a name, an agent, and the buildings it owns."""

    name: str
    agent: Agent
    buildings: List[str]
    is_default: bool = False
    source: Optional[str] = None
    #: calls that raised and were replaced by the default agent
    failures: int = 0
    #: calls that overran `call_timeout_seconds` and got an empty bid
    timeouts: int = 0
    #: cumulative wall-clock seconds spent inside this participant's code
    seconds: float = 0.0
    #: set when `run_budget_seconds` is exhausted; no further calls are made
    disqualified: bool = False

    def __repr__(self) -> str:
        kind = "default" if self.is_default else "submission"
        return "Participant(%s, %s, %d buildings)" % (self.name, kind, len(self.buildings))


@dataclass
class DiscoveryReport:
    """What happened while loading `submissions/`."""

    loaded: List[str] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    unknown_buildings: List[str] = field(default_factory=list)
    unclaimed: List[str] = field(default_factory=list)

    def render(self) -> str:
        lines = []
        if self.loaded:
            lines.append("loaded submissions: " + ", ".join(sorted(self.loaded)))
        for name, why in self.skipped:
            lines.append("SKIPPED %s: %s" % (name, why))
        for message in self.conflicts:
            lines.append("CONFLICT " + message)
        for message in self.unknown_buildings:
            lines.append("UNKNOWN BUILDING " + message)
        if self.unclaimed:
            lines.append("unclaimed buildings run by the default agent: %d (%s)"
                         % (len(self.unclaimed), ", ".join(self.unclaimed)))
        return "\n".join(lines)


def _load_module(path: Path):
    """Import a submission file in isolation, without polluting sys.path."""
    module_name = "annex96_submission_" + path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise SubmissionError("cannot import %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _instantiate(agent_obj, name: str, buildings: Sequence[str]) -> Agent:
    """Accept either an Agent subclass or an already-built instance."""
    if isinstance(agent_obj, Agent):
        agent_obj.name = name
        agent_obj.buildings = list(buildings)
        return agent_obj
    if isinstance(agent_obj, type) and issubclass(agent_obj, Agent):
        return agent_obj(name=name, buildings=list(buildings))
    if callable(agent_obj):
        built = agent_obj(name=name, buildings=list(buildings))
        if isinstance(built, Agent):
            return built
    raise SubmissionError("AGENT must be an Agent subclass or instance, got %r"
                          % type(agent_obj))


def discover(folder, valid_buildings: Sequence[str],
             report: Optional[DiscoveryReport] = None) -> Tuple[List[Participant], DiscoveryReport]:
    """Load every submission in `folder`.

    Malformed submissions are skipped with an explanation rather than
    aborting the run, so one bad file cannot break a tournament.
    """
    report = report or DiscoveryReport()
    participants: List[Participant] = []
    folder = Path(folder)
    if not folder.exists():
        return participants, report

    valid = set(valid_buildings)

    # So load_weights() can find checkpoints sitting next to the submissions.
    from .weights import register_search_dir
    register_search_dir(folder)

    for path in sorted(folder.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        try:
            module = _load_module(path)
        except Exception:                                        # noqa: BLE001
            report.skipped.append((path.name, "import failed:\n"
                                   + traceback.format_exc(limit=3)))
            continue

        buildings = getattr(module, "BUILDINGS", None)
        agent_obj = getattr(module, "AGENT", None)
        name = str(getattr(module, "NAME", path.stem))

        if agent_obj is None:
            report.skipped.append((path.name, "no module-level AGENT defined"))
            continue
        if not buildings:
            report.skipped.append((path.name, "no module-level BUILDINGS defined"))
            continue

        claimed = [str(b) for b in buildings]
        missing = [b for b in claimed if b not in valid]
        if missing:
            report.unknown_buildings.append(
                "%s claims %s, which do not exist" % (name, missing))
            claimed = [b for b in claimed if b in valid]
        if not claimed:
            report.skipped.append((path.name, "claims no existing buildings"))
            continue

        try:
            agent = _instantiate(agent_obj, name, claimed)
        except Exception as exc:                                 # noqa: BLE001
            report.skipped.append((path.name, "could not construct AGENT: %s" % exc))
            continue

        participants.append(Participant(name=name, agent=agent, buildings=claimed,
                                        is_default=False, source=str(path)))
        report.loaded.append(name)

    return participants, report


def resolve_conflicts(participants: List[Participant],
                      report: DiscoveryReport,
                      policy: str = "first") -> List[Participant]:
    """Handle two submissions claiming the same building.

    policy "first"  the earlier submission (alphabetically) keeps it, the
                    later one loses that building. Good for local runs.
    policy "error"  raise. Use this for the central tournament, where an
                    overlapping claim means the partition is wrong.
    """
    owner: Dict[str, str] = {}
    for participant in participants:
        kept = []
        for building in participant.buildings:
            if building in owner:
                report.conflicts.append(
                    "%s is claimed by both %s and %s" % (building, owner[building],
                                                         participant.name))
                continue
            owner[building] = participant.name
            kept.append(building)
        participant.buildings = kept

    if report.conflicts and policy == "error":
        raise SubmissionError("overlapping building claims:\n  "
                              + "\n  ".join(report.conflicts))

    return [p for p in participants if p.buildings]


def fill_unclaimed(participants: List[Participant], all_buildings: Sequence[str],
                   report: DiscoveryReport, default_agent: str = "rbc",
                   mode: str = "individual") -> List[Participant]:
    """Give every unclaimed building to the built-in default agent.

    mode "individual"  each unclaimed building becomes its own participant.
                       This is the default: it makes the market deep and
                       means a single student still faces many counterparties.
    mode "pooled"      all unclaimed buildings become one participant, which
                       nets internally first and so trades much less.
    """
    claimed = {b for p in participants for b in p.buildings}
    unclaimed = [b for b in all_buildings if b not in claimed]
    report.unclaimed = list(unclaimed)
    if not unclaimed:
        return participants

    factory: Callable[..., Agent] = BUILTIN_AGENTS.get(default_agent, SimpleRBCAgent)

    if mode == "pooled":
        name = "default_pool"
        participants.append(Participant(
            name=name, agent=factory(name=name, buildings=list(unclaimed)),
            buildings=list(unclaimed), is_default=True))
    else:
        for building in unclaimed:
            name = "default_%s" % building
            participants.append(Participant(
                name=name, agent=factory(name=name, buildings=[building]),
                buildings=[building], is_default=True))

    return participants


def build_participants(backend, submissions_folder, default_agent: str = "rbc",
                       unclaimed_mode: str = "individual",
                       conflict_policy: str = "first",
                       assignments: Optional[Dict[str, List[str]]] = None,
                       strict_assignments: bool = False):
    """Full pipeline: discover, resolve conflicts, fill the rest.

    `assignments` optionally pins which buildings each submission is allowed
    to claim. With `strict_assignments` on, claiming anything else is an
    error -- that is the setting to use for a graded tournament.
    """
    all_buildings = list(backend.building_names)
    participants, report = discover(submissions_folder, all_buildings)

    if assignments:
        for participant in participants:
            allowed = assignments.get(participant.name)
            if allowed is None:
                # A submission whose NAME is not in the assignment table.
                # Harmless while a student is experimenting locally, but
                # under strict assignments it is a way to escape your
                # allocation just by renaming yourself, so it must fail.
                message = ("%s is not in the assignment table (known: %s)"
                           % (participant.name, sorted(assignments)))
                if strict_assignments:
                    raise SubmissionError(message)
                report.conflicts.append(message)
                continue
            stray = [b for b in participant.buildings if b not in allowed]
            if stray:
                message = ("%s claims %s but is only assigned %s"
                           % (participant.name, stray, allowed))
                if strict_assignments:
                    raise SubmissionError(message)
                report.conflicts.append(message)
                participant.buildings = [b for b in participant.buildings if b in allowed]
        participants = [p for p in participants if p.buildings]

    participants = resolve_conflicts(participants, report, policy=conflict_policy)
    participants = fill_unclaimed(participants, all_buildings, report,
                                  default_agent=default_agent, mode=unclaimed_mode)
    return participants, report
