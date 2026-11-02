# Advanced agents

Stronger opponents to test a submission against, beyond the default agent
that runs every unclaimed house. Each is an ordinary submission file — it sees
only its own houses and the public market information.

| file | written for | strategy |
|---|---|---|
| [`market_forecast_battery.py`](market_forecast_battery.py) | a fast storage-rich home (80 kWh / 80 kW) | **market forecast battery management** |
| [`marginal_value_storage.py`](marginal_value_storage.py) | a storage-rich home | marginal value of storage |
| [`marginal_value_generation.py`](marginal_value_generation.py) | a PV-rich home | marginal value of storage (the same agent) |
| [`team_opt_hp.py`](team_opt_hp.py) | a portfolio of homes | a team's MPC agent: one linear programme over batteries, trades and heat pumps |

The participant names are the strategy names, so in the hourly page each one
is listed under its strategy, e.g. *◆ market forecast battery management
199613*. The first three also draw their reasoning on their bid panel: a red
dashed line at the price below which they buy for storage, a green dashed
line at the price above which they sell from storage, and a dotted line at
their forecast of the hour's clearing price. Read every bid against those
lines.

Files starting with `_` hold shared code:

- [`_advanced_common.py`](_advanced_common.py) — what the first three share:
  the price forecast, the load profile, the 30-hour optimisation, and
  `CurveAgent`, which bids a demand curve (kWh wanted at every legal price)
  and dispatches whatever the market and the grid make of it;
- [`_marginal_value.py`](_marginal_value.py) — the marginal-value strategy.

## Playing against them

The ready-made way is a field (see [`../fields.py`](../fields.py)):

```bash
python -m annex96p2p.altered_stock run --configurations balanced --modes competitive \
    --days 7 --submissions submissions --out results/team_yourname \
    --field advanced       # complex agents on every house you do not claim
                           # or: --field diverse  (simple and complex mixed)
```

The study below — each agent alone, together, everywhere — is
[`../../examples/advanced_study.py`](../../examples/advanced_study.py):

```bash
python examples/advanced_study.py --list
python examples/advanced_study.py trio                     # the three together, full month
python examples/advanced_study.py trio --submission submissions/team_yourname.py
python examples/advanced_study.py --report trio            # bills against an all-default run
```

Results go to `results/advanced/<scenario>/balanced/competitive/`, with
`hourly_trades.html` already written. `--submission` adds your file; the
advanced agents step off the houses it claims.

## The strategies

### Market forecast battery management (fast storage home)

1. **Limits.** Keep at least 1.5 hours of the house's own load (base load
   plus heat pump) in the battery, and at least 1.5 hours of its own PV as
   free room at the top. Below the reserve it refills at any price.
2. **Forecast.** The clearing price for this hour and the hours ahead.
   For each hour of the day it learns the share of the grid price that hour
   cleared at, as an exponentially weighted average over past days. It
   multiplies that by the grid price ahead: the published daily shape times
   today's factor. The same forecaster also tracks how often each hour clears
   at all.
3. **Bid a step.** At any price below the forecast for the next
   `FORECAST_HOURS` hours (their average), it buys the house's load plus as
   much as the battery takes. At any price above it, it serves the load from
   the battery and sells as much as the battery gives. The grid counts as one
   more seller: if the grid price itself is below the forecast, the agent
   charges from the grid.

`FORECAST_HOURS` (1 by default) and `SPREAD` (a dead band around the
forecast; 0 by default, as stated) are parameters. The forecast for the
current hour does not change the bid. A bid is a whole curve, so it already
covers every price the hour might clear at. The agent logs that forecast so
it can be checked: after the first week it is off by 2.7¢ on average in the
hours that clear, with no bias. About a third of hours do not clear at all.

### Marginal value of storage (slow storage home, PV-rich home)

1. **Assume** the market clears as it did in the past: in each hour of the
   day at the same share of the grid price, and as often. A kWh bought then
   costs the clearing price, or the grid price if the hour does not clear. A
   kWh offered earns the clearing price, or nothing.
2. **Optimise** the next 30 hours from the current state of charge: the
   least cost of the house's forecast load, PV and battery, ending the
   horizon half full.
3. **Perturb** the starting charge by ±1% of capacity. The change in total
   cost per kWh is the marginal value of stored energy.
