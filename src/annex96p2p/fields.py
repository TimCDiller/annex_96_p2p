"""Fields: which agents run the houses that no submission claims.

A field is defined in `agents/fields.py` as a list of entries, each putting
one agent file from `agents/` on a set of houses:

    dict(agent="advanced/marginal_value_storage.py",   # path inside agents/
         houses=[...],                                 # building names
         name="marginal value",                        # optional, else the file's NAME
         params=dict(HORIZON=30),                      # optional class attributes to override
         together=False)                               # one participant for all houses?

By default every house becomes its own participant, named "<name> <house>".
Houses a submission claims are taken out of the field first, so a student
always plays their own houses against whatever the field puts on the rest.
Houses the field does not mention are run by the default agent, as always.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Dict, List, Sequence

from .registry import Participant, _instantiate

DEFAULT_FIELD = "default"


def _import(path: Path, prefix: str):
    name = "%s_%s" % (prefix, "_".join(path.with_suffix("").parts[-3:]).replace("-", "_"))
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot import %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_fields(agents_dir) -> Dict[str, list]:
    """FIELDS from <agents_dir>/fields.py."""
    path = Path(agents_dir) / "fields.py"
    if not path.exists():
        raise ValueError("no field definitions at %s" % path)
    return dict(_import(path.resolve(), "annex96_fields").FIELDS)


def field_participants(agents_dir, field: str, buildings: Sequence[str],
                       taken: Sequence[str] = ()) -> List[Participant]:
    """The field's participants on every house not in `taken`."""
    fields = load_fields(agents_dir)
    if field not in fields:
        raise ValueError("unknown field %r; defined in %s/fields.py: %s"
                         % (field, agents_dir, ", ".join(fields)))
    valid, taken = set(buildings), set(taken)
    parts, seen = [], set()
    for entry in fields[field]:
        path = (Path(agents_dir) / entry["agent"]).resolve()
        module = _import(path, "annex96_field")
        base = getattr(module, "AGENT")
        params = dict(entry.get("params", {}))
        cls = type(base.__name__, (base,), params) if params else base
        label = entry.get("name") or str(getattr(module, "NAME", path.stem))
        houses = list(entry["houses"])
        unknown = [h for h in houses if h not in valid]
        if unknown:
            raise ValueError("field %r puts %s on houses that do not exist: %s"
                             % (field, entry["agent"], unknown))
        overlap = seen.intersection(houses)
        if overlap:
            raise ValueError("field %r puts two agents on %s" % (field, sorted(overlap)))
        seen.update(houses)
        houses = [h for h in houses if h not in taken]
        groups = [houses] if entry.get("together") else [[h] for h in houses]
        for group in groups:
            if not group:
                continue
            name = label if entry.get("together") else "%s %s" % (label, group[0].rsplit("-", 1)[-1])
            parts.append(Participant(name=name, agent=_instantiate(cls, name, group),
                                     buildings=group, is_default=False, source=str(path)))
    return parts
