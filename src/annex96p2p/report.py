"""Turning the ledger into something readable."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .runner import RunResult

RULE = "=" * 78
THIN = "-" * 78


def _money(x: float) -> str:
    return "$%8.2f" % x


def leaderboard_text(result: RunResult, show_defaults: bool = False,
                     top: Optional[int] = None) -> str:
    """The scoreboard, ranked on score against the default agent."""
    board = result.leaderboard
    if board.empty:
        return "(no results)"

    scored = "score" in board.columns

    if not show_defaults:
        submissions = board[~board.is_default]
        board = submissions if not submissions.empty else board
        board = board.copy()
        board["rank"] = np.arange(1, len(board) + 1)
    if top:
        board = board.head(top)

    if scored:
        header = ("  #  participant          score   total bill   vs default   "
                  "energy   comfort   bought    sold")
        lines = [RULE, "LEADERBOARD  (score = your bill / default agent bill; "
                       "lower is better)", RULE, header, THIN]
        for _, row in board.iterrows():
            saved = row["reference_cost"] - row["total_cost"]
            lines.append("%3d  %-18s %6.3f  %s  %s   %s %s %7.1f %7.1f" % (
                int(row["rank"]), str(row["participant"])[:18], row["score"],
                _money(row["total_cost"]), _money(saved),
                _money(row["energy_cost"]), _money(row["comfort_cost"]),
                row["bought_kwh"], row["sold_kwh"]))
        lines.append(THIN)
        lines.append("score below 1.000 means you beat the built-in default agent "
                     "on the same buildings.")
        lines.append("total bill = energy cost + comfort cost "
                     "($%.2f per degC-hour outside the band)."
                     % result.tariff.comfort_price)
        return "\n".join(lines)

    header = ("  # participant           total bill   energy   comfort  "
              "vs grid-only   bought    sold   degC-h")
    lines = [RULE, "LEADERBOARD  (lowest total bill wins)", RULE, header, THIN]
    for _, row in board.iterrows():
        lines.append("%3d %-18s %s %s %s   %+6.1f%%  %7.1f %7.1f %7.1f" % (
            int(row["rank"]), str(row["participant"])[:18],
            _money(row["total_cost"]), _money(row["energy_cost"]),
            _money(row["comfort_cost"]), row["market_gain_pct"],
            row["bought_kwh"], row["sold_kwh"], row["degree_hours"]))
    lines.append(THIN)
    lines.append("total bill = energy cost + comfort cost.  "
                 "'vs grid-only' is how much the market saved you.")
    lines.append("NOTE: raw bills are NOT comparable across different portfolio "
                 "sizes. Enable `reference: true` to score properly.")
    return "\n".join(lines)


def system_text(result: RunResult) -> str:
    s = result.summary
    if not s:
        return ""
    lines = [RULE, "SYSTEM", RULE,
             "  timesteps                %d over %d participants"
             % (s["timesteps"], s["participants"]),
             "  total bill               $%.2f  (grid-only reference $%.2f)"
             % (s["total_bill"], s["grid_only_bill"]),
             "  gain from trading        $%.2f" % s["system_market_gain"],
             "  peer-to-peer volume      %.1f kWh" % s["p2p_volume_kwh"],
             "  grid imports             %.1f kWh" % s["grid_import_kwh"],
             "  curtailed (unsold PV)    %.1f kWh" % s["curtailed_kwh"],
             "  comfort exceedance       %.1f degC-hours" % s["degree_hours"],
             "  market cleared in        %.0f%% of timesteps" % (100 * s["clearing_rate"]),
             "  mean clearing price      $%.3f/kWh" % s["mean_clearing_price"],
             "  wall clock               %.2f s" % result.wall_seconds]
    return "\n".join(lines)


def hourly_market_text(result: RunResult, day: int = 0) -> str:
    """One day of clearing prices and volumes, next to the grid price."""
    market = result.ledger.market_frame()
    if market.empty:
        return ""
    frame = market[market.day == day]
    if frame.empty:
        return ""
    lines = [RULE, "MARKET, day %d" % day, RULE,
             "  hour   grid $/kWh   clearing $/kWh   volume kWh"]
    for _, row in frame.iterrows():
        price = ("     --   " if pd.isna(row.clearing_price)
                 else "  $%.2f   " % row.clearing_price)
        lines.append("  %4d       $%.3f       %s   %8.2f"
                     % (row.hour, row.grid_import_price, price, row.volume_kwh))
    return "\n".join(lines)


def compute_text(result: RunResult) -> str:
    """Per-participant compute use, timeouts and disqualifications."""
    rows = [p for p in result.participants if not p.is_default]
    rows = rows or list(result.participants)
    if not rows:
        return ""
    lines = [RULE, "COMPUTE", RULE,
             "  participant           seconds   timeouts   raised   status"]
    for p in sorted(rows, key=lambda x: -x.seconds):
        status = ("DISQUALIFIED" if p.disqualified
                  else "over budget" if p.timeouts else "ok")
        lines.append("  %-20s %8.2f %10d %8d   %s"
                     % (p.name[:20], p.seconds, p.timeouts, p.failures, status))
    lines.append(THIN)
    lines.append("a timeout gets an empty bid for that timestep; exhausting the "
                 "per-run budget")
    lines.append("disqualifies the participant for the remainder of the run.")
    return "\n".join(lines)


def warnings_text(result: RunResult) -> str:
    warnings = result.ledger.warnings
    failures = [p for p in result.participants if p.failures or p.timeouts
                or p.disqualified]
    if not warnings and not failures and not result.slow_calls:
        return ""
    lines = [RULE, "WARNINGS", RULE]
    for participant in failures:
        bits = []
        if participant.failures:
            bits.append("%d raised (default agent substituted)" % participant.failures)
        if participant.timeouts:
            bits.append("%d timed out (empty bid substituted)" % participant.timeouts)
        if participant.disqualified:
            bits.append("DISQUALIFIED, over the per-run compute budget")
        lines.append("  %s: %s" % (participant.name, "; ".join(bits)))
    for message in warnings[:20]:
        lines.append("  " + message.replace("\n", "\n    "))
    if len(warnings) > 20:
        lines.append("  ... and %d more" % (len(warnings) - 20))
    for message in result.slow_calls[:5]:
        lines.append("  SLOW " + message)
    return "\n".join(lines)


def full_report(result: RunResult, show_defaults: bool = False) -> str:
    parts = [leaderboard_text(result, show_defaults=show_defaults),
             system_text(result),
             hourly_market_text(result, day=0),
             compute_text(result),
             warnings_text(result)]
    return "\n\n".join(p for p in parts if p)


def save(result: RunResult, out_dir: str) -> None:
    """Write the ledger, the market history and the leaderboard as CSV."""
    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=True)
    result.ledger.to_frame().to_csv(path / "settlements.csv", index=False)
    result.ledger.market_frame().to_csv(path / "market.csv", index=False)
    result.leaderboard.to_csv(path / "leaderboard.csv", index=False)
    (path / "report.txt").write_text(full_report(result, show_defaults=True),
                                     encoding="utf-8")
