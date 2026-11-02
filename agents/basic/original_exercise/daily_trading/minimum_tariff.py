"""Two-house minimum tariff strategy; run examples/daily_trading.py."""
from annex96p2p.trading_agents import MinimumTariffAgent

NAME = "minimum_tariff"
BUILDINGS = ["resstock-amy2018-2021-release-1-216895",
             "resstock-amy2018-2021-release-1-179247"]
AGENT = MinimumTariffAgent
