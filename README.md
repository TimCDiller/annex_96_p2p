# Peer-to-peer electricity trading

A graduate course exercise, adapted from
[IEA EBC Annex 96 Common Exercise 1](https://github.com/kkaspar10/annex96_common_exercise_1).

The original exercise asks one controller to keep a whole portfolio of homes
tracking a flat reference load. This version changes two things:

1. **Electricity has a time-varying price**, and the objective is to minimise
   the sum of everybody's bills instead of flattening a load profile.
2. **Each student controls only a subset of the buildings**, and everyone
   trades energy with each other in a peer-to-peer market.

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

The whole thing is numpy, pandas and pyyaml. No CityLearn, no PyTorch, no
dataset to download, and a full simulated week runs in about a tenth of a
second. The smoke run finishes instantly and prints a leaderboard.

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

## The rules

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

### What you get to look at

`obs` is a dict of `BuildingObs`, one per building you control, with named
fields — no magic indices:

```
indoor_temperature   comfort_low / comfort_high   outdoor_temperature
base_load_kwh        solar_generation_kwh         solar_irradiance
battery_soc_kwh      battery_capacity_kwh         battery_max_power_kw
hp_max_electric_kw   hp_cop                       last_net_kwh
passive_net_kwh   <- net energy if you leave everything idle. Start here.
```

`Action` has two numbers, both in `[-1, 1]`: `hvac` (negative cools, positive
heats) and `battery` (positive charges, negative discharges).

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

The shipped `submissions/example_team.py` scores about **0.94** using only a
price-aware battery and honest bidding. There is a lot left on the table:
pre-cooling, forecasting, strategic bid shading, and reasoning about when the
market will be glutted.

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
  backends/
    mock.py       self-contained building simulator (the default)
    citylearn_backend.py   the real Annex 96 neighbourhood (opt-in)
submissions/      drop your file here
config/           smoke.yaml (fast) and default.yaml (a full week)
examples/         explain_market.py
tests/            44 tests; run them to verify your checkout
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
