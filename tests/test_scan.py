"""The submission scanner is a report, not a gate.

There is no import allowlist: a submission may use any library it likes. What
the scanner does is describe what a file touches, so that whoever runs it
knows what to expect and so those choices can be asked about when the author
presents their work.

So the contract under test is:

  * anything noteworthy is REPORTED, and exits 0 by default
  * --strict turns notes into a non-zero exit, for CI
  * only a file that genuinely will not load is an ERROR
  * scanning never imports or executes the file
"""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "scan_submissions.py"

CLEAN = textwrap.dedent('''
    import numpy as np
    from annex96p2p import Action, Agent

    BUILDINGS = ["B01"]

    class A(Agent):
        def bid(self, obs, mkt):
            return mkt.empty_bid()
        def dispatch(self, obs, mkt, result):
            return {n: Action() for n in obs}

    AGENT = A
''')

RET = "        return mkt.empty_bid()"
assert RET in CLEAN


def inject(line):
    return CLEAN.replace(RET, line + "\n" + RET)


def write(tmp_path, body, name="team_x.py"):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def scan(path, *extra):
    """Run the scanner as a subprocess: it must never import the target."""
    r = subprocess.run([sys.executable, str(SCRIPT), str(path), *extra],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


# --------------------------------------------------------------------------
# clean files
# --------------------------------------------------------------------------
def test_clean_submission_passes(tmp_path):
    code, out = scan(write(tmp_path, CLEAN))
    assert code == 0, out
    assert "0 note(s)" in out


def test_shipped_example_passes():
    code, out = scan(ROOT / "submissions")
    assert code == 0, out


def test_imports_are_listed(tmp_path):
    code, out = scan(write(tmp_path, CLEAN))
    assert "annex96p2p" in out and "numpy" in out


# --------------------------------------------------------------------------
# no import allowlist -- ML frameworks are unremarkable
# --------------------------------------------------------------------------
@pytest.mark.parametrize("snippet", [
    "import torch",
    "import tensorflow as tf",
    "from sklearn.linear_model import Ridge",
    "import jax.numpy as jnp",
    "import stable_baselines3",
    "import gymnasium",
    "import cvxpy",
    "import scipy.optimize",
    "import some_library_nobody_has_heard_of",
])
def test_any_library_is_allowed_without_comment(tmp_path, snippet):
    code, out = scan(write(tmp_path, snippet + "\n" + CLEAN))
    assert code == 0, out
    assert "0 note(s)" in out, "a library import was flagged: %s" % out


# --------------------------------------------------------------------------
# noteworthy behaviour is reported, but does not fail
# --------------------------------------------------------------------------
@pytest.mark.parametrize("snippet,expect", [
    ("import os", "os"),
    ("import subprocess", "subprocess"),
    ("import socket", "socket"),
    ("import pickle", "pickle"),
])
def test_notable_imports_are_reported_not_blocked(tmp_path, snippet, expect):
    code, out = scan(write(tmp_path, snippet + "\n" + CLEAN))
    assert code == 0, "a note must not fail the scan"
    assert expect in out and "note(s)" in out


@pytest.mark.parametrize("line", [
    "        eval('1+1')",
    "        open('/etc/passwd')",
    "        x = type(self).__subclasses__()",
    "        y = self.bid.__globals__",
])
def test_notable_calls_are_reported_not_blocked(tmp_path, line):
    code, out = scan(write(tmp_path, inject(line)))
    assert code == 0, out
    assert "note" in out


def test_pickle_loading_is_reported_with_the_reason(tmp_path):
    body = CLEAN.replace(RET, "        m = torch.load('m.pt')\n" + RET)
    code, out = scan(write(tmp_path, "import torch\n" + body))
    assert code == 0
    assert "torch.load()" in out and "nobody can read" in out.replace("\n", " ") \
        or "unpickling" in out


def test_infinite_loop_is_reported(tmp_path):
    body = CLEAN.replace(RET, "        while True:\n            pass")
    code, out = scan(write(tmp_path, body))
    assert code == 0
    assert "while True" in out


def test_a_loop_with_a_break_is_unremarkable(tmp_path):
    body = CLEAN.replace(RET, "        while True:\n            break\n" + RET)
    code, out = scan(write(tmp_path, body))
    assert code == 0
    assert "0 note(s)" in out


def test_import_time_side_effect_is_reported(tmp_path):
    code, out = scan(write(tmp_path, CLEAN + "\nprint('at import')\n"))
    assert code == 0
    assert "import time" in out


# --------------------------------------------------------------------------
# --strict, for CI
# --------------------------------------------------------------------------
def test_strict_makes_notes_fail(tmp_path):
    path = write(tmp_path, "import os\n" + CLEAN)
    assert scan(path)[0] == 0
    assert scan(path, "--strict")[0] == 1


def test_strict_still_passes_a_clean_file(tmp_path):
    assert scan(write(tmp_path, CLEAN), "--strict")[0] == 0


# --------------------------------------------------------------------------
# genuine errors
# --------------------------------------------------------------------------
def test_unparseable_file_is_an_error(tmp_path):
    code, out = scan(write(tmp_path, "def broken(:\n"))
    assert code == 1
    assert "does not parse" in out


def test_missing_declarations_are_an_error(tmp_path):
    code, out = scan(write(tmp_path, "import numpy as np\n"))
    assert code == 1
    assert "BUILDINGS" in out and "AGENT" in out


# --------------------------------------------------------------------------
# the one hard guarantee
# --------------------------------------------------------------------------
def test_scanner_does_not_execute_the_file(tmp_path):
    marker = tmp_path / "pwned.txt"
    body = ("with open(%r, 'w') as f:\n    f.write('x')\n" % str(marker)) + CLEAN
    code, out = scan(write(tmp_path, body))
    assert not marker.exists(), "the scanner executed the submission"
    # It should still say that something happens at import time -- the exact
    # wording differs between a bare expression and a `with` block.
    assert "on import" in out or "import time" in out, out
