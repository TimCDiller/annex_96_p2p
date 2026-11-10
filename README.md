# Peer-to-peer electricity trading

A graduate course exercise, adapted from
[IEA EBC Annex 96 Common Exercise 1](https://github.com/kkaspar10/annex96_common_exercise_1).

The original exercise asks one controller to keep a whole portfolio of homes
tracking a flat reference load. This version changes two things:

1. **Electricity has a time-varying price**, and the objective is to minimise
   the sum of everybody's bills instead of flattening a load profile.
2. **Each student controls only a subset of the buildings**, and everyone
   trades energy with each other in a peer-to-peer market.

**The course runs on the Vermont winter stock** — a January in Vermont, twenty
homes heated by heat pumps, with PV and batteries redistributed so that there
is something to trade (details in [The course market](#the-course-market)).
Everything in this README is about that market unless it says otherwise.

**Reading order:** [Getting set up](#getting-set-up) →
[Testing your agent](#testing-your-agent) → [Handing in](#handing-in) →
[Submission rules](#submission-rules) → [How the market works](#how-the-market-works) →
[Writing a submission](#writing-a-submission) → [The API in full](#the-api-in-full).
Then [Debugging your agent](#debugging-your-agent) when something looks wrong.

---

## Getting set up

You need Python **3.10** (the tested version), Git, and about 2 GB of disk for
PyTorch. A CPU is enough. Run every command from the repository root.

```bash
git clone -b release/course-v1 https://github.com/TimCDiller/annex_96_p2p.git
cd annex_96_p2p
python -m venv .venv
```

Activate the environment — `.venv\Scripts\Activate.ps1` in PowerShell, or
`source .venv/bin/activate` on Linux/macOS — then:

```bash
python -m pip install -r scenarios/requirements.txt
python -m pip install -e .

# the upstream building models and CityLearn fork, pinned to a fixed commit
git clone -c core.longpaths=true https://github.com/kkaspar10/annex96_common_exercise_1 ../annex96_common_exercise_1
git -C ../annex96_common_exercise_1 checkout 28ab31e88f9f4e1559bca7be3aaae8c918a20f83

# generate the course market from it (writes generated/altered_stock/)
python -m annex96p2p.altered_stock build

# confirm the installation
python -m pytest -q
```

Use `scenarios/requirements.txt`, not a CityLearn release from PyPI: the
runner imports the exercise's own CityLearn fork from the upstream checkout,
and the requirements pin everything that fork needs.

`generated/altered_stock/` is not in Git. Every student builds it locally from
the pinned upstream commit, so everyone gets byte-identical data; the `run`
command checks the hashes and refuses to start if a generated file was edited.

### The course market

The stock is the first 20 homes of the upstream Vermont January neighbourhood
(`annex96_ce1_vt_neighborhood`, 743 hourly steps), with the weather, the
occupants' demand and setpoints, the heat pumps and the building thermal
models unchanged. Only the PV and batteries are redistributed, alternating
house by house:

| | PV-rich homes (10) | storage-rich homes (10) |
|---|---|---|
| PV | 40 kW | 1 kW |
| battery | 4 kWh / 1 kW | 80 kWh; five at 80 kW (1C), five at 20 kW (0.25C) |

That is the `balanced` configuration, and it is **the one you are graded
on**. A second configuration, `solar_abundant`, gives the PV-rich homes 60 kW
instead; it is there for comparison, not grading.

All batteries start empty and have a 90% round trip. Prices follow a
"duck-shaped" retail tariff: a morning peak of $0.34 at 07:00, an evening peak
of $0.45 at 18:00, and a midday low of $0.055 at 13:00, varied by ±10% from
day to day. Grid export earns nothing. The full equipment table for your
buildings is in `generated/altered_stock/balanced/buildings.csv`; the
recipe is [scenarios/altered_stock.yaml](scenarios/altered_stock.yaml).

---

## Testing your agent

**`submissions/` is where you test.** Put your agent there as one `.py` file,
starting from one of three starting points:

| file | what it is |
|---|---|
| [`submissions/_template.py`](submissions/_template.py) | inherits the default agent unchanged — the smallest legal submission |
| [`agents/basic/half_battery.py`](agents/basic/half_battery.py) | a complete simple agent written out in full: holds its battery at 50%, sells surplus from 20% of the grid price, buys shortfall up to 80% |
| [`agents/basic/market_player.py`](agents/basic/market_player.py) | the default agent's physical plan with different prices: buys energy for storage only at $0.02 or less, sells in the peaks only at 80% of the grid price or more — see [what it does to the market](#one-player-moves-the-market-for-everyone) |

```bash
cp submissions/_template.py submissions/team_yourname.py     # or one of the agents/basic/ files
# set NAME and BUILDINGS to your Canvas assignment, then edit the agent
```

Every agent in this repository is in [`agents/`](agents/): `basic/` holds the
simple ones above, `advanced/` the stronger opponents described in
[Choosing your opponents](#choosing-your-opponents-the-field). The one
exception is the default agent, which the runner itself needs, in
[stock_agents.py](src/annex96p2p/stock_agents.py).

Files starting with `_` are ignored by the runner, which is why the
starting points are never loaded themselves. It also makes `_scratch.py` a
safe place to experiment.

Run your file in the course market:

```bash
python -m annex96p2p.altered_stock run --configurations balanced --modes competitive \
    --days 7 --submissions submissions --out results/team_yourname
```

Every house you do not claim is run by the default agent,
`StorageServiceAgent` in [stock_agents.py](src/annex96p2p/stock_agents.py),
one participant per house. So your market is full of counterparties even when
yours is the only file in `submissions/`. It is not a weak opponent: it holds
the comfort band with a smoothed thermostat, fills its battery from cheap
midday and overnight power before each price peak, sells into the peaks, and lets its battery absorb
forecast error in real time (`net_target_kwh`, see [`Action`](#action--what-you-return-one-per-building)).

`--days 7` takes about 20 seconds; drop it for the full month (a minute or
two) once your agent works. The run **stops with an error** if your file does not load, claims a
building that does not exist, claims a building another file in `submissions/`
also claims, or if your agent raises an exception or times out at any point.
Fix those before you look at any numbers.

Results land in `results/team_yourname/balanced/competitive/`; see
[Debugging your agent](#debugging-your-agent) for what each file is.

### Choosing your opponents: the field

By default every house you do not claim runs the default agent. **`--field`
switches who runs them instead** — this is the one place to change it:

```bash
python -m annex96p2p.altered_stock run --configurations balanced --modes competitive \
    --days 7 --submissions submissions --out results/team_yourname \
    --field default        # default | basic | advanced | diverse
```

| `--field` | who runs the houses you do not claim |
|---|---|
| `default` | the default agent on every house — **the graded setting**, and what you get without `--field` |
| `basic` | **simple opponents**: the default agent, [`half_battery`](agents/basic/half_battery.py) and [`market_player`](agents/basic/market_player.py), each on a third of the houses of every kind |
| `advanced` | **complex opponents** on every house: *market forecast battery management*, *marginal value* and *team_opt_hp*, described in [agents/advanced/README.md](agents/advanced/README.md) |
| `diverse` | simple and complex side by side, spread evenly over the kinds of house — the most varied market to test in |

The fields are defined in [agents/fields.py](agents/fields.py). Houses you
claim are taken out of the field, so you always play your own houses against
whatever runs the rest. Copy a field there to build your own, and run it with
`--field <its name>`. In the hourly page your participant is starred (★) and
the field's agents are marked ◆; the advanced ones also draw their buying and
selling thresholds on their bid panel.

An agent that only beats the default agent has learned to beat the default
agent. Test in more than one field before you trust a result — the
[advanced agents' study](agents/advanced/README.md) shows strategies that win
against default agents and lose, or stop mattering, against each other.

### Is it any good?

Compare against the same houses run by the default agent. Run once **without**
`--submissions`, so that every house is a default agent, then add up the bills
of your buildings in both runs:

```bash
python -m annex96p2p.altered_stock run --configurations balanced --modes competitive \
    --days 7 --out results/baseline
```

```python
import pandas as pd
mine = ["resstock-amy2018-2021-release-1-112208", "resstock-amy2018-2021-release-1-179247"]
me   = pd.read_csv("results/team_yourname/balanced/competitive/settlements.csv")
base = pd.read_csv("results/baseline/balanced/competitive/settlements.csv")
print("you:     ", me[me.participant == "team_yourname"].total_cost.sum())
print("default: ", base[base.participant.isin(mine)].total_cost.sum())
```

In the baseline run each house is its own participant, named after the
building. Run the same `--days` in both, or the comparison means nothing.

To see what your agent did hour by hour — its trades, battery charge, grid
imports and every bid it made as a price-time heatmap — plot the run:

```bash
python examples/hourly_trades.py --run results/team_yourname/balanced/competitive
```

and open `hourly_trades.html` in that folder. Your agent is starred in the
selector and the page opens on it.

### One player moves the market for everyone

Prices are uniform: everyone who trades in an hour gets the same clearing
price: of the prices that trade the most energy, the one where demand and
supply are closest (see [Bidding](#bidding)). So a single participant's bids
can move the price for the whole market. Two pre-generated pages show it on
the full month, every other house
running the default agent:

- [docs/runs/all_default.html](docs/runs/all_default.html): all 20 houses on the default agent;
- [docs/runs/market_player.html](docs/runs/market_player.html): house 199613
  (storage-rich, 80 kWh, 1C) runs [`market_player.py`](agents/basic/market_player.py) instead.

Download them, or open them from your clone; GitHub does not render HTML
pages. In the second, the selector opens on the player and its bid heatmap
shows the strategy: cheap storage bids at $0.00–0.02, asks only in the top
fifth of each peak.

| full month, balanced | all default | with the market player | change |
|---|---:|---:|---:|
| the player's house | $420.33 | $393.89 | −$26.44 (−6.3%) |
| clearing price in the hours it sold | 66% of grid | 90% of grid | |
| the other nine storage-rich homes | $3,721 | $3,539 | −$181 |
| the ten PV-rich homes | $5,705 | $5,923 | +$217 |
| all 20 houses | $9,846 | $9,856 | +$9 |

One house holding out for 80% of the grid price lifted the peak price for
every seller in those hours, so the other storage owners gained nearly seven
times what the player did, without changing anything themselves. The PV-rich homes,
buying at the peaks, paid for all of it. Physically almost nothing changed:
curtailment, grid imports and traded volume each moved by less than 1%. Strategic
pricing moved money between participants; it did not create any. Keep that in
mind when you read your own result: your score depends on the others, and the
others' bills depend on you.

More experiments like this one — what beats the default agent, whether a house
can store heat, cartels, hardliners and cheaters — are written up in the
[scenario gallery](docs/scenarios/README.md).

---

## Handing in

**Hand in on Canvas.** Upload your one `.py` file — plus any weights file it
loads — and your written report, by the deadline posted there. Your building
assignment is also posted on Canvas.

Nothing in this repository submits anything. `submissions/` only exists on
your own machine so you can test; pushing to it, or opening a pull request,
is not a hand-in. Your instructor collects the Canvas files and runs everyone
together in the same market.

---

## Submission rules

Your submission is **one file**. Everything below is checked either by the
runner or by `scripts/scan_submissions.py`, so it is worth reading once
rather than discovering by rejection.

**It must define, at module level:**

| name | required | meaning |
|---|---|---|
| `BUILDINGS` | yes | list of the buildings you control — exactly your Canvas assignment |
| `AGENT` | yes | your `Agent` subclass, or an instance of it |
| `NAME` | no | your team name; defaults to the filename |

### Use whatever libraries you like

**There is no restriction on what you may import.** PyTorch, scikit-learn,
SciPy, Gymnasium, CVXPY — train a policy, fit a forecaster, solve an MPC
problem, whatever you want to build. PyTorch, scikit-learn and SciPy are
already in the course environment; anything else, say in your report what to
install.

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
a black box that scores marginally lower and that you cannot account for.

### Practical constraints

Not restrictions on your toolkit — just what the runner needs in order to
work:

- **claim only the buildings you were assigned.**
- **use only the observations you are handed.** Reaching for information the
  firewall does not give you — the generated data files, another
  submission, the ledger — is not a technical impossibility, it is academic
  dishonesty, and it will be obvious the moment you have to explain your
  agent.
- **build your model in `reset()`, not at import time.** Module-level code
  runs before any guard exists.
- **stay inside the compute limits:** 5 seconds per `bid()` or `dispatch()`
  call, 120 seconds for your agent over the whole run. Either one ends the
  run with an error. The default agent uses a fraction of a second for a
  whole month, and a small neural net doing inference twice an hour is still
  nowhere near the limits; training during the run is not.
- **prefer `.npz` or `.safetensors` to a pickled checkpoint.** A `.pt` file
  executes code when loaded and nobody can read it. Train offline, load the
  weights in `reset()`, and keep inference deterministic — seed everything,
  `eval()`, `no_grad()` — so your result reproduces.

Two things are explicitly **allowed**, and are the point of the exercise:
**strategic bid shading** — you are never obliged to bid your true valuation —
and **coordinating with another team**, if you do it openly and write about
what happened.

---

## How the market works

### Where the money goes

Each hour, for each participant:

```
bill = clearing_price x p2p_quantity          what you paid or were paid in the market
     + grid_price     x grid_import           whatever you did not cover in the market
     + $0.50          x degree_hours          every degC-hour outside the comfort band
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

Your bid is **netted** before it clears: at every price you count as a buyer
of what you bid to buy at that price or above, minus what you offered at that
price or below, or as a seller of the difference the other way, never both.
So a bid that buys above its own sells clears exactly like the
non-overlapping bid with the same net position, and you never trade with
yourself.

The market clears at a single uniform price — the one that maximises the
traded volume, which is the rule real power exchanges use. Everyone in the
money trades at that same price regardless of what they bid, the short side
fills completely, and the long side is rationed pro rata.

Bids are steps, so often a whole range of prices trades that same maximum
volume. The market then takes the price in that range where demand and supply
are **closest** — the end of the range next to where the two curves cross.
When supply is short that moves the price up the range, when demand is short
down it: excess demand pushes the price up, excess supply pulls it down. If
several prices are equally close, it takes the middle of those. So in the
hourly page's bid heatmap for the whole market, the clearing price sits next
to the boundary between red (more buying than selling) and green.

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
from annex96p2p import Action, Agent, Thermostat

BUILDINGS = ["resstock-amy2018-2021-release-1-112208"]   # from Canvas
NAME = "team_yourname"                                    # optional, defaults to the file name

class MyAgent(Agent):
    def reset(self):
        self.thermostat = Thermostat()

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
        return {name: Action(hvac=self.thermostat(o), battery=0.0)
                for name, o in obs.items()}

AGENT = MyAgent
```

That one is short to show the shape, and it has the classic bug: it bids
its passive position, then runs the heat pump without having bought for it.
[`half_battery.py`](agents/basic/half_battery.py) shows the
fix — decide the heat pump and battery once, in `bid()`, and let `dispatch()`
carry out the same plan. Call `Thermostat` once per hour only; it is a PI
controller and remembers its past calls. It also blends each command with the
previous hour's (`smoothing=0.5`), because the bare loop settles into a
two-hour on/off cycle on these heat pumps; the docstring in
[api.py](src/annex96p2p/api.py) has the measurements.

Or subclass the default agent and override only what you want to change,
as [`_template.py`](submissions/_template.py) does. Read
[stock_agents.py](src/annex96p2p/stock_agents.py) first.

One trap specific to this dataset: CityLearn's observations precede the
physical step by one hour, so the PV you observe is not quite the PV you will
produce. Selling exactly what you observed can leave you short, covering the
gap from the grid at full price. `hourly_audit.csv` shows how much of what
was sold was physically backed. Dispatching with
[`net_target_kwh`](#action--what-you-return-one-per-building) lets your battery
absorb that error instead of the grid, as far as its power allows — the
storage-rich homes almost entirely, the PV-rich homes' 1 kW battery only a
little. Your bid still has to be right; the market price of a bad bid remains.

---

## The API in full

Three things come in, one goes out. There is nothing else available to you —
this is the complete surface.

### `obs` — a dict of `BuildingObs`, keyed by building name

One entry per building you control, and nothing at all about the others.
Named fields, so no magic indices.

| field | unit | meaning |
|---|---|---|
| `name` | | building name, e.g. `"resstock-amy2018-2021-release-1-112208"` |
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
| `net_target_kwh` | kWh, optional | the grid exchange you want this hour instead of a battery rate; see below |

`hvac` and `battery` are **fractions of rated power, not amounts of energy** —
the single most common source of a mismatch between what you bid and what you
consume. Values are clipped, and the battery is additionally limited by its
state of charge and remaining capacity.

**`net_target_kwh` — let the battery follow the load.** A fixed `battery`
rate is decided before the hour, against a load and PV you can only forecast.
Set `net_target_kwh` instead — positive to draw that much from the market and
grid, negative to deliver that much, typically what you cleared — and the
battery behaves like a real home inverter: within the hour it charges whatever
surplus would otherwise be curtailed and discharges whatever shortfall would
otherwise come from the grid, so your meter reads the target. It is still
limited by its power rating and charge, and whatever it cannot absorb goes to
the grid or is curtailed as usual. `battery` is ignored when this is set. You
learn nothing new from it: the battery reacts to the hour physically, your
agent still never sees it. The default agent uses it.

---

## Debugging your agent

Every run writes these to `results/<out>/balanced/competitive/`:

| file | what it holds |
|---|---|
| `settlements.csv` | one row per participant per hour — **the single source of truth**; every other number derives from it |
| `market.csv` | clearing price and volume per hour |
| `hourly_audit.csv` | the market per hour, plus how much of what was sold was physically backed by surplus |
| `buildings_hourly.csv` | per house per hour: owner, net energy, PV, battery, state of charge, indoor temperature, degree-hours, heat-pump draw |
| `report.txt`, `leaderboard.csv`, `summary.json` | totals per participant and for the whole market |
| `assignments.json` | who owned which house in this run |
| `bids.csv` | every bid as submitted: participant, hour, price, kWh (+ buy, − sell) |
| `hourly_trades.html` | written by `examples/hourly_trades.py`: the run hour by hour, with a selector per house |

`results/<out>/comparison.txt` summarises every configuration and mode you ran.

```python
import pandas as pd
d = pd.read_csv("results/team_yourname/balanced/competitive/settlements.csv")
print(d[d.participant == "team_yourname"].head(48).to_string())
```

The useful columns are `net_kwh` (what you physically did), `p2p_kwh` (what
you traded), `grid_import_kwh`, `curtailed_kwh`, `clearing_price`,
`degree_hours` and `total_cost`.

| symptom in the ledger | what it means |
|---|---|
| `curtailed_kwh` is large | you paid for energy you could not use, or had surplus with nowhere to put it. Money thrown away. |
| `p2p_kwh` and `net_kwh` have **opposite signs** | your bid and your dispatch disagree. This is the classic bug — you declared yourself a buyer and then produced, or vice versa. |
| `grid_import_kwh` high in cheap hours | your bid did not clear. Either you bid too low, or you did not bid for the energy your heat pump and battery were about to use. |
| `clearing_price` is empty when you wanted to sell | the market did not cross. Your reserve price was above what anyone would pay. |
| `degree_hours > 0` | you drifted outside the comfort band, at $0.50 per °C·hour. |
| `total_cost` > `baseline_cost` | you are doing worse than buying everything from the grid and never trading at all. |
| your bill is above the [baseline run](#is-it-any-good)'s | the default agent does better on your houses. Compare the two hour by hour. |

---

## Troubleshooting

**`python` opens the Microsoft Store, or "Python was not found" (Windows).**
That is the App Execution Alias stub, not Python. Use `py -3.10` to create
the virtual environment, or install from python.org with "Add python.exe to
PATH" ticked. Inside the activated environment, `python` is fine.

**`No module named annex96p2p`, `scipy`, `torch`.** The virtual environment
is not active, or you skipped one of the two `pip install` lines. Activate it
and re-run both from the repository root.

**`Filename too long` when cloning the upstream repository (Windows).** Its
data files have very long names. Keep the `-c core.longpaths=true` in the
clone command; if you already have a half-checked-out copy, delete
`../annex96_common_exercise_1` and clone again.

**`No module named citylearn`.** The upstream checkout is missing or not at
`../annex96_common_exercise_1`. Clone it there, or pass
`--upstream <path>` to both `build` and `run`.

**`generated/altered_stock/... does not exist`, or an integrity error.** Run
`python -m annex96p2p.altered_stock build` again. Never edit generated files.

**My submission is not loaded.** The filename starts with `_`, or you passed
the wrong `--submissions` folder. Without `--submissions`, nobody's file is
loaded and every house runs the default agent.

**"claims ... which do not exist".** Copy the ids from
`generated/altered_stock/balanced/buildings.csv`; they are the long
`resstock-amy2018-2021-release-1-…` names.

**"Duplicate name or building claim".** Two files in `submissions/` claim the
same house or use the same `NAME` — usually a second copy of your own agent.
Rename the old one to start with `_`.

**"An agent failed or timed out".** Your agent raised or ran over its compute
limit somewhere in the run. `report.txt` is not written in that case; run
your agent's methods on a single step in a script to find the exception.

**The run dies with no Python error — "Windows fatal exception: access
violation", exit code 3221225477 — and your agent uses `scipy.optimize`
(`linprog`, `milp`).** SciPy's HiGHS solver crashes the process on Windows
when it is called from a new short-lived thread each time, and older
versions of the runner called every agent on a fresh thread each hour. It
typically struck a few days into the run. The runner now keeps one worker
thread per participant for the whole run, which fixes it with no change to
your code; `tests/test_fields.py::test_highs_runs_on_one_thread_per_participant`
checks it. If you still see it, update your checkout. To rule out your own
code, run with `python -X faulthandler -m annex96p2p.altered_stock run ...`,
which prints the Python stack of the thread that crashed.

---

## Before you hand in

```
[ ]  the course run (balanced, competitive, full month) completes with your file in submissions/
[ ]  python scripts/scan_submissions.py submissions/team_yourname.py   exits clean
[ ]  BUILDINGS matches your Canvas assignment, and nothing else
[ ]  curtailed_kwh is small — you are not paying for energy you discard
[ ]  degree_hours is small — you are not buying your way out of comfort
[ ]  you have compared your bill against the baseline run on the same houses
[ ]  you can explain every decision your agent makes, without
     saying "the model decided"
[ ]  the .py file (and any weights it loads) and your report are uploaded to Canvas
```

The scan is a **report, not a gate** — it lists what your file touches
(filesystem, network, dynamic code) so whoever runs it knows what to expect.

A reasonable submission is 60–150 lines. If yours is much longer, most of it
is probably not earning anything.

---

## Repository layout

```
src/annex96p2p/
  api.py            Agent, Action, BuildingObs, MarketInfo, Thermostat -- what you import
  stock_agents.py   StorageServiceAgent (the default agent) and EvilAgent
  altered_stock.py  builds and runs the course market
  stock_dataset.py  generates the altered stock from the pinned upstream data
  market.py         the auction: price grid, clearing, bid validation
  tariff.py         grid prices, export price, the comfort penalty
  ledger.py         settlement, and the one table everything derives from
  runner.py         the simulation loop and the information firewall
  registry.py       finding submissions, resolving claims
  fields.py         loads a field from agents/fields.py
  rc_thermal.py, rc_agents.py   optional RC thermal model and preheating agent
  baselines.py, reference.py, trading_agents.py   used by the original exercise
  backends/         the CityLearn adapter, and the interface it implements
agents/             every agent you can play against or start from:
  fields.py         the fields: who runs the houses you do not claim (--field)
  basic/            half_battery.py, market_player.py; original_exercise/ holds the original exercise's agents
  advanced/         market forecast battery management, marginal value, team_opt_hp, and their study
submissions/        test your agent here; _template.py to start from
scenarios/          the course-market recipe, pinned requirements, instructor guide, fitted RC models
generated/          the course market, built locally (not in Git)
examples/           reports and experiments (advanced_study.py: the advanced agents' study)
config/default.yaml the original exercise's config
docs/               assignment.md, security.md, citylearn.md, original_exercise.md;
                    runs/ holds the two pre-generated pages from the market-player example
scripts/            scan_submissions.py -- the safety scan
tests/              run python -m pytest to verify your checkout
```

---

## For instructors

[docs/assignment.md](docs/assignment.md) has a suggested lab sequence and
grading split. [scenarios/STUDENT_GUIDE.txt](scenarios/STUDENT_GUIDE.txt) has
the full detail of how the course market is built — what was changed and why,
provenance and hashes — and the experiments below.

### Running everyone together

Download the Canvas `.py` files (and any weights) into one folder —
`submissions/` on a clean checkout is fine — and run:

```bash
python scripts/scan_submissions.py submissions/
python -m annex96p2p.altered_stock run --configurations balanced --modes competitive \
    --submissions submissions --out results/class
```

Overlapping claims, unknown buildings and files that fail to import are hard
errors, so a clean start means the assignment was respected. Compare each team
against a run without `--submissions`, on the same houses, as in
[Is it any good?](#is-it-any-good). Do not read a bill-to-baseline ratio as
opponent-independent: in this stock the other participants are trading
partners, and a team's bill depends on them.

### Before you run collected submissions

A submission is a Python file and the runner imports it, so it executes with
your privileges. There is no sandbox. The scan above is a smoke detector, not
a sandbox: it flags dangerous imports, `eval`/`exec`/`open`, sandbox escapes,
monkey-patching, imports of sibling submissions, and code that runs at import
time. The real protection is isolation — run the class in a container or a
throwaway VM. **Read [docs/security.md](docs/security.md) before running
twenty collected files on the machine that holds your gradebook.**

### Experiments on the same stock

`--modes` selects who owns what. Besides `competitive` (20 independent houses
plus the students):

| mode | what changes |
|---|---|
| `pv_honest`, `storage_honest` | one owner controls six PV-rich or six storage-rich homes (30% of the stock), honestly |
| `pv_withhold`, `storage_withhold` | the same owner withholds supply to push prices up |
| `storage_flood` | the storage owner dumps battery energy at the peaks |
| `rc_preheat`, `rc_no_preheat` | every house runs a fitted RC thermal model, with or without pre-peak heating |

```bash
python -m annex96p2p.altered_stock run            # every configuration and mode, full month
python examples/altered_stock_report.py           # results/altered_stock/report.html
```

`strategic_effects.csv` compares each strategic owner with the honest owner of
the same six houses. The RC preheating exercise —
`examples/calibrate_rc.py`, `rc_preheat_report.py`, `rc_diagnostics.py` — is
described in the [student guide](scenarios/STUDENT_GUIDE.txt).

### The original exercise

The unaltered 25-home Vermont and Texas neighbourhoods, scored against
`SimpleRBCAgent` with `python -m annex96p2p run`, are still in the repository
for comparison: see [docs/original_exercise.md](docs/original_exercise.md).
Its agents are in `agents/basic/original_exercise/`, and
`python examples/explain_market.py` walks one market clearing through in real
numbers — useful in a first lecture whichever stock you use.

### Tests

```bash
python -m pytest                          # contracts and the course market; skips what is not installed
python -m pytest tests/test_citylearn.py  # the original 25-home stock, a couple of minutes
```

Contract tests run against `tests/stub.py`, a physics-free backend. Tests that
need the upstream data or the generated stock skip themselves when it is
absent. Everything is deterministic — same submission, same numbers, on any
machine.

## Licence

MIT. The upstream Common Exercise 1 material remains under its own terms.
