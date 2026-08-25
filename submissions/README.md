# Submissions

Put your agent here as a single `.py` file.

```bash
cp example_team.py team_yourname.py
python -m annex96p2p check submissions/team_yourname.py
python -m annex96p2p run --config ../config/default.yaml
```

**The rules, the full API, the debugging guide and the pre-submission
checklist all live in [the main README](../README.md).** They are deliberately
not repeated here, so there is one copy that cannot drift out of date. Read
these sections before you start:

- [Submission rules](../README.md#submission-rules) — what a submission may
  and may not do
- [How the market works](../README.md#how-the-market-works) — the price
  bounds, the bid→clear→dispatch order, why there is no imbalance penalty
- [The API in full](../README.md#the-api-in-full) — every field of
  `BuildingObs`, `MarketInfo`, `TradeResult` and `Action`
- [Debugging your agent](../README.md#debugging-your-agent) — reading
  `settlements.csv` when something looks wrong
- [The default agent, and how to beat it](../README.md#the-default-agent-and-how-to-beat-it)
  — its weaknesses are the assignment

The one thing worth repeating: anything nobody claims is run by the built-in
default agent, one participant per building. That is why you can develop
alone — your market is full of counterparties even when yours is the only
file in this folder.

Files starting with `_` are ignored, so `_scratch.py` is a safe place to
experiment.
