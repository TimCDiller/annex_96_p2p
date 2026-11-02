"""The advanced opponents in agents/advanced/: contracts, not performance claims."""
import sys
from pathlib import Path

import numpy as np
import pytest

from annex96p2p.market import build_price_grid
from annex96p2p.registry import Participant
from annex96p2p.runner import simulate
from annex96p2p.tariff import Tariff
from stub import StubBackend

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "advanced"))
from _advanced_common import ETA, BatteryDP, curve_to_bid      # noqa: E402
from _marginal_value import MarginalValueAgent                  # noqa: E402
from market_forecast_battery import MarketForecastBattery       # noqa: E402


class Market:
    def __init__(self, price):
        self.price_grid = build_price_grid(price)
        self.n_levels = len(self.price_grid)


def test_curve_to_bid_rebuilds_the_curve_the_market_adds_up():
    mkt = Market(0.30)
    p = mkt.price_grid
    demand = np.where(p < 0.10, 12.0, np.where(p < 0.20, 3.0, -5.0))
    bid = curve_to_bid(mkt, demand)
    # The market counts a buy at its price and every cheaper one, an ask at
    # its price and every dearer one.
    buys = np.cumsum(np.clip(bid, 0, None)[::-1])[::-1]
    sells = np.cumsum(np.clip(-bid, 0, None))
    assert np.allclose(buys - sells, demand)


def test_marginal_value_of_stored_energy_is_the_purchase_it_replaces():
    # Steady load, one buying price, nothing to sell: an extra stored kWh
    # delivers 0.95 kWh and saves that much at the buying price.
    dp = BatteryDP(capacity=4.0, power=1.0)
    hours = 6
    cost = dp.costs([2.0, 3.0], np.full(hours, 2.0), np.full(hours, 0.30),
                    np.zeros(hours), terminal=2.0)
    assert cost[0] - cost[1] == pytest.approx(0.30 * ETA, rel=1e-6)


@pytest.mark.parametrize("cls", [MarketForecastBattery, MarginalValueAgent])
def test_agents_run_two_days_without_failing(cls):
    backend = StubBackend(n_buildings=4, days=2)
    houses = backend.building_names
    participants = [Participant("advanced", cls("advanced", houses[:2]), houses[:2]),
                    Participant("other", cls("other", houses[2:]), houses[2:])]
    result = simulate(backend, participants, Tariff.synthetic(backend.n_timesteps),
                      progress=False)
    assert not result.ledger.check_balance()
    assert all(p.failures == 0 and p.timeouts == 0 for p in participants)
    logged = participants[0].agent.offer_log
    assert logged and all(np.isfinite(r["forecast_now"]) for r in logged)