4. **Bid.** Buy below that value × 0.95 (a kWh bought stores 0.95). Sell
   above it ÷ 0.95 (a kWh sold takes 1.05 from the battery). The round-trip
   loss becomes a dead band. Because charging lowers the value of the next
   kWh, the cost is also read at three points up and three down within one
   hour of battery power. Each slice is bid at its own marginal value, which
   makes a stepped curve. The house's own load is bid at up to the grid
   price, and its own surplus from $0.00.

The optimisation is a dynamic programme over 81 charge levels. One backward
pass gives the least cost from every starting charge, so the "same run from a
marginally lower and higher charge" is read off one solve. It takes about
1 ms. A linear programme gives the same gradient (checked: $0.217/kWh both
ways on a test problem). This agent was first written as a linear programme
with SciPy's HiGHS solver, which crashed the run on Windows: the runner then
called every agent on a fresh thread each hour. The runner now keeps one
thread per participant and HiGHS is fine (see Troubleshooting in the main
README), but the dynamic programme was kept: it needs only numpy.

## Results

Full month, `balanced`, mode `competitive`. Every number is compared with
all 20 houses on the default agent ($9,846 in total). Bills include comfort
(16.8 $/month on the fast house in every run: the agents keep the default
thermostat, so comfort never differs).

### 1. Each agent alone among default opponents

| scenario | agent's bill | same house, default | change | whole market |
|---|---:|---:|---:|---:|
| forecast, 1 hour ahead | $257.74 | $420.33 | **−38.7%** | −$46 |
| forecast, 3 hours ahead | $236.02 | $420.33 | **−43.8%** | −$107 |
| forecast, 6 hours ahead | $514.62 | $420.33 | **+22.4%** | +$159 |
| forecast, 1 hour, ±10% dead band | $271.45 | $420.33 | −35.4% | −$5 |
| marginal value, slow storage home | $637.43 | $839.12 | **−24.0%** | −$165 |
| marginal value, PV-rich home | $524.21 | $571.00 | **−8.2%** | −$2 |
| marginal value, *on the fast home* | $54.25 | $420.33 | **−87.1%** | −$207 |

**Yes, both strategies beat the default agent comfortably**, on every house
type. The last row puts the marginal-value agent on the forecast agent's own
fast house, for a like-for-like comparison. It does twice as well.

**Where the money comes from (fast house).** Every winning agent does the
same thing physically. It imports far more from the grid off-peak than the
default does, and resells that energy to peers in the peaks below the grid
price. The default's fast house sells 708 kWh to peers a month. The advanced
agents sell 3,800–4,300 kWh.

| fast house, month | default | forecast 1 h | forecast 3 h | forecast 6 h | marginal value |
|---|---:|---:|---:|---:|---:|
| grid import, kWh | 3,445 | 6,642 | 6,602 | 6,791 | 7,032 |
| average grid price paid | 11.9¢ | 13.9¢ | 12.8¢ | 14.4¢ | 12.1¢ |
| sold to peers, kWh | 708 | 3,849 | 3,853 | 4,032 | 4,267 |
| average price received | 21.1¢ | 23.2¢ | 21.1¢ | 17.2¢ | 23.4¢ |
| of which sold in peak hours | 708 | 2,634 | 2,750 | 1,296 | 3,072 |

The default agent is a weak opponent here for a structural reason. It
refills only modestly overnight and paces its sales across the peak. Its
fast battery sits mostly idle, waiting for midday PV.

**The forecast horizon decides when the battery fills.** The battery is
fast, so the step bid moves 80 kWh in whichever single hour first falls below
the reference. The horizon chooses that hour:

- **1 hour:** fills at 05:00 from the grid (2.4 MWh over the month), because
  05:00 is the first hour cheaper than the 06:00 forecast. That is not the
  cheapest hour of the night.
- **3 hours:** fills at 03:00, the cheapest hour (grid ~11¢), because the
  average of 04:00–06:00 is already above it. Its grid energy costs $79 less
  a month. It also sells earlier and cheaper (21.1¢ instead of 23.2¢), which
  gives back most of that: net, $22 better.
- **6 hours:** fills at 01:00–02:00, and then **sells 1.8 MWh at 05:00, before
  the morning peak**. At 05:00 the average of the next six hours includes the
  cheap 10:00–11:00 lull, so the reference falls below the current price and
  the step says sell. It sells into the dearest hours less than half as much
  as the shorter horizons, and receives 17¢ instead of 23¢. Averaging over
  the peak destroys the signal the strategy runs on.

The ±10% dead band, meant to cover the round-trip loss, made it slightly
worse. Thresholds on a forecast with 2.7¢ of error do not gain from a 2¢
band, and the band sometimes keeps it out of the market for an hour.

