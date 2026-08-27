# Peer-to-peer electricity trading

A graduate course exercise, adapted from
[IEA EBC Annex 96 Common Exercise 1](https://github.com/kkaspar10/annex96_common_exercise_1).

The original exercise asks one controller to keep a whole portfolio of homes
tracking a flat reference load. This version changes two things:

1. **Electricity has a time-varying price**, and the objective is to minimise
   the sum of everybody's bills instead of flattening a load profile.
2. **Each student controls only a subset of the buildings**, and everyone
   trades energy with each other in a peer-to-peer market.

**Reading order if you are writing a submission:** [Quick start](#quick-start)
→ [Submission rules](#submission-rules) →
[How the market works](#how-the-market-works) →
[Writing a submission](#writing-a-submission) → [The API in full](#the-api-in-full).
Then [Debugging your agent](#debugging-your-agent) when something looks wrong,
and [The default agent, and how to beat it](#the-default-agent-and-how-to-beat-it)
when it works but is not winning.

Run `python examples/explain_market.py` first — it walks one market clearing
through in real numbers and is the fastest way to understand the mechanism.

---

## Quick start

```bash
git clone <this repo>
cd annex96-p2p
pip install -e ".[dev]"

python -m pytest                                  # confirm your setup works
python examples/explain_market.py                 # how the market works, in numbers
python -m annex96p2p run --config config/smoke.yaml
```

That needs numpy, pandas and pyyaml and nothing else, and runs a simulated
week in about a tenth of a second on a synthetic 12-building neighbourhood.

**There are two building stocks, and the same agent code runs on both:**

| config | stock | needs | speed |
|---|---|---|---|
| `smoke.yaml` | 12 synthetic buildings, 2 days | numpy | instant |
| `mock.yaml` | 12 synthetic buildings, a week | numpy | ~0.1 s |
| `ce1_scale.yaml` | 25 synthetic buildings, a week | numpy | ~0.3 s |
| **`default.yaml`** | **the real 25 Annex 96 homes, 30 days** | CityLearn + PyTorch | ~2 min |

Develop against the synthetic stock because the loop is fast, then confirm on
`default.yaml`. The real stock needs a clone of the exercise repo:

```bash
git clone https://github.com/kkaspar10/annex96_common_exercise_1 ../annex96_common_exercise_1
pip install torch
python -m annex96p2p run --config config/default.yaml
```

The differences between the two stocks are real and documented in
[docs/citylearn.md](docs/citylearn.md) -- the real homes all have both PV and a
large heat pump, comfort is much harder to hold, and the comfort price is
calibrated differently as a result. Mechanisms transfer; numbers do not.

Then:

```bash
cp submissions/example_team.py submissions/team_yourname.py
# edit BUILDINGS and the two methods
python -m annex96p2p check submissions/team_yourname.py
python -m annex96p2p run --config config/default.yaml
```

Every building you do **not** claim is run by a built-in default agent, so you
always have a full market of opponents to trade against, even alone on your
own laptop with only your own file present.

---

## Submission rules

Your submission is **one file** in `submissions/`. Everything below is checked
either by the runner or by `scripts/scan_submissions.py`, so it is worth
reading once rather than discovering by rejection.

**It must define, at module level:**

| name | required | meaning |
|---|---|---|
| `BUILDINGS` | yes | list of the buildings you control |
| `AGENT` | yes | your `Agent` subclass, or an instance of it |
| `NAME` | no | your team name; defaults to the filename |

### Use whatever libraries you like

**There is no restriction on what you may import.** PyTorch, TensorFlow,
JAX, scikit-learn, XGBoost, Gymnasium, stable-baselines3, CVXPY — train a
policy, fit a forecaster, solve an MPC problem, whatever you want to build.
There are optional extras for the common cases:

```bash
pip install -e ".[ml]"      # torch, scikit-learn, safetensors
pip install -e ".[rl]"      # torch, gymnasium, stable-baselines3, safetensors
```

They are extras rather than dependencies so a plain clone still installs in
seconds.

### The real requirement: you have to explain it

The bar is not that your agent scores well. It is that **you can explain what
it does and why, out loud and in writing.** A score you cannot account for is
worth nothing.

So expect to answer, about your own submission:

- what your bid curve represents, and why it has the shape it has
- why your agent buys or sells at a particular hour, in terms of the physics
  and the prices rather than "the model decided"
- where its remaining losses are, and what you would do next
- if you trained something: what the state and reward were, what it learned
  that a rule could not have expressed, and how you know it is not just
  memorising the price series

A hand-written 40-line rule you fully understand is a better submission than
a black box that scores marginally lower and that you cannot account for. If
you use a learned policy, you are responsible for being able to say what it
learned.

### Practical constraints

Not restrictions on your toolkit — just what the runner needs in order to
work:

- **claim only the buildings you were assigned.** Actions returned for
  buildings you do not own are silently discarded.
- **use only the observations you are handed.** Reaching for information the
  firewall does not give you is not a technical impossibility, it is
  academic dishonesty, and it will be obvious the moment you have to explain
  your agent.
- **build your model in `reset()`, not at import time.** Module-level code
  runs before any guard exists.
- **stay inside the compute limits below.** These are enforced.
- **prefer `.npz` or `.safetensors` to a pickled checkpoint** — see
  [Shipping a trained model](#shipping-a-trained-model). Not a prohibition:
  a `.pt` file executes code when loaded and nobody can read it, so it is a
  courtesy to whoever runs your submission and to whoever has to review it.
- **files starting with `_` are ignored**, so `_scratch.py` is a safe place
  to experiment.

Two things are explicitly **allowed**, and are the point of the exercise:
**strategic bid shading** — you are never obliged to bid your true valuation —
and **coordinating with another team**, if you do it openly and write about
what happened.

Check yourself before handing anything in:

```bash
python -m annex96p2p check submissions/team_yourname.py     # does it load?
python scripts/scan_submissions.py submissions/team_yourname.py   # what does it do?
```

The scan is a **report, not a gate** — it lists what your file touches
(filesystem, network, dynamic code) so whoever runs it knows what to expect,
and so those choices come up when you present. It blocks nothing.

### How much compute you get

Two limits, both set in the config file, both reported in a **COMPUTE**
section after every run:

| setting | default | what happens when you exceed it |
|---|---:|---|
| `call_timeout_seconds` | 5.0 | that single `bid()` or `dispatch()` gets an **empty bid** (or idle actions) and is flagged. The run continues. |
| `run_budget_seconds` | 120.0 | you are **disqualified for the rest of the run** — empty bids, idle actions, and your agent is not called again. |

For scale: the shipped example agent uses about **0.02 seconds in total** for a
whole simulated week. A small neural net doing inference twice per timestep is
still nowhere near either limit. The 5-second per-call figure is deliberately
roomy because the *first* call also pays for importing torch and building your
model.

What will get you into trouble is training during the run, or an accidental
infinite loop. Note the asymmetry: a call that **raises** is rescued by the
default agent, so a crash costs you one timestep of good behaviour. A call
that **overruns** is not rescued — you get an empty bid, so you simply do not
trade that hour. Slow is punished harder than broken, on purpose.

The per-run budget exists because a timed-out call cannot actually be killed —
Python has no way to stop a running thread. The overrunning call is abandoned
and keeps consuming CPU, so the budget is what stops a spinning agent being
re-entered for every remaining timestep.

### Shipping a trained model

Put the weights file next to your submission and load it in `reset()`:

```python
from annex96p2p import Agent, load_weights

class MyAgent(Agent):
    def reset(self):                       # not at import time
        w = load_weights("team_yourname.npz")
        self.W1, self.b1 = w["W1"], w["b1"]
```

`load_weights` accepts `.npz` and `.safetensors` and resolves the name inside
`submissions/`, so your agent works whichever directory the run started from.
Nothing stops you opening a file yourself — this just gets the path right and
keeps the checkpoint readable. Converting what you trained is two lines:

```python
import numpy as np, torch
sd = torch.load("model.pt", map_location="cpu")        # on YOUR machine
np.savez("submissions/team_yourname.npz",
         **{k: v.numpy() for k, v in sd.items()})
```

Then hand in both files. Train offline, keep inference deterministic — seed
everything, `eval()`, `no_grad()` — so your result reproduces.

---

## How the market works

### Where the money goes

Each hour, for each participant:

```
bill = clearing_price x p2p_quantity          what you paid or were paid in the market
     + grid_price     x grid_import           whatever you did not cover in the market
     + $3.00          x degree_hours          every degC-hour outside the comfort band
```

Comfort is priced rather than merely reported. Without that, the cheapest
strategy is to switch the heat pump off and let the building drift, which is
not a strategy anybody wants you to find.

### You cannot export to the grid

This rule is carried over unchanged from Common Exercise 1, and it is the
reason the market exists. Surplus PV that does not sell to a peer is
**curtailed and earns nothing**. So a seller will accept almost any price
above zero, and there is real value sitting in the market waiting to be
captured.

### Bidding

Every hour you submit **one signed number per price level**. Levels run in
one cent steps from `$0.00` to the current grid price:

| bound | why it is there |
|---|---|
| `$0.00` floor | you cannot export, so unsold surplus is worth nothing |
| grid price ceiling | you can always buy from the utility, so nobody pays more inside the market |

Positive means *buy that much at that price or cheaper*. Negative means
*sell that much at that price or dearer*.

The market clears at a single uniform price — the one that maximises the
traded volume, which is the rule real power exchanges use. Everyone in the
money trades at that same price regardless of what they bid, the short side
fills completely, and the long side is rationed pro rata.

### The order within an hour

```
1. you observe        only the buildings you control
2. bid(obs, mkt)      your bid vector
3. the market clears  centrally, one uniform price
4. dispatch(...)      your heat pump and battery, knowing what you cleared at
5. physics            one timestep
6. settlement         written to the ledger
```

Bidding happens *before* dispatch on purpose. Your bid is a statement about
what you are willing to pay; your dispatch then happens knowing what you
actually got.

### What you are told

Your own buildings, the current grid price, the legal price grid, and **last
hour's clearing price and volume**. That is all. You never see another
participant's bid, their load, their portfolio, or who you traded with.

### There is no penalty for bidding badly

There is no obligation to deliver what you bid and no imbalance charge,
because none is needed. The price bounds already punish dishonesty:

- **Sell more than you have** and you cover the gap from the grid at the full
  retail price, having been paid the lower clearing price for it.
- **Buy more than you can use** and the excess is curtailed. You paid for
  energy that vanished.

The grid is always the balancing party. Your only real obligation is to make
your bid and your dispatch agree with each other.

---

## Writing a submission

One file in `submissions/`, two module-level names, two methods:

```python
from annex96p2p import Action, Agent

BUILDINGS = ["B01", "B02", "B03"]     # which buildings you control
NAME = "team_yourname"                # optional, defaults to the file name

class MyAgent(Agent):
    def bid(self, obs, mkt):
        """Return one signed quantity per price level in mkt.price_grid."""
        bid = mkt.new_bid()
        need = sum(o.passive_net_kwh for o in obs.values())
        if need > 0:
            bid.buy(price=mkt.grid_import_price - 0.01, quantity=need)
        else:
            bid.sell(price=0.02, quantity=-need)
        return bid.array

    def dispatch(self, obs, mkt, result):
        """Return one Action per building. result.price / result.quantity
        tell you what you just cleared at."""
        return {name: Action(hvac=-1.0 if o.indoor_temperature > o.comfort_high - 0.5
                             else 0.0,
                             battery=0.0)
                for name, o in obs.items()}

AGENT = MyAgent
```

`python -m annex96p2p buildings` lists what is available and what each
building is made of. Files starting with `_` are ignored, so `_scratch.py` is
a safe place to experiment.

---

## The API in full

Three things come in, one goes out. There is nothing else available to you —
this is the complete surface.

### `obs` — a dict of `BuildingObs`, keyed by building name

One entry per building you control, and nothing at all about the others.
Named fields, so no magic indices.

| field | unit | meaning |
|---|---|---|
| `name` | | building name, e.g. `"B01"` |
| `timestep` `hour` `day` | | `hour` is 0–23, `day` is 0-based |
| `outdoor_temperature` | °C | |
| `solar_irradiance` | 0–1 | fraction of peak |
| `indoor_temperature` | °C | |
| `comfort_low` `comfort_high` | °C | edges of the comfort band |
| `base_load_kwh` | kWh | non-shiftable load **this** timestep |
| `solar_generation_kwh` | kWh | PV output this timestep; you cannot curtail it |
| `battery_soc_kwh` | kWh | current charge |
| `battery_capacity_kwh` | kWh | 0 if this building has no battery |
| `battery_max_power_kw` | kW | charge/discharge rating |
| `hp_max_electric_kw` | kW | heat pump draw at full output |
| `hp_cop` | | coefficient of performance |
| `last_net_kwh` | kWh | your net energy in the **previous** timestep |

Convenience properties:

| property | meaning |
|---|---|
| `passive_net_kwh` | net energy if you leave heat pump and battery idle. **Start here** — positive means you must buy, negative means you have surplus. |
| `has_battery` `has_pv` | booleans |
| `comfort_mid` | midpoint of the band |
| `is_uncomfortable` | currently outside the band |

### `mkt` — a `MarketInfo`

Identical for every participant. Note what is absent: anyone else's bid,
load, or identity.

| field / method | meaning |
|---|---|
| `grid_import_price` | $/kWh you pay the utility this hour — your fallback, and the bid ceiling |
| `grid_export_price` | $/kWh the utility pays you. `0.0` means unsold surplus is curtailed. |
| `price_grid` | numpy array of the legal bid prices: `0.00 … grid_import_price` in $0.01 steps |
| `n_levels` | `len(price_grid)` |
| `last_clearing_price` | previous hour's clearing price, or `None` if it did not clear |
| `last_clearing_volume` | kWh traded in the previous hour |
| `hour` `day` `timestep` | |
| `empty_bid()` | a zero array of the right length for this hour |
| `level(price)` | index of the nearest legal price level |
| `new_bid()` | a `Bid` helper (below) |

### `Bid` — the helper for building a bid vector

```python
bid = mkt.new_bid()
bid.buy(price=0.18, quantity=4.0)    # buy up to 4 kWh at $0.18 or cheaper
bid.sell(price=0.06, quantity=2.0)   # sell up to 2 kWh at $0.06 or dearer
return bid.array                     # a numpy array of length mkt.n_levels
```

Prices snap to the nearest legal level. Each level holds **one signed
number**, so buying and selling at the same level cancels out. You may build
the array yourself instead — positive to buy, negative to sell.

### `result` — a `TradeResult`, passed to `dispatch()` only

| field | meaning |
|---|---|
| `price` | the clearing price this hour, or `None` if nothing traded |
| `quantity` | **your** net position: `> 0` you bought, `< 0` you sold |
| `traded` | convenience boolean |

### `Action` — what you return, one per building

| field | range | meaning |
|---|---|---|
| `hvac` | −1 … +1 | −1 full cooling, 0 off, +1 full heating |
| `battery` | −1 … +1 | +1 charge at full power, −1 discharge at full power |

Both are **fractions of rated power, not amounts of energy** — the single most
common source of a mismatch between what you bid and what you consume. Values
are clipped, and the battery is additionally limited by its state of charge
and remaining capacity.

---

## Debugging your agent

Every run writes `results/<name>/settlements.csv` — one row per participant
per hour, and the single source of truth. Every number in the printed report
is derived from it. Read it when something looks wrong:

```bash
python -m annex96p2p run --config config/default.yaml
python -c "import pandas as pd; d = pd.read_csv('results/default/settlements.csv'); \
print(d[d.participant == 'team_yourname'].head(48).to_string())"
```

The useful columns are `net_kwh` (what you physically did), `p2p_kwh` (what
you traded), `grid_import_kwh`, `curtailed_kwh`, `clearing_price`,
`degree_hours` and `total_cost`.

| symptom in the ledger | what it means |
|---|---|
| `curtailed_kwh` is large | you paid for energy you could not use, or had surplus with a full battery. Money thrown away. |
| `p2p_kwh` and `net_kwh` have **opposite signs** | your bid and your dispatch disagree. This is the classic bug — you declared yourself a buyer and then produced, or vice versa. |
| `grid_import_kwh` high in cheap hours | your bid did not clear. Either you bid too low, or you did not bid for the energy your heat pump and battery were about to use. |
| `clearing_price` is empty when you wanted to sell | the market did not cross. Your reserve price was above what anyone would pay. |
| `degree_hours > 0` | you drifted outside the comfort band, at $3.00 per °C·hour. |
| `total_cost` > `baseline_cost` | you are doing worse than buying everything from the grid and never trading at all. |
| your score is above 1.000 | you are losing to the default agent. Compare hour by hour against a run with your file removed. |

The market itself is in `results/<name>/market.csv` — clearing price and
volume per hour, which is where you look to understand *why* your bid filled
or did not.

---

## Troubleshooting

**`python` opens the Microsoft Store, or "Python was not found" (Windows).**
That is the App Execution Alias stub, not Python. Use `py` instead of
`python` for every command in this README, or install from python.org with
"Add python.exe to PATH" ticked.

**`No module named annex96p2p`.** You skipped `pip install -e ".[dev]"`, or
you are in a different virtual environment than the one you installed into,
or you are not in the repository root. Re-run the install from the root.

**`pip install -e .` fails with a setuptools or backend error.** Upgrade pip
first: `python -m pip install --upgrade pip`. The build needs
`setuptools >= 68`.

**`pytest` passes but `python -m annex96p2p` does not.** You are almost
certainly in the wrong directory — run it from the repository root, where
`config/` and `submissions/` live.

**My submission is ignored.** Three usual causes: the filename starts with
`_`; there is no module-level `AGENT` or `BUILDINGS`; or it raised on import.
`python -m annex96p2p check submissions/team_yourname.py` tells you which.

**"claims buildings that do not exist".** Run
`python -m annex96p2p buildings` to see the real names and what each one is
made of.

**No `results/` folder.** Either `save_csv: false` in your config, or you are
looking in the wrong place — the path comes from `out:` in the config file.

**Everything is very slow.** You are on the CityLearn backend. Set
`backend: mock` for development; the mock runs a full week in about a tenth of
a second.

---

## Scoring

Raw bills are **not** comparable between participants — a team with five
buildings will always pay more than a team with one, and a team that happens
to own a lot of PV will always pay less. So every participant is scored
against the same buildings run by the built-in default agent:

```
score = your total bill / the default agent's bill on your buildings

score < 1.00   you beat the default agent
score = 1.00   you matched it
score > 1.00   you did worse than doing nothing clever
```

This is deliberately **opponent-invariant**: it does not depend on how good
the other submissions are, so the number you see on your laptop is the number
you get in the tournament. The runner computes it automatically by running a
second, all-default simulation over the same building groups.

---

## The default agent, and how to beat it

Every unclaimed building is run by `SimpleRBCAgent` in
[`baselines.py`](src/annex96p2p/baselines.py). It is crude on purpose. Read
it — its weaknesses are the assignment.

**Its bidding:**
- bids at exactly one price level, so it has no reservation curve at all
- as a buyer it offers one cent under the grid price, so it almost always
  fills and almost always pays the most it possibly could
- as a seller it dumps everything at one cent, handing essentially all the
  gains from trade to whoever buys
- it bids its *passive* position and ignores that it is about to move a
  battery, so its bid and its dispatch disagree every single hour
- it never looks at the previous clearing price

**Its dispatch:**
- bang-bang thermostat with a half degree deadband
- never pre-cools, so it uses none of the free storage in the building fabric
- battery runs on a wall clock — charge 10:00–15:00, discharge 18:00–21:00 —
  with no reference to price, PV output, or state of charge
- nothing reacts to the price it just cleared at

Two worked examples ship, in deliberately different styles. Read both — they
fail in different ways.

| file | style | score | what it demonstrates |
|---|---|---:|---|
| `example_team.py` | a rule stack — if-statements about the price | ~0.97 | price-aware battery, bid/dispatch consistency |
| `example_soc_spread.py` | a market maker — quotes around a reservation price | ~0.79 | pricing storable vs perishable energy separately |

`example_team.py` sits close to 1.0 on purpose. The default agent is
thermally competent — it has to be, or the score would just measure who can
hold a thermostat — so beating it takes real work on the market side. That is
the assignment.

`example_soc_spread.py` is worth studying even if you write nothing like it.
It sets its mid-price at `(1 - state_of_charge) x grid_price` and quotes a
spread around that, which means it buys the midday glut and sells into the
evening peak with **no forecast at all** — the inventory does the reasoning.
It also separates the three positions a portfolio actually holds: must-have
load, perishable PV surplus, and storable battery charge. Pricing the second
with the rule meant for the third curtailed 680 kWh in a week.

Both leave a lot on the table: pre-cooling, forecasting, strategic bid
shading, and reasoning about when the market will be glutted.

Three things worth understanding early, all of which the example gets wrong
in its git history:

1. **Net across your own buildings before you bid.** Charging a battery from
   your own roof is a transfer between two buildings you already own. It
   should never touch the market.
2. **Self-consume stored energy before selling it.** A kWh out of your battery
   that you use yourself saves the full grid price. The same kWh sold into the
   market earns only the clearing price, which is capped below it.
3. **Scale your actions to what you bid for.** `Action.battery` is a fraction
   of rated power, not an amount of energy. Sending `1.0` when you only bid
   for 2 kWh pulls the rest off the grid at full retail.

---

## Before you submit

```
[ ]  python -m annex96p2p check submissions/team_yourname.py     prints OK
[ ]  python scripts/scan_submissions.py submissions/team_yourname.py   exits clean
[ ]  python -m pytest                                            still passes
[ ]  a full run completes with no WARNINGS section naming your agent
[ ]  your score is below 1.000
[ ]  curtailed_kwh is small — you are not paying for energy you discard
[ ]  degree_hours is small — you are not buying your way out of comfort
[ ]  BUILDINGS matches what you were assigned, and nothing else
[ ]  no COMPUTE timeouts or disqualification in the report
[ ]  you can explain every decision your agent makes, without
     saying "the model decided"
```

A reasonable submission is 60–150 lines. If yours is much longer, most of it
is probably not earning anything.

---

## Repository layout

```
src/annex96p2p/
  api.py          Agent, Action, BuildingObs, MarketInfo -- what you import
  market.py       the auction: price grid, clearing, bid validation
  tariff.py       grid prices, export price, the comfort penalty
  ledger.py       settlement, and the one table everything derives from
  runner.py       the simulation loop and the information firewall
  registry.py     finding submissions, resolving claims, filling the rest
  reference.py    the all-default run that makes scores comparable
  baselines.py    the default agents
  weights.py      load_weights() -- the sanctioned way to read trained weights
  backends/
    mock.py       self-contained building simulator (the default)
    citylearn_backend.py   the real Annex 96 neighbourhood (opt-in)
submissions/      drop your file here; two example_*.py templates
config/           smoke.yaml (fast), default.yaml (a week), ce1_scale.yaml (25 buildings)
examples/         explain_market.py, check_partition.py
scripts/          scan_submissions.py -- the safety scan
docs/             assignment.md, security.md, citylearn.md
tests/            77 tests; run them to verify your checkout
```

Results land in `results/` as `settlements.csv`, `market.csv` and
`leaderboard.csv`. `settlements.csv` has one row per participant per hour and
is the single source of truth — every number in the report is derived from it.

---

## For instructors

Running everyone together is the same code path. Drop every submitted file
into `submissions/` and:

```bash
python -m annex96p2p tournament --config config/default.yaml
```

`tournament` differs from `run` in two ways: overlapping building claims
become a hard error instead of a warning, and if `assignments:` is set in the
config, a submission claiming a building it was not assigned is rejected.

To pin the partition, uncomment the `assignments` block in
[`config/default.yaml`](config/default.yaml).

### Specialise the portfolios — this is the setting that breaks the exercise

The intuitive choice is the wrong one. Giving each team "one of each
archetype" feels fair and complementary, and it is exactly what kills the
market: a team that can balance itself internally will, and should, because
moving energy between two buildings you already own is free and never touches
the market.

Same twelve buildings, same default agent, same prices, one simulated week —
the only thing that changes is where the ownership boundaries fall:

| partition | traded | value created |
|---|---:|---:|
| each team one archetype | 328 kWh | $42.25 |
| a dominant archetype each | 199 kWh | $25.39 |
| one of each archetype per team | 23 kWh | $1.99 |
| one participant per building (12 players) | 328 kWh | $42.25 |

```bash
python examples/check_partition.py     # reproduces the table, and tests your own
```

Two consequences. **Specialise:** give one team the PV and another the
batteries, so they have to trade with each other. **More participants means a
deeper market:** twelve students holding one building each trade about as much
as four teams of three, because every ownership boundary is a boundary energy
has to cross by trading. With a small cohort, prefer one building per student.

Specialised teams do face genuinely different problems, which makes for a
better discussion but means scores are not perfectly like-for-like. If you
want them comparable, rotate the assignment between labs and average — do not
flatten the portfolios to fix it.

### Matching the real neighbourhood

The real Annex 96 CE1 dataset has **25 homes** (both the Texas and Vermont
schemas define exactly 25, all with PV and a battery). The mock backend
defaults to 12, but takes any exact count:

```bash
python -m annex96p2p run --config config/ce1_scale.yaml   # 25 buildings, 5 teams
python -m annex96p2p buildings --n-buildings 25
```

[`config/ce1_scale.yaml`](config/ce1_scale.yaml) ships a worked five-team
partition at that size — 553 kWh traded over a week, clearing in 46% of hours.
Designing a lab there means the building count and the `assignments:` block
carry over to the CityLearn backend unchanged; only the building names need
remapping, since CityLearn uses the ResStock ids.

Student code is called inside a guard: exceptions, malformed bids, wrong-length
vectors, NaNs and junk return values are all repaired or substituted, counted,
and reported. One broken submission cannot take down a tournament run.

### Before you run collected submissions

Those guards protect against **mistakes**, not against **attacks**. A
submission is a Python file and the runner imports it, so it executes with
your privileges. There is no sandbox.

```bash
make scan          # static AST scan, does not import anything
```

This flags dangerous imports, `eval`/`exec`/`open`, dunder sandbox escapes,
`setattr` monkey-patching, imports of sibling submissions, `while True` with
no break, and code that runs at import time. It is a smoke detector, not a
sandbox.

The actual protection is isolation. Either have students submit by pull
request so [CI](.github/workflows/ci.yml) runs everything on a throwaway
runner, or run the tournament in a container:

```bash
make docker-tournament
```

**Read [docs/security.md](docs/security.md) before running twenty collected
files on the machine that holds your gradebook.** It has the full threat
model, a table of what the guards do and do not stop, and the rules to put in
the assignment brief.

Everything is deterministic — same submission, same numbers, on any machine.

### Using the real CityLearn neighbourhood

The mock backend is a teaching model, not a research model. To run against the
actual Annex 96 CE1 homes, see [docs/citylearn.md](docs/citylearn.md). Agent
code is identical across both backends. That adapter is **not yet verified**
against the upstream dataset and needs one checking pass.

---

## Licence

MIT. The upstream Common Exercise 1 material remains under its own terms.
