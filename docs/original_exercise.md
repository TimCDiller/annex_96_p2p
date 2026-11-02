# The original exercise: the unaltered 25-home neighbourhood

**This is not the course default.** The course runs on the altered Vermont
winter stock described in the [README](../README.md). This page keeps the
original setup -- the real Annex 96 Common Exercise 1 neighbourhood, unaltered,
scored against `SimpleRBCAgent` -- for comparison and for instructors.

Numbers on this page were measured before the shared `Thermostat` gained
smoothing (it now blends each command with the previous hour's; see its
docstring). Re-run to get current figures; the comparisons between agents
still illustrate the same points.

The bidding API, market rules and debugging guide in the README apply here
unchanged. What differs is the building stock, the baseline agent, the runner
(`python -m annex96p2p run` with `config/default.yaml`) and the scoring.
The example agents for this exercise live in
[`agents/basic/original_exercise/`](../agents/basic/original_exercise/), and
`config/default.yaml` points there.

## Quick start

```bash
git clone <this repo>
cd annex96-p2p
pip install -e ".[dev]"

python -m pytest                    # contract checks, no dataset needed
python examples/explain_market.py   # the market in real numbers, no dataset needed
```

Running an actual simulation needs the exercise dataset and PyTorch, because
the buildings carry LSTM thermal models:

```bash
git clone -c core.longpaths=true https://github.com/kkaspar10/annex96_common_exercise_1 ../annex96_common_exercise_1
pip install torch
python -m annex96p2p run --config config/default.yaml
```

The building stock is the real Annex 96 Common Exercise 1 neighbourhood. It
ships as two datasets of 25 ResStock homes each, and they are genuinely
different control problems:

| dataset | season | actuated device | timesteps |
|---|---|---|---:|
| `annex96_ce1_vt_neighborhood` | Vermont, January | `heating_device` | 743 |
| `annex96_ce1_tx_neighborhood` | Texas, June | `cooling_device` | 719 |

**Vermont is the default.** Each building actuates exactly one device, never
both, so half of the `hvac` range does nothing — see
[`BuildingObs.hp_draw_kwh()`](../src/annex96p2p/api.py), which exists because
bidding against the inert half is an easy and expensive mistake.

Building the environment takes about 30 s and each timestep about 0.15 s, so a
full run is a couple of minutes. Set `days:` in the config while you iterate.

Setup details and the things about this dataset that will surprise you are in
[docs/citylearn.md](citylearn.md).

Then:

```bash
cp agents/basic/original_exercise/example_team.py agents/basic/original_exercise/team_yourname.py
# edit BUILDINGS and the two methods
python -m annex96p2p check agents/basic/original_exercise/team_yourname.py
python -m annex96p2p run --config config/default.yaml
```

