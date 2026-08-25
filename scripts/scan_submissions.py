#!/usr/bin/env python
"""Static safety scan for student submissions. Run this BEFORE a tournament.

    python scripts/scan_submissions.py submissions/
    python scripts/scan_submissions.py submissions/team_x.py --strict

WHY THIS EXISTS
---------------
A submission is a Python file, and the runner imports it with
`importlib.exec_module`. That executes whatever is in the file, with the full
privileges of whoever ran the command. The runtime guards in `runner.py`
catch mistakes -- exceptions, malformed bids, junk return values -- but they
are not a security boundary and cannot be one. Nothing stops a submission
from reading your files, opening a socket, or monkey-patching the market.

So this script reads each submission WITHOUT importing it, using the `ast`
module, and reports anything a controller has no business doing. It is a
smoke detector, not a sandbox: a determined attacker can evade a static
scan. Treat a clean report as "no obvious problem", never as "safe to run
on my laptop".

The actual protection is isolation -- run tournaments in a container, a VM,
or a disposable CI runner. See docs/security.md.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import List, Tuple

# Modules a controller legitimately needs.
ALLOWED_IMPORTS = {
    "annex96p2p", "numpy", "np", "math", "statistics", "random",
    "typing", "dataclasses", "collections", "itertools", "functools",
    "heapq", "bisect", "enum", "abc", "copy", "decimal", "fractions",
    "pandas",           # heavier than needed, but harmless and common
}

# Modules that let a submission leave its own process.
BANNED_IMPORTS = {
    "os": "filesystem and process access",
    "sys": "interpreter internals",
    "subprocess": "runs external programs",
    "socket": "network access",
    "shutil": "file deletion and copying",
    "pathlib": "filesystem access",
    "importlib": "dynamic imports bypass this scan",
    "builtins": "can rebind built-in functions",
    "ctypes": "arbitrary native calls",
    "pickle": "arbitrary code on load",
    "marshal": "arbitrary code on load",
    "urllib": "network access",
    "requests": "network access",
    "http": "network access",
    "ftplib": "network access",
    "smtplib": "network access",
    "multiprocessing": "spawns processes",
    "threading": "concurrency, can outlive the call",
    "signal": "can interfere with the runner",
    "atexit": "runs code after the simulation",
    "gc": "interpreter internals",
    "inspect": "reads other objects' internals",
    "traceback": "reads stack frames",
    "tempfile": "filesystem access",
    "glob": "filesystem access",
    "io": "filesystem access",
    "webbrowser": "launches programs",
    "platform": "host fingerprinting",
    "getpass": "reads user identity",
    "sqlite3": "filesystem access",
}

# Names that should never be called from a controller.
BANNED_CALLS = {
    "eval": "evaluates arbitrary code",
    "exec": "executes arbitrary code",
    "compile": "compiles arbitrary code",
    "__import__": "dynamic import bypasses this scan",
    "open": "filesystem access",
    "input": "blocks the run waiting for stdin",
    "breakpoint": "drops into a debugger and blocks",
    "globals": "reaches outside the submission",
    "locals": "reaches outside the submission",
    "vars": "reaches outside the submission",
    "setattr": "can monkey-patch the runner or the market",
    "delattr": "can break the runner",
    "memoryview": "raw memory access",
}

# Attribute chains used to escape a sandbox or reach the runner.
BANNED_ATTRS = {
    "__class__": "type introspection, used to escape sandboxes",
    "__bases__": "type introspection",
    "__subclasses__": "classic sandbox escape",
    "__mro__": "type introspection",
    "__globals__": "reaches the module namespace of another function",
    "__code__": "reads or replaces bytecode",
    "__dict__": "can monkey-patch another object",
    "__builtins__": "can rebind built-in functions",
    "__reduce__": "pickle-based code execution",
    "__getattribute__": "attribute interception",
    "f_globals": "stack frame access",
    "f_locals": "stack frame access",
    "gi_frame": "generator frame access",
}


class Scanner(ast.NodeVisitor):
    def __init__(self, path: Path):
        self.path = path
        self.findings: List[Tuple[int, str, str]] = []   # line, level, message

    def flag(self, node, level, msg):
        self.findings.append((getattr(node, "lineno", 0), level, msg))

    # -- imports ----------------------------------------------------------
    def visit_Import(self, node):
        for a in node.names:
            self._check_import(node, a.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.level:           # relative import: reaches outside one file
            self.flag(node, "BLOCK", "relative import (submissions are one file)")
        elif node.module:
            self._check_import(node, node.module)
        self.generic_visit(node)

    def _check_import(self, node, name):
        root = name.split(".")[0]
        if root in BANNED_IMPORTS:
            self.flag(node, "BLOCK", "imports %r -- %s" % (name, BANNED_IMPORTS[root]))
        elif root not in ALLOWED_IMPORTS:
            self.flag(node, "WARN", "imports %r, which is not on the allowlist" % name)
        # importing a sibling submission is cheating, not just unsafe
        if root.startswith("team") or "submission" in root:
            self.flag(node, "BLOCK", "imports another submission (%r)" % name)

    # -- calls ------------------------------------------------------------
    def visit_Call(self, node):
        f = node.func
        if isinstance(f, ast.Name) and f.id in BANNED_CALLS:
            self.flag(node, "BLOCK", "calls %s() -- %s" % (f.id, BANNED_CALLS[f.id]))
        if isinstance(f, ast.Attribute) and f.attr in ("system", "popen", "spawn",
                                                       "fork", "execv", "remove",
                                                       "unlink", "rmtree", "chmod"):
            self.flag(node, "BLOCK", "calls .%s() -- process or filesystem access" % f.attr)
        # getattr(x, "__class__") style indirection
        if isinstance(f, ast.Name) and f.id == "getattr" and len(node.args) >= 2:
            a = node.args[1]
            if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                    and a.value.startswith("__"):
                self.flag(node, "BLOCK", "getattr() with a dunder name (%r)" % a.value)
        self.generic_visit(node)

    # -- attributes -------------------------------------------------------
    def visit_Attribute(self, node):
        if node.attr in BANNED_ATTRS:
            self.flag(node, "BLOCK", "touches .%s -- %s" % (node.attr, BANNED_ATTRS[node.attr]))
        self.generic_visit(node)

    # -- module-level side effects ----------------------------------------
    def check_toplevel(self, tree):
        """Import-time code runs before any guard. It should be declarations only."""
        for n in tree.body:
            if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                              ast.AsyncFunctionDef, ast.ClassDef, ast.Assign,
                              ast.AnnAssign, ast.Expr, ast.Pass)):
                if isinstance(n, ast.Expr) and not isinstance(n.value, ast.Constant):
                    self.flag(n, "WARN", "expression evaluated at import time")
                continue
            self.flag(n, "WARN", "%s at module level runs on import"
                      % type(n).__name__)

    # -- unbounded loops --------------------------------------------------
    def visit_While(self, node):
        test = node.test
        forever = (isinstance(test, ast.Constant) and test.value is True)
        if forever and not any(isinstance(x, ast.Break) for x in ast.walk(node)):
            self.flag(node, "BLOCK", "while True with no break -- the runner cannot "
                                     "interrupt this and the tournament will hang")
        self.generic_visit(node)


def scan(path: Path) -> List[Tuple[int, str, str]]:
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        return [(exc.lineno or 0, "BLOCK", "does not parse: %s" % exc.msg)]
    s = Scanner(path)
    s.check_toplevel(tree)
    s.visit(tree)

    # Required declarations
    names = {t.id for n in tree.body if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)}
    for req in ("BUILDINGS", "AGENT"):
        if req not in names:
            s.findings.append((0, "WARN", "no module-level %s -- will not load" % req))
    return sorted(s.findings)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+", help="submission files or folders")
    ap.add_argument("--strict", action="store_true",
                    help="treat WARN as failure too")
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

    blocked = warned = 0
    for f in files:
        found = scan(f)
        bad = [x for x in found if x[1] == "BLOCK"]
        warn = [x for x in found if x[1] == "WARN"]
        blocked += len(bad)
        warned += len(warn)
        status = "BLOCK" if bad else ("WARN" if warn else "ok")
        print("%-6s %s" % (status, f))
        for line, level, msg in found:
            print("       %s:%d  %s: %s" % (f.name, line, level, msg))

    print("\n%d file(s): %d blocking, %d warning" % (len(files), blocked, warned))
    if blocked or (args.strict and warned):
        print("\nDo NOT run these on a machine you care about. A clean report is not")
        print("proof of safety either -- see docs/security.md for the isolation setup.")
        return 1
    print("\nNo obvious problems. This is a static scan, not a sandbox: still run")
    print("the tournament in a container or a disposable VM. See docs/security.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
