"""The safety scanner must keep catching what it claims to catch."""
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

#: The line inside bid() that fixtures splice extra statements in front of.
RET = "        return mkt.empty_bid()"
assert RET in CLEAN


def inject(line):
    """Put `line` inside bid(), immediately before its return."""
    return CLEAN.replace(RET, line + "\n" + RET)


def write(tmp_path, body, name="team_x.py"):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def scan(path):
    """Run the scanner as a subprocess: it must never import the target."""
    r = subprocess.run([sys.executable, str(SCRIPT), str(path)],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def test_clean_submission_passes(tmp_path):
    code, out = scan(write(tmp_path, CLEAN))
    assert code == 0, out
    assert "BLOCK" not in out


def test_shipped_example_passes():
    code, out = scan(ROOT / "submissions")
    assert code == 0, out


@pytest.mark.parametrize("snippet,expect", [
    ("import os", "os"),
    ("import subprocess", "subprocess"),
    ("import socket", "socket"),
    ("from pathlib import Path", "pathlib"),
    ("import importlib", "importlib"),
    ("import ctypes", "ctypes"),
])
def test_dangerous_imports_are_blocked(tmp_path, snippet, expect):
    code, out = scan(write(tmp_path, snippet + "\n" + CLEAN))
    assert code == 1, out
    assert "BLOCK" in out and expect in out


@pytest.mark.parametrize("line", [
    "        eval('1+1')",
    "        exec('x=1')",
    "        open('/etc/passwd')",
    "        setattr(mkt, 'x', 1)",
    "        __import__('os')",
    "        globals()",
])
def test_dangerous_calls_are_blocked(tmp_path, line):
    code, out = scan(write(tmp_path, inject(line)))
    assert code == 1, out
    assert "BLOCK" in out


@pytest.mark.parametrize("line", [
    "        x = ().__class__",
    "        y = type(self).__subclasses__()",
    "        z = self.bid.__globals__",
    "        w = getattr(mkt, '__class__')",
])
def test_sandbox_escapes_are_blocked(tmp_path, line):
    code, out = scan(write(tmp_path, inject(line)))
    assert code == 1, out
    assert "BLOCK" in out


def test_infinite_loop_is_blocked(tmp_path):
    body = CLEAN.replace(RET, "        while True:\n            pass")
    code, out = scan(write(tmp_path, body))
    assert code == 1, out
    assert "while True" in out


def test_a_loop_with_a_break_is_allowed(tmp_path):
    body = CLEAN.replace(RET, "        while True:\n            break\n" + RET)
    code, out = scan(write(tmp_path, body))
    assert code == 0, out


def test_importing_another_submission_is_blocked(tmp_path):
    code, out = scan(write(tmp_path, "import team_rival\n" + CLEAN))
    assert code == 1, out
    assert "another submission" in out


def test_import_time_side_effect_is_flagged(tmp_path):
    code, out = scan(write(tmp_path, CLEAN + "\nprint('running at import')\n"))
    assert "import time" in out


def test_unparseable_file_is_reported_not_crashed(tmp_path):
    code, out = scan(write(tmp_path, "def broken(:\n"))
    assert code == 1, out
    assert "does not parse" in out


def test_missing_declarations_are_flagged(tmp_path):
    code, out = scan(write(tmp_path, "import numpy as np\n"))
    assert "BUILDINGS" in out and "AGENT" in out


def test_scanner_does_not_execute_the_file(tmp_path):
    """The whole point: scanning a hostile file must not run it."""
    marker = tmp_path / "pwned.txt"
    body = ("with open(%r, 'w') as f:\n    f.write('x')\n" % str(marker)) + CLEAN
    code, out = scan(write(tmp_path, body))
    assert code == 1, out
    assert not marker.exists(), "the scanner executed the submission"