**Why the marginal-value agent does better on the same battery.** It prices
the whole 30 hours ahead, including the loss. It charges in the cheapest
hours (03:00 and 13:00–14:00), sells at 06:00–07:00, 17:00 and 22:00, and
sizes each step. The forecast agent compares each hour with only the next one or few.

**What each does to the market.**

- **Forecast agent, 1 or 3 hours ahead.** Barely moves prices: the peak
  clearing share stays at 67–68% of the grid price. It takes peak sales from
  the other storage owners. With 3 hours, the slow storage homes lose $86 and
  the other fast homes $83. The whole market saves a little, because energy
  bought from the grid off-peak replaces some peak-hour imports.
- **Marginal value, slow storage home.** Also takes sales from the other
  storage owners: the fast homes lose $122. It lowers the peak share (67.5% →
  65%), so the PV homes gain $142.
- **Marginal value, PV-rich home.** A single PV house moved **$486 from the
  storage homes to the PV homes**, while its own bill fell only $47. It
  offers its last 1–2 kWh of stored energy at its marginal value (7–10¢)
  rather than at 1¢. In the thin late-morning hours, that makes it the
  marginal seller. Uniform pricing takes the middle of the range that trades
  the most, so the 10:00–11:00 clearing price rises from 55–57% to 75–78% of
  the grid price, for every seller. The same effect as the market player in
  the [scenario gallery](../docs/scenarios/03_market_power.md), reached by
  pricing honestly rather than by holding out.

### 2. The three together

| | alone | in the trio (1 h) | in the trio (3 h) |
|---|---:|---:|---:|
| market forecast battery management | −38.7% (1 h) / −43.8% (3 h) | **−45.8%** | −33.3% |
| marginal value storage | −24.0% | −21.6% | −20.0% |
| marginal value generation | −8.2% | −4.9% | −7.0% |
| PV-rich homes | | −$268 | −$405 |
| fast storage homes (incl. the forecast agent) | | +$84 | +$125 |
| slow storage homes (incl. marginal value storage) | | −$37 | −$1 |
| whole market | | −$221 (−2.2%) | −$280 (−2.8%) |

**They hardly hurt each other.** Each keeps most of what it earned alone,
and the default storage owners are still the ones who lose business. The
forecast agent on a 1-hour horizon does better in the trio than alone: it
imports $83 less from the grid and pays $34 more in the market, so more of
its energy comes from peers. The exception is the 3-hour forecast agent,
which loses a quarter of its gain (−44% → −33%). It pays $37 more for what it
buys in the market and receives $33 less for what it sells.

### 3. Every house strategic

Forecast agents on all five fast homes, marginal-value agents on the five
slow homes and the ten PV homes.

| | 1 h forecast | 3 h forecast |
|---|---:|---:|
| PV-rich homes | +$258 (+4.5%) | **−$530 (−9.3%)** |
| fast storage homes (forecast agents) | +$75 (+3.0%) | +$21 (+0.9%) |
| slow storage homes (marginal value) | **−$884 (−54%)** | −$436 (−27%) |
| whole market | −$551 (−5.6%) | **−$944 (−9.6%)** |
| clearing price at peak, % of grid | 62% | 51% |
| traded between peers | 37,200 kWh | 36,700 kWh |
| curtailed | 4,500 kWh | 3,900 kWh |

- **The forecast strategy stops working once everyone uses it.** Five 80 kW
  batteries all bid the same step against the same forecast, fill in the same
  hour and sell in the same hours. Each ends at about the default's bill
  (−2% to +12%). The arbitrage they found alone is competed away.
- **The marginal-value storage agents are the winners.** Three of the five
  slow homes end the month with a bill near zero or below it (house 216895:
  −$110). Their slices are priced at their own marginal value, while the fast
  homes all act on the same threshold at once, so the slow homes trade around
  them.
- **The market as a whole gets much cheaper,** by 6–10%, more than any
  scenario in the gallery. The fast batteries import 9 MWh more from the grid
  off-peak (29,100 vs 19,900 kWh) and resell it. The PV homes' grid imports
  fall from 27,400 to 20,500 kWh. With 3-hour forecasts the peak price falls
  to half the grid price, and the PV homes, who buy at the peaks, keep most
  of that saving. With 1-hour forecasts the fast homes sell less at the peak,
  and the PV homes end up slightly worse off.
- **The cost is curtailment.** 800–1,400 kWh more PV is shed, almost all of
  it on the PV homes. Most likely this is because storage that has already
  filled from the grid has less room for the midday surplus. That is not
  measured directly.

