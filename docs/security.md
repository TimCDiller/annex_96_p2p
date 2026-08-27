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
| an infinite loop | bounded now, but not cured: `call_timeout_seconds` abandons the call and substitutes an empty bid, and `run_budget_seconds` disqualifies the participant. Python cannot kill the thread, so the abandoned call keeps burning CPU and the run degrades. |
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

This parses each file with `ast` **without importing it** and reports what it
touches: filesystem, network, subprocesses, dynamic code, unpickling, sandbox
escapes, uninterruptible loops, import-time side effects. It also lists every
library imported.

Note what it is not. **There is no import allowlist** — students may use any
library they like, and nothing is blocked. It exits 0 unless a file genuinely
will not load; `--strict` makes any note fail if you want that in CI.

The intended use is twofold: read it before you execute someone's code, and
use it as a prompt list for the viva. The actual check on a submission is that
its author has to explain it; this just tells you what to ask about.

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

> Use any libraries you like. Your submission is a single file (plus a
> weights file if you trained something) that defines `BUILDINGS`, `NAME` and
> `AGENT`, builds anything expensive in `reset()` rather than at import time,
> acts only on the buildings assigned to you and only on the observations you
> are given, and stays inside the published compute limits. You will be asked
> to explain, in your report and in person, what your agent does and why —
> a result you cannot account for scores nothing.

### 5. Cap the run

In your tournament config:

```yaml
max_bid_kwh: 500.0          # clips absurd bid quantities
call_timeout_seconds: 2.0   # per call; overrun gets an empty bid
run_budget_seconds: 60.0    # per participant per run; overrun disqualifies
```

`call_timeout_seconds` and `run_budget_seconds` are real enforcement — see the
COMPUTE section of the report for who used what. Tighten them for a graded run
once you know what legitimate submissions cost; the shipped example uses about
0.02 s for a whole week.

They are not a complete answer, because Python cannot kill a running thread.
An overrunning call is abandoned rather than terminated, so it keeps consuming
CPU and intermittently holding the GIL. The per-run budget stops it being
re-entered every timestep, which turns a hang into a slow run rather than a
dead one. A genuinely hard limit needs one process per agent.

So still wrap the whole thing:

```bash
timeout 900 python -m annex96p2p tournament --config config/default.yaml
```

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
