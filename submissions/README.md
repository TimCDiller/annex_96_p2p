# Submissions — test your agent here

This folder is where you **test** your agent on your own machine. It is not
how you hand in: **hand-ins happen on Canvas**.

```bash
cp submissions/_template.py submissions/team_yourname.py      # or one of the _example_ files
# set NAME and BUILDINGS to your Canvas assignment, then edit the agent
python -m annex96p2p.altered_stock run --configurations balanced --modes competitive \
    --days 7 --submissions submissions --out results/team_yourname
```

Run from the repository root, after the one-time setup in
[Getting set up](../README.md#getting-set-up).

| file | what it is |
|---|---|
| `_template.py` | inherits the default agent unchanged — the smallest legal submission |
| `_example_half_battery.py` | a complete simple agent: battery held at 50%, surplus sold from 20% of the grid price, shortfall bought up to 80% |
| `_example_market_player.py` | the default agent's physical plan with strategic prices: buys for storage only at $0.02 or less, sells at peak only at 80% of the grid price or more ([what it does to the market](../README.md#one-player-moves-the-market-for-everyone)) |

Files starting with `_` are not loaded, so neither of these takes part in
your runs, and `_scratch.py` is a safe place to experiment. Keep only one
loaded copy of your own agent here: two files claiming the same buildings
stop the run.

Every house nobody claims is run by the default agent, one participant per
house, so your market is full of counterparties even when yours is the only
file in this folder.

**The rules, the full API, the debugging guide and the hand-in checklist live
in [the main README](../README.md)** — one copy, so it cannot drift:

- [Testing your agent](../README.md#testing-your-agent) and
  [Is it any good?](../README.md#is-it-any-good)
- [Handing in](../README.md#handing-in)
- [Submission rules](../README.md#submission-rules)
- [How the market works](../README.md#how-the-market-works)
- [The API in full](../README.md#the-api-in-full)
- [Debugging your agent](../README.md#debugging-your-agent)
- [Before you hand in](../README.md#before-you-hand-in)
