# Submissions

Put your agent in this folder as a single `.py` file.

```bash
cp example_team.py team_yourname.py
python -m annex96p2p check submissions/team_yourname.py
python -m annex96p2p run --config ../config/default.yaml
```

## What the file must contain

```python
BUILDINGS = ["B01", "B02"]     # required: which buildings you control
AGENT = MyAgent                # required: an Agent subclass or instance
NAME = "team_yourname"         # optional: defaults to the file name
```

## Rules

- **One file.** If you need helpers, put them in the same file.
- **Files starting with `_` are ignored**, so `_scratch.py` is a safe place
  to keep experiments without them being loaded as a submission.
- **Only claim buildings you were assigned.** Run
  `python -m annex96p2p buildings` to see what exists.
- **Only touch your own buildings.** `dispatch()` may return actions only for
  buildings in your `BUILDINGS`; anything else is discarded.
- **Do not import from other submissions**, read other participants' state, or
  reach into the runner. The observations you are handed are the only
  information you get.
- **Keep it fast.** A call taking longer than a second is flagged. There are
  thousands of calls in a run.
- **Do not crash on purpose.** A failing agent is replaced by the default
  agent for that timestep, which will cost you.

## Unclaimed buildings

Anything nobody claims runs on the built-in default agent, one participant per
building. That is why you can develop alone: your market is full of
counterparties even when yours is the only file here.
