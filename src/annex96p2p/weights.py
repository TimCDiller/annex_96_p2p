"""Loading trained model weights from a submission.

You are not forbidden from opening files yourself -- there is no restriction
on what a submission may do with the standard library. This is a convenience
that gets two things right which hand-rolled loading usually gets wrong:

  * it resolves the filename inside `submissions/`, so your agent works
    regardless of which directory the run was started from;
  * it reads only formats that are pure data, so whoever reviews or runs your
    submission can see exactly what is in your checkpoint.

Use it like this:

    from annex96p2p import load_weights

    class MyAgent(Agent):
        def reset(self):
            w = load_weights("team_yourname.npz")
            self.W1, self.b1 = w["W1"], w["b1"]

The file must sit next to your submission in `submissions/`, and be named
after it. Two formats are accepted:

    .npz            numpy archive, loaded with allow_pickle=False
    .safetensors    if the `safetensors` package is installed

Both are pure data. Pickle-based checkpoints -- `.pt`, `.pth`, `.pkl`,
`.joblib` -- are not accepted *here*, and you are free to load one yourself
with `torch.load` if you prefer. The reason to prefer these formats is not
that pickle grants you any capability your submission does not already have.
It is that a pickle is the one part of a submission nobody can read: a
150-line agent can be reviewed in two minutes, a 40 MB checkpoint cannot. If
you are going to be asked to explain what your agent learned, a readable
weights file is on your side.

Converting a trained PyTorch model is two lines:

    import numpy as np, torch
    sd = torch.load("model.pt", map_location="cpu")     # on YOUR machine
    np.savez("submissions/team_yourname.npz",
             **{k: v.numpy() for k, v in sd.items()})

or with safetensors:

    from safetensors.torch import save_file
    save_file(model.state_dict(), "submissions/team_yourname.safetensors")

Then rebuild the network in `reset()` and load the arrays into it. Keep the
model small: it is called twice per timestep, thousands of times per run, and
there is a `call_timeout_seconds` budget.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

import numpy as np

ALLOWED_SUFFIXES = {".npz", ".safetensors"}

#: Overridable so the runner can point at a different submissions folder.
_SEARCH_DIRS: list = []


def register_search_dir(path) -> None:
    """Called by the runner so `load_weights` finds the folder in use."""
    p = Path(path).resolve()
    if p not in _SEARCH_DIRS:
        _SEARCH_DIRS.insert(0, p)


def _candidates(name: str):
    seen = []
    for d in _SEARCH_DIRS:
        seen.append(d / name)
    seen.append(Path("submissions") / name)
    seen.append(Path(__file__).resolve().parents[2] / "submissions" / name)
    return seen


def load_weights(name: str) -> Dict[str, np.ndarray]:
    """Load a weights file from the submissions folder.

    `name` is a bare filename -- no directories, no `..`. Returns a dict of
    numpy arrays.
    """
    if os.path.sep in name or "/" in name or ".." in name:
        raise ValueError(
            "load_weights() takes a bare filename, not a path: %r. Weights live "
            "next to your submission." % name)

    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(
            "%r is not an accepted weights format. Use .npz (numpy, saved with "
            "np.savez) or .safetensors. Pickle-based checkpoints (.pt, .pth, "
            ".pkl, .joblib) are refused because unpickling executes arbitrary "
            "code -- convert yours first, see annex96p2p/weights.py." % name)

    tried = _candidates(name)
    path: Optional[Path] = next((p for p in tried if p.is_file()), None)
    if path is None:
        raise FileNotFoundError(
            "no weights file %r. Looked in:\n  %s"
            % (name, "\n  ".join(str(p) for p in tried)))

    if suffix == ".npz":
        # allow_pickle=False is the whole point: pure arrays, no code.
        with np.load(path, allow_pickle=False) as z:
            return {k: z[k] for k in z.files}

    try:
        from safetensors.numpy import load_file
    except Exception as exc:                                     # noqa: BLE001
        raise RuntimeError(
            "reading %s needs the safetensors package: pip install safetensors "
            "(%s)" % (name, exc))
    return dict(load_file(str(path)))
