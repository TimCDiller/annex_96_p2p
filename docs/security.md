# Running other people's code

Read this before you collect submissions and run a tournament on your own
machine.

## The honest threat model

A submission is a Python file. The runner imports it with
`importlib.exec_module`, which **executes whatever is in the file** with the
full privileges of whoever ran the command. There is no sandbox, and there
cannot be a meaningful one inside the same process.

So it is worth being precise about what the existing guards do and do not do.

### What the runtime guards protect against

These are in [`runner.py`](../src/annex96p2p/runner.py) and
[`market.py`](../src/annex96p2p/market.py), and they are about **mistakes**:

| Failure | What happens |
|---|---|
| `bid()` or `dispatch()` raises | caught, default agent substituted for that timestep, counted and reported |
| bid is the wrong length | padded or truncated, warning recorded |
| bid contains NaN or infinity | those entries zeroed |
| bid quantities are absurd | clipped to `max_bid_kwh` |
| `dispatch()` returns junk | buildings idled for that timestep |
| `dispatch()` returns actions for buildings it does not own | silently discarded |
| submission does not parse or import | skipped with an explanation, run continues |
| two submissions claim the same building | warning in `run`, hard error in `tournament` |
| a submission renames itself to escape its assignment | hard error under `strict_assignments` |
| a submission reads another team's observations | impossible — the firewall passes only its own slice |

One broken submission cannot take down a tournament. That part is solid, and
there are tests for it.

### What they do NOT protect against

| Attack | Why the guard misses it |
|---|---|
| `os.system(...)`, file deletion, network calls | runs at import or inside the call; nothing intercepts it |
| reading your files, your other students' submissions, your solutions | ordinary file I/O |
| monkey-patching `market.clear` or the ledger | the submission can import package internals and rebind them |
| an infinite loop | `_timed` measures elapsed time **after** the call returns, so a hang is never detected — the tournament simply stops |
| `sys.exit()` / `os._exit()` | the guard catches `Exception`, not `SystemExit` |
| exhausting memory | no limit |
| code that runs at import time | executes before any guard exists |

**Summary: the guards are robustness against accidents, not security against
attacks.** Running a collected tournament on your laptop is running untrusted
code on your laptop.

## What to actually do

### 1. Scan before you run

```bash
python scripts/scan_submissions.py submissions/
make scan
```

This parses each file with `ast` **without importing it** and flags dangerous
imports, `eval`/`exec`/`open`, dunder-attribute sandbox escapes, `setattr`
monkey-patching, imports of sibling submissions, `while True` with no break,
and code that runs at import time. It exits non-zero if anything is blocking.

It is a smoke detector, not a sandbox — a determined student can evade a
static scan. A clean report means "nothing obvious", never "safe to run".

### 2. Isolate the run — this is the actual protection

Pick one. In rough order of how much I would trust it:

**Ephemeral CI (best, and free).** Students submit by pull request; GitHub
Actions runs the scan and the simulation on a fresh throwaway runner. Nothing
executes on your machine at all. The workflow is already in
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml).

**Container.** For running a full tournament locally:

```bash
docker build -t annex96 .
docker run --rm --network none --memory 2g --cpus 2 \
  -v "$PWD/submissions:/app/submissions:ro" \
  annex96 python -m annex96p2p tournament --config config/default.yaml
```

`--network none` removes network access, `:ro` makes the submissions
read-only, and the memory and CPU caps stop a runaway agent. The
[`Dockerfile`](../Dockerfile) is in the repo.

**Disposable VM.** A throwaway cloud instance or a local VM with no
credentials on it and nothing you care about.

**A dedicated OS user** with no access to your home directory. Weaker, but
much better than nothing.

What I would *not* do is run twenty collected files directly on the machine
that holds your gradebook and your SSH keys.

### 3. Read the submissions

They are one file each, typically under 150 lines. Skimming twenty of them
takes fifteen minutes and catches everything a static scan does plus the
things it cannot: subtle cheating, plagiarism, logic that games the scoring
rather than the physics.

### 4. Set the rules explicitly

Put this in the assignment brief, because a rule nobody stated is a rule
nobody follows:

> Your submission is a single file that imports only `annex96p2p`, `numpy`
> and the Python standard library's pure-computation modules. It must not
> read or write files, use the network, spawn processes, import another
> submission, modify anything in the `annex96p2p` package, or run code at
> import time beyond defining `BUILDINGS`, `NAME` and `AGENT`. Anything that
> makes the tournament hang or crash scores zero.

### 5. Cap the run

In your tournament config:

```yaml
max_bid_kwh: 500.0        # clips absurd bid quantities
slow_call_seconds: 0.25   # lowers the bar for the "slow" report
```

Then wrap the whole thing in an external timeout so a hang is bounded rather
than infinite:

```bash
timeout 600 python -m annex96p2p tournament --config config/default.yaml
```

`slow_call_seconds` only *reports*; the external `timeout` is what actually
saves you. A true per-call timeout needs a subprocess or a signal, neither of
which is portable across Windows and POSIX, which is why the runner does not
attempt it.

## Cheating, as distinct from attacking

Some things are unsporting rather than dangerous, and the code does not stop
them:

- **Hard-coding the price series.** The synthetic tariff is deterministic, so
  a student can memorise it instead of forecasting. Fix by running the graded
  tournament with a different `seed` or a `price_csv` they have not seen.
- **Hard-coding the weather or their own load.** Same fix.
- **Colluding with another team.** Two students agreeing to bid in a
  complementary way is not detectable from the ledger, and it is a *feature*
  — let it happen, then discuss why it is illegal in real markets.
- **Overfitting to the default agent.** A submission tuned to exploit
  `SimpleRBCAgent` specifically will do worse against real opponents. The
  tournament exposes this on its own.

The seed change is the important one. If the graded run uses the same seed
students developed against, you are partly grading memorisation.
