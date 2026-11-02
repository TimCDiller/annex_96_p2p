# Running this as a course

Notes for the instructor. None of this is enforced by the code — it is a
suggested shape for the assignment, and the knobs that matter.

The course market is the altered Vermont winter stock: 20 homes, `balanced`
configuration, mode `competitive` (see the [README](../README.md#the-course-market)).
Students test in `submissions/` on their own machines and hand in on Canvas;
post each team's building assignment on Canvas too.

## A four-lab ladder

Each lab reuses the same submission file, so students build one agent up
rather than starting over.

**Lab 0 — one building, no market.** Assign one building each and let them
ignore `bid()` entirely (returning `mkt.empty_bid()` is legal). The task is
dispatch only: minimise the bill for one building under the duck tariff. They
discover preheating and battery arbitrage here, before any market exists to
distract them.

**Lab 1 — the market appears.** Same buildings, but now `bid()` matters.
`agents/basic/half_battery.py` is a reasonable starting point: one
price level each way, at fixed fractions of the grid price. The lesson is the
price grid, and that a bid is a reservation curve, not a quantity.

**Lab 2 — portfolios and internal netting.** Give each team a mix of PV-rich
and storage-rich homes. The new content is netting across your own buildings
before bidding, and self-consumption versus selling: a kWh out of your own
battery that you use saves the full grid price, while the same kWh sold earns
only the clearing price, which is capped below it.

**Lab 3 — the class market.** Download the Canvas files into `submissions/`
and run everyone together (README, [Running everyone together](../README.md#running-everyone-together)).
Then the discussion: who shaded their bids, who got squeezed, what the
clearing price series looked like, and why the total system bill is not
minimised even though every individual minimised their own. The
`pv_withhold` / `storage_withhold` / `storage_flood` modes make a good
follow-up on market power.

## What to grade on

The defensible number is a matched comparison: a team's bill on its own
houses, against the bill of the default agent on the same houses in a run
without submissions (README, [Is it any good?](../README.md#is-it-any-good)).
Students can compute it on their own laptops.

It is **not** opponent-invariant in this stock. The houses trade heavily with
each other, so a team's bill moves with what the other participants do, and
the class run will not reproduce a student's laptop number exactly. Give teams
comparable portfolios (the same count of PV-rich and storage-rich homes, and
of 1C and 0.25C batteries), or rank within portfolio type.

Do **not** grade on rank in the class run alone. Rank depends on what
everybody else submitted, which a student cannot control or predict.

A defensible split:

| weight | on |
|---|---|
| 40% | bill against the default agent on the same houses, comfort included |
| 40% | a short written analysis (see below) |
| 20% | the class market, or participation in it |

The written part is where the actual learning shows up. Good prompts:

- Your bid and your dispatch have to agree. Show a timestep where yours did
  not, and what it cost you.
- Plot the clearing price against the grid price. When does the market clear
  well below the grid price, and who captures that surplus?
- How much of what you sold was physically backed (`hourly_audit.csv`)? Where
  did the rest come from?
- The system total bill is in the report. Compare it to what a single
  controller owning all twenty houses could have achieved. Where did the
  difference go?

That last one is the good question. It is the price of anarchy, and it is
worth building a centralised perfect-foresight benchmark for if you want a
final project.

## Knobs

The course market's parameters live in
[scenarios/altered_stock.yaml](../scenarios/altered_stock.yaml). Change the
recipe and run `python -m annex96p2p.altered_stock build` again; never edit
generated files, because the run checks their hashes. Students must rebuild
too, so settle the recipe before the course starts.

| setting | effect |
|---|---|
| `comfort_price` | $ per °C·hour outside the band; 0.50 ships. At 0.0 the optimal strategy is to switch the heat pump off; much above 1.0 comfort dominates the bill and the market becomes a side issue. |
| `duck_prices`, `daily_price_variation` | the 24-hour tariff and its day-to-day spread. A wider peak/valley gap makes storage and trading more valuable. |
| `configurations` | PV and battery sizes for each home role. |
| `--days` (run flag) | 7 while iterating; omit for the full month. |

## Anti-gaming

Most of it is handled by construction:

- agents receive only their own buildings' observations, and only the previous
  timestep's clearing price and volume;
- `dispatch()` return values for buildings a participant does not own are
  discarded;
- bids are validated, clipped and repaired before clearing;
- overlapping or unknown building claims stop the run;
- runs are deterministic, so a result can always be reproduced.

In the course runner an exception or a timeout stops the whole run rather
than being papered over, so a broken submission is found before grading, not
hidden by a substitute agent.

Two things are *not* prevented, deliberately:

- **Collusion.** Two students can agree to bid in a way that benefits them
  both. Let it happen and then discuss why it is illegal in real markets.
- **Strategic bid shading.** Not gaming at all — it is the point.

The one thing to watch is a submission that imports another submission, reads
the generated data files, or reads the ledger. Nothing stops it technically;
say it is out of bounds, run `scripts/scan_submissions.py`, and skim the files.

## Scaling to a real cohort

There are 20 houses: 10 PV-rich, 10 storage-rich, and within the storage-rich
homes five 1C and five 0.25C batteries. Five teams of four (two PV-rich, two
storage-rich each, one 1C and one 0.25C battery) give everyone the same
portfolio. Ten pairs of one PV-rich and one storage-rich home also works, if
you rank the 1C pairs and the 0.25C pairs separately. `generated/altered_stock/balanced/buildings.csv` lists
role and C-rate per house.

The original 25-home exercise, with its `tournament` command and assignment
table, is described in [original_exercise.md](original_exercise.md).
