"""Two-house naive clock strategy; run examples/daily_trading.py."""
from annex96p2p.trading_agents import NaiveClockAgent

NAME = "naive_clock"
BUILDINGS = ["resstock-amy2018-2021-release-1-485614",
             "resstock-amy2018-2021-release-1-498771"]
AGENT = NaiveClockAgent