Every building you do **not** claim is run by a built-in default agent, so you
always have a full market of opponents to trade against, even alone on your
own laptop with only your own file present.

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
[`baselines.py`](../src/annex96p2p/baselines.py). It is crude on purpose. Read
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
- the thermostat is competent but blind to price — it reacts only to the
  temperature it already has. It uses the shared
  [`Thermostat`](../src/annex96p2p/api.py), so you are *not* being handed an
  easy win by out-controlling a bad one. See
  [pre-cooling](#pre-cooling-and-finding-out-for-yourself).
- battery runs on a wall clock — charge 10:00–15:00, discharge 18:00–21:00 —
  with no reference to price, PV output, or state of charge
- nothing reacts to the price it just cleared at

Four agents ship, in deliberately different styles. All four share the same
dispatch — target the middle of the comfort band, cover the load from the
battery, touch the grid only when the battery is flat — so the difference
between them is **bidding and nothing else**.

| file | how it prices | score | bought | sold |
|---|---|---:|---:|---:|
| `example_inverse_soc.py` | divider at `(1 - state_of_charge) x grid_price` | 1.005 | 144 | 59 |
| `example_team.py` | a rule stack keyed off price percentiles | 1.006 | 74 | 43 |
| `example_flat50.py` | assumes the market clears at 50% of next hour's grid price | 1.013 | 201 | 46 |
| `example_surplus.py` | four obvious rules: self-consume, charge on surplus, dump when full, pay 95% when empty | 1.025 | 110 | 2 |

Two weeks of the Vermont January, five buildings each, everyone else on the
default agent. `score` is your bill over what the default agent would have paid
for the same buildings, so 1.000 is "no better than doing nothing clever" and
lower is better.

**Read that table honestly: every one of them is slightly WORSE than the
default, and they are all within 2% of each other.** That is not four badly
written agents. It is what this scenario is: in a Vermont January the market
moves about 1% of the energy, so there is almost nothing for a bidding strategy
to win or lose, and what little the agents do trade costs them a bit more than
it saves. The volumes in the last two columns are the whole story -- tens of
kWh, against tens of thousands imported.

If you want bidding to matter, this is the dial to turn, and the reasons are
quantified in the next section.

### See it for yourself

```bash
pip install -e ".[viz]"
python examples/sample_run.py --days 7
```

writes a self-contained HTML booklet: how the market cleared, whether the
buildings stayed inside their comfort bands, and **one tab per agent showing
its whole bid book** -- price up the side, time along the bottom, colour for
quantity with blue buying and red selling, and a cross wherever the market
actually cleared. A bid only matters relative to those crosses, so that plot
tells you at a glance whether an agent is pricing itself into the market or out
of it.

It also computes why the market is thin, which is the first thing anyone asks.
On the Vermont January the peer-to-peer market handles about **1.2% of the
energy** -- 546 kWh traded against 44,633 kWh imported -- for three reasons the
booklet quantifies:

- **there is barely a supply side.** Only **2%** of participant-hours are net
  producing. PV generates in 126 of 336 hours and never much, because it is a
  Vermont winter.
- **storage is far too small to matter.** The compressor draws 19.5 kW; the
  battery holds 37.8 kWh but discharges at only 2.9 kW. Running storage flat out
  does not cover a building's own heating, let alone leave a surplus to sell.
- **battery-first dispatch makes every agent self-sufficient**, which removes
  the demand the sellers needed. Individually rational, collectively thinning.

None of that is a fault in the market mechanism -- everything offered cleared,
in every hour examined. It is a fact about the season. **If you want bidding to
be the thing that decides the exercise, run the Texas summer dataset instead**:
cooling in June puts real PV surplus into the middle of the day, which is when
a peer-to-peer market has something to do. Point `citylearn_schema` in
`config/default.yaml` at `annex96_ce1_tx_neighborhood`, and re-run
`python -m annex96p2p buildings` to get that dataset's building ids.

### Pre-cooling, and finding out for yourself

The building fabric is a thermal store nobody bills you for, and on these
homes it holds more energy than the battery does. Over-cool while energy is
cheap, then coast through the expensive hours with the compressor off.
`Thermostat` takes an `aim` per call, so trying it costs one line:

```python
hvac = self.thermostat(o, aim=-0.6 if cheap else 0.4)   # -1 bottom, +1 top
```

Whether it pays is a question about numbers, not principle. Against you: a
colder building leaks faster, every degree-hour outside the band is charged,
and moving off centre spends the margin that keeps you inside it. Measured on
the Vermont month, aiming low does save energy but costs comfort, and the two
very nearly cancel:

| `aim` | where the target sits | in band | °C·h | imported |
|---:|---|---:|---:|---:|
| `0.00` | band centre | 77.3% | 3351 | 34678 |
| `-0.50` | 1.0 K above the floor | 71.2% | 3628 | 31393 |
| `-0.85` | 0.3 K above the floor | 57.2% | 4027 | 29813 |

For you: the peak/off-peak ratio, the width of the expensive window, the
thermal mass. The shipped agents all aim at the centre, so that column is the
baseline you are trying to beat.

One hazard specific to this stock: the comfort band is the occupant setpoint
plus or minus `comfort_band`, and the ResStock schedules move it by **up to
7 K within a single building**. Cold banked against a band that then steps
down underneath you is wasted, and no controller can follow a 6 K step in an
hour. Sweep `aim` over a few values and measure it.

---

## Running it as a tournament

Running everyone together is the same code path. Drop every submitted file
into `submissions/` and:

```bash
python -m annex96p2p tournament --config config/default.yaml
```

`tournament` differs from `run` in two ways: overlapping building claims
become a hard error instead of a warning, and if `assignments:` is set in the
config, a submission claiming a building it was not assigned is rejected.

To pin the partition, uncomment the `assignments` block in
[`config/default.yaml`](../config/default.yaml).

### The stock is uniform — there is no clever partition

Every one of the 25 homes has a PV array sized to 70–100% of its own annual
demand **and** a battery holding 1.5–3.5 hours of its own daily peak. There
are no archetypes to separate, no structural sellers to pair with structural
buyers, and no partition that unlocks more trade than another.

That has a consequence worth knowing before you set the assignment: because
everyone is long solar in the same hours and everyone has somewhere to put it,
the market is thinner than the mechanism can support. The gains come from
pricing your own inventory well, not from finding a counterparty who is short.

Assign buildings however you like — by ResStock id, in blocks of five for
five teams. Pin it with the `assignments` block in
[`config/default.yaml`](../config/default.yaml) and `tournament` will enforce it.


---

## Four additional two-house agents

`agents/basic/original_exercise/daily_trading/` contains two naive agents (idle storage and
clock-based storage) and two arbitrage agents (fixed price thresholds and
thresholds learned from observed prices). Both arbitrage agents target 0.5 K
above the lower comfort limit. This is a target, not a comfort guarantee.
They retain unsold battery energy, and the adaptive agent can buy inexpensive
peer energy specifically for charging.

Run the controlled comparison from the repository root, using a Python
environment with the upstream CityLearn fork's dependencies installed:

```bash
python examples/daily_trading.py             # full Vermont January
python examples/daily_trading.py --days 7 --out results/daily_trading_week
```

The experiment transfers eight houses to the new agents; existing submissions
keep their remaining houses. The arbitrage pairs were selected for relatively
low heating demand during the first week, so this is a designed trading
scenario, not a random assignment or an out-of-sample strategy benchmark.
The script compares the original market, the same new ownership groups with
default controllers, and the new strategies. Original submission files are
unchanged. Each scenario saves assignments, settlements, daily volumes, comfort
statistics, and physically backed versus grid-backed sales under `results/`.
`summary.json` and `daily_comparison.csv` compare all three scenarios. The last
day has only 23 transitions in the 743-step dataset; startup is included.

The four new files are in a separate folder to prevent overlapping claims in
ordinary runs. Use this experiment script to combine them with the originals.
Agents observe only their own houses and public market information; the
experiment's diagnostic recorder does not pass additional data to them.

### Varying the grid tariff

`python examples/price_variation.py` runs the four-new-agent market three
times: original prices, increased spread with unchanged peak timing, and the
same increased-spread prices shifted between hours each day (seed 96).
All three profiles have the same monthly mean and preserve the delivery-price
floor. Buildings, ownership, agent settings, and comfort penalties stay fixed;
agents do not receive future prices. These are synthetic sensitivity scenarios,
not historical electricity-price data.

Outputs under `results/price_variation/` include a comparison table, a chart,
per-agent bills, hourly audits, and the exact price CSV for each run. Overnight
metrics cover 00:00 through 06:00. To inspect hourly trades and clearing prices:

```bash
python examples/hourly_trades.py --run results/price_variation/shifting_peaks
```
