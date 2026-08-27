# Running this as a course

Notes for the instructor. None of this is enforced by the code — it is a
suggested shape for the assignment, and the knobs that matter.

## A four-lab ladder

Each lab reuses the same submission file, so students build one agent up
rather than starting over.

**Lab 0 — one building, no market.** Assign one building each and let them
ignore `bid()` entirely (returning `mkt.empty_bid()` is legal). The task is dispatch only: minimise the bill for
one building under a time-varying price. They discover pre-cooling and
battery arbitrage here, before any market exists to distract them.

**Lab 1 — the market appears.** Same buildings, but now `bid()` matters.
Start them on a single price level, as the default agent does, and let them
find out that it fills at a terrible price. The lesson is the price grid, and
that a bid is a reservation curve, not a quantity.

**Lab 2 — portfolios and internal netting.** Give each student four or five
buildings. The new content is netting across your own buildings before
bidding, and self-consumption versus selling: a kWh out of your own battery
that you use saves the full grid price, while the same kWh sold earns only the
clearing price, which is capped below it.


**Lab 3 — the tournament.** Everyone submits, you run
`python -m annex96p2p tournament`, and the leaderboard goes on the projector.
Then the discussion: who shaded their bids, who got squeezed, what the
clearing price series looked like, and why the total system bill is not
minimised even though every individual minimised their own.

## What to grade on

The `score` column is the defensible number: your bill divided by the default
agent's bill on the same buildings. It is opponent-invariant, so a student
gets the same score on their laptop as in your tournament, and it normalises
away portfolio size.

Do **not** grade on tournament rank alone. Rank depends on what everybody else
submitted, which a student cannot control or predict, and it rewards being in
a weak field.

A defensible split:

| weight | on |
|---|---|
| 50% | `score` against the default agent |
| 30% | a short written analysis (see below) |
| 20% | tournament rank, or participation in it |

The written part is where the actual learning shows up. Good prompts:

- Your bid and your dispatch have to agree. Show a timestep where yours did
  not, and what it cost you.
- Plot the clearing price against the grid price. When does the market clear
  well below the grid price, and who captures that surplus?
- The system total bill is in the report. Compare it to what a single
  controller owning all twelve buildings could have achieved. Where did the
  difference go?

That last one is the good question. It is the price of anarchy, and it is
worth building a centralised perfect-foresight benchmark for if you want a
final project.

## Knobs

| setting | effect |
|---|---|
| `comfort_price` | at `0.0` the optimal strategy is to switch the heat pump off. Below about `0.18` ignoring comfort is rational and the term is decorative. `0.50` is the shipped value. |
| `export_price` | at `0.0` (the Common Exercise 1 rule) unsold surplus is curtailed, which is what gives the market its reason to exist. Raising it weakens the market sharply. |
| `days` | blank runs the full 720 timesteps, about two minutes. Set 2-3 while iterating. |
| `price_volatility` | raises the day-to-day spread. More volatility makes storage more valuable. |
| `price_csv` | swap in real ISO day-ahead prices for the month you are modelling. |

## Anti-gaming

Most of it is handled by construction:

- agents receive only their own buildings' observations, and only the previous
  timestep's clearing price and volume;
- `dispatch()` return values for buildings a participant does not own are
  discarded;
- bids are validated, clipped and repaired before clearing;
- exceptions are caught and the default agent substituted, so crashing is
  never advantageous;
- runs are deterministic, so a result can always be reproduced.

Two things are *not* prevented, deliberately:

- **Collusion.** Two students can agree to bid in a way that benefits them
  both. Let it happen and then discuss why it is illegal in real markets.
- **Strategic bid shading.** Not gaming at all — it is the point.

The one thing to watch is a submission that imports another submission, or
reads the ledger. Nothing stops it technically; say it is out of bounds and
skim the files.

## Scaling to a real cohort

There are 25 buildings. Five teams of five, or twelve students with two each
and one spare. Pin it with the `assignments` block in `config/default.yaml`
and run `tournament`, which turns an out-of-assignment claim into an error.

The stock is uniform -- every home has both a PV array sized to most of its own
demand and a battery holding a few hours of its own peak -- so no partition
unlocks more trade than another. Divide it however is administratively
convenient. See [citylearn.md](citylearn.md) for what that uniformity means
for the market.
