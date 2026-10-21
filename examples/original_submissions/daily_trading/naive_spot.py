"""Two-house naive spot strategy; run examples/daily_trading.py."""
from annex96p2p.trading_agents import NaiveSpotAgent

NAME = "naive_spot"
BUILDINGS = ["resstock-amy2018-2021-release-1-467125",
             "resstock-amy2018-2021-release-1-481052"]
AGENT = NaiveSpotAgent
