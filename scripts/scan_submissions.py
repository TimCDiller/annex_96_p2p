#!/usr/bin/env python
"""Summarise what a submission does. Read this before you run it.

    python scripts/scan_submissions.py submissions/
    python scripts/scan_submissions.py submissions/team_x.py --strict

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
There is **no restriction on what a submission may import**. Use PyTorch,
TensorFlow, JAX, scikit-learn, stable-baselines3, CVXPY, whatever you like.
Nothing here is an allowlist and nothing here is a gate.

The real check on a submission is not static analysis. It is that its author
has to stand up and explain, out loud and in writing, what their agent does
and why. An agent nobody can explain is worthless however well it scores, and
that requirement catches everything a scanner would — plagiarism, a
copy-pasted model, a hack that games the scoring rather than the physics —
plus a great deal that a scanner cannot.

So this script does something narrower and more useful: it reads each file
with `ast`, **without importing it**, and prints the things worth knowing
before you execute someone else's code on your machine, and worth asking
about in the viva. Filesystem access, network calls, subprocesses, dynamic
code, unpickling, and loops that cannot be interrupted.

By default it exits 0 — it is a report, not a verdict. `--strict` makes any
note fail, which is useful in CI if you would rather be told loudly.

A clean report does NOT mean safe to run. A determined author can evade a
static scan trivially. The actual protection is isolation: run collected
submissions in a container or a throwaway CI runner, never on the machine
with your gradebook on it. See docs/security.md.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import List, Tuple

# Things worth knowing about, with why you might ask. Nothing here is banned.
NOTABLE_IMPORTS = {
    "os": "filesystem and process access",
    "sys": "interpreter internals",
    "subprocess": "runs external programs",
    "socket": "network access",
    "shutil": "file deletion and copying",
    "pathlib": "filesystem access",
    "importlib": "dynamic imports, invisible to this scan",
    "builtins": "can rebind built-in functions",
    "ctypes": "arbitrary native calls",
    "pickle": "unpickling executes arbitrary code",
    "cloudpickle": "unpickling executes arbitrary code",
    "dill": "unpickling executes arbitrary code",
    "joblib": "pickle-based persistence",
    "marshal": "arbitrary code on load",
    "urllib": "network access",
    "requests": "network access",
    "http": "network access",
    "multiprocessing": "spawns processes",
    "threading": "concurrency that can outlive a call",
    "signal": "can interfere with the runner",
    "atexit": "runs code after the simulation ends",
    "inspect": "reads other objects' internals",
    "tempfile": "filesystem access",
    "glob": "filesystem access",
    "io": "filesystem access",
    "webbrowser": "launches programs",
    "sqlite3": "filesystem access",
}

NOTABLE_CALLS = {
    "eval": "evaluates code built at run time",
    "exec": "executes code built at run time",
    "compile": "compiles code at run time",
    "__import__": "dynamic import, invisible to this scan",
    "open": "reads or writes a file (load_weights() is the sanctioned route)",
    "input": "blocks the run waiting on stdin",
    "breakpoint": "drops into a debugger and blocks",
    "globals": "reaches outside the submission",
    "setattr": "can rebind attributes on the runner or the market",
    "delattr": "can remove attributes from other objects",
}

NOTABLE_ATTRS = {
    "__subclasses__": "walks the class hierarchy, a classic sandbox escape",
    "__globals__": "reads another function's module namespace",
    "__code__": "reads or replaces bytecode",
    "__builtins__": "can rebind built-in functions",
    "__reduce__": "pickle-based code execution",
    "f_globals": "stack frame access",
    "f_locals": "stack frame access",
}


class Scanner(ast.NodeVisitor):
    def __init__(self):
        self.notes: List[Tuple[int, str, str]] = []
        self.errors: List[Tuple[int, str, str]] = []
        self.imports: set = set()

    def note(self, node, msg):
        self.notes.append((getattr(node, "lineno", 0), "note", msg))

    def error(self, node, msg):
        self.errors.append((getattr(node, "lineno", 0), "ERROR", msg))

    # -- imports ----------------------------------------------------------
    def visit_Import(self, node):
        for a in node.names:
            self._check(node, a.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            self._check(node, node.module)
        self.generic_visit(node)

    def _check(self, node, name):
        root = name.split(".")[0]
        self.imports.add(root)
        if root in NOTABLE_IMPORTS:
            self.note(node, "imports %s -- %s" % (name, NOTABLE_IMPORTS[root]))
        if root.startswith("team") or "submission" in root:
            self.note(node, "imports %s, which looks like another submission" % name)

    # -- calls ------------------------------------------------------------
    def visit_Call(self, node):
        f = node.func
        if isinstance(f, ast.Name) and f.id in NOTABLE_CALLS:
            self.note(node, "calls %s() -- %s" % (f.id, NOTABLE_CALLS[f.id]))
        if isinstance(f, ast.Attribute):
            if f.attr in ("system", "popen", "spawn", "fork", "execv",
                          "remove", "unlink", "rmtree", "chmod"):
                self.note(node, "calls .%s() -- process or filesystem access" % f.attr)
            if f.attr == "load":
                base = f.value.id if isinstance(f.value, ast.Name) else ""
                if base in ("torch", "pickle", "joblib", "dill", "cloudpickle"):
                    self.note(node, "calls %s.load() -- unpickling executes arbitrary "
                                    "code; .npz or .safetensors is readable, a pickle "
                                    "is not" % base)
        self.generic_visit(node)

    # -- attributes -------------------------------------------------------
    def visit_Attribute(self, node):
        if node.attr in NOTABLE_ATTRS:
            self.note(node, "touches .%s -- %s" % (node.attr, NOTABLE_ATTRS[node.attr]))
        self.generic_visit(node)

    # -- loops the runner cannot interrupt --------------------------------
    def visit_While(self, node):
        if (isinstance(node.test, ast.Constant) and node.test.value is True
                and not any(isinstance(x, ast.Break) for x in ast.walk(node))):
            self.note(node, "while True with no break -- the call timeout will "
                            "abandon it, and the per-run budget will disqualify")
        self.generic_visit(node)

    # -- import-time side effects -----------------------------------------
    def check_toplevel(self, tree):
        for n in tree.body:
            if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                              ast.AsyncFunctionDef, ast.ClassDef, ast.Assign,
                              ast.AnnAssign, ast.Pass)):
                continue
            if isinstance(n, ast.Expr):
                if not isinstance(n.value, ast.Constant):      # not a docstring
                    self.note(n, "expression runs at import time, before any guard")
                continue
            self.note(n, "%s at module level runs on import" % type(n).__name__)


def scan(path: Path):
    src = path.read_text(encoding="utf-8", errors="replace")
    s = Scanner()
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        s.errors.append((exc.lineno or 0, "ERROR", "does not parse: %s" % exc.msg))
        return s
    s.check_toplevel(tree)
    s.visit(tree)

    declared = {t.id for n in tree.body if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    for req in ("BUILDINGS", "AGENT"):
        if req not in declared:
            s.errors.append((0, "ERROR", "no module-level %s -- it will not load" % req))
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+", help="submission files or folders")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if anything at all is noted")
    args = ap.parse_args(argv)

    files: List[Path] = []
    for p in args.paths:
        q = Path(p)
        if q.is_dir():
            files += sorted(f for f in q.glob("*.py") if not f.stem.startswith("_"))
        elif q.exists():
            files.append(q)
        else:
            print("no such path: %s" % p)
            return 2

    errors = notes = 0
    for f in files:
        s = scan(f)
        errors += len(s.errors)
        notes += len(s.notes)
        status = "ERROR" if s.errors else ("note" if s.notes else "ok")
        libs = ", ".join(sorted(s.imports)) or "nothing"
        print("%-6s %s" % (status, f))
        print("       imports: %s" % libs)
        for line, level, msg in sorted(s.errors + s.notes):
            print("       %s:%d  %s" % (f.name, line, msg))

    print("\n%d file(s): %d error(s), %d note(s)" % (len(files), errors, notes))
    if errors:
        print("\nErrors mean the submission will not load at all.")
    if notes:
        print("\nNotes are not violations -- there is no import allowlist. They are\n"
              "the things to read before running this on your own machine, and the\n"
              "things to ask the author about when they present their work.")
    print("\nEither way: a clean report is not proof of anything. Run collected\n"
          "submissions in a container or a throwaway CI runner, not on your\n"
          "own machine. See docs/security.md.")
    return 1 if (errors or (args.strict and notes)) else 0


if __name__ == "__main__":
    sys.exit(main())