### 4. A mixed market, with a team's MPC agent

`team_opt_hp` ([`team_opt_hp.py`](team_opt_hp.py), run unchanged) is a
team's submission: a 30-hour linear programme over its whole portfolio that
plans batteries, trades and heat pumps (through the published RC models). It
also coasts in the peaks and asks 80% of the grid price for what it sells
there. In scenario `mixed` it runs one portfolio of five houses. The other
houses are spread evenly over the strategies, by house type. The forecast
agent sits on storage homes only and uses its best horizon (3 h).
The `diverse` field in [`../fields.py`](../fields.py) is this market with
the simple agents half_battery and market_player on two of its default houses.

| strategy | PV-rich | fast storage | slow storage | bill | same houses, default | change |
|---|---|---|---|---:|---:|---:|
| team_opt_hp (one portfolio) | 112208 179247 425540 | 199613 | 147002 | $2,467 | $2,679 | −7.9% |
| marginal value | 20199 223581 247942 | 4421 | 411001 | $1,855 | $2,283 | **−18.7%** |
| forecast, 3 h | — | 245723 408344 | 216895 319918 | $1,441 | $1,682 | −14.3% |
| default | 376570 409896 460412 481052 | 485614 | 467125 | $3,394 | $3,203 | **+6.0%** |
| whole market | | | | $9,156 | $9,846 | −7.0% |

Control `opt_hp_alone`: the same five houses run by `team_opt_hp`, every
other house on the default agent.

| team_opt_hp, five houses | default | alone among defaults | in the mixed market |
|---|---:|---:|---:|
| bill | $2,679 | $1,924 (**−28.2%**) | $2,467 (−7.9%) |
| earned selling to peers | $535 | $974 | $309 |
| peak kWh sold / offered | | 2,778 / 7,196 | 734 / 7,755 |
| peak hours its 80% ask cleared | | 82 of 150 | 27 of 158 |
| comfort penalty | $81 | $340 | $364 |

- **team_opt_hp is strong against the default agent and weak against
  strategic sellers.** Its edge comes from selling stored energy at the peak
  for 80% of the grid price. Against default storage owners the peak clears
  at about 71% of the grid price, and its ask often holds. In the mixed
  market the forecast and marginal-value agents sell into the same peaks for
  less, the peak clears at 61%, and its ask is undercut in five hours out of
  six. It has no learning on the ask: it keeps offering 7.8 MWh at a price
  that no longer clears.
- **Its heating costs it money in both markets.** It runs at 4–4.5 times the
  default's degree-hours (680–730 °C·h vs 161). The extra degree-hours are
  spread over the whole day, not concentrated in the peaks where it coasts.
  So they come from the plan steering the heat pump, not only from the
  coasting rule. That costs about $260–280 a month. Without that penalty it
  would be −18% in the mixed market rather than −8%.
- **The marginal-value agent is again the most robust.** It wins big on
  storage (411001 −72%, 4421 −55%), but its PV homes are mixed: 20199 −8%,
  the two smaller ones +7%. The mix pushes the midday clearing price down to
  44% of the grid price (59% all-default), and the PV homes sell at midday.
- **The forecast agent wins on all four storage homes** (−6% to −32%),
  including the slow ones it was not designed for.
- **The default agent pays for everyone else.** Its two storage homes lose
  their peak sales: 485614 doubles its bill ($234 → $464), 467125 is +19%.
  Its PV homes break even or gain a little from cheaper peaks. Whole market:
  7% cheaper, curtailment up 1,200 kWh, as in the all-strategic runs.

## In short

- **Against the default agent, both strategies work.** The marginal-value
  agent is the stronger and the more robust one: −87% on the fast home, −24%
  on the slow one, −8% on a PV home. The forecast agent wins −39 to −44% with
  a 1–3 hour horizon but **loses 22% at 6 hours**. Its result rests on one
  parameter that decides which single hour the battery fills in.
- **Against each other, they mostly coexist,** because both mainly take
  business from the passive default storage owners. A student should expect
  to beat the default agent more easily than to beat these.
- **A whole market of them is far more efficient** (−6 to −10%). But a
  strategy that works when you alone use it — the forecast step — stops
  working when everyone does.
- **Pricing stored energy at its true value moves the clearing price.** One
  PV house doing so lifted the late-morning price by 20 points of the grid
  price for everyone.

One month, one configuration, one run each. The gains are large compared
with run-to-run noise, which is zero here (everything is deterministic), but
not with what a different month or equipment mix might do.
