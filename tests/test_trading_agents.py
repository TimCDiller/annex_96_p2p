"""Bid/dispatch contracts, not performance claims about the stub physics."""
from dataclasses import replace

import numpy as np
import pytest

from annex96p2p.api import MarketInfo, TradeResult
from annex96p2p.market import build_price_grid
from annex96p2p.trading_agents import (NaiveSpotAgent, NaiveClockAgent,
                                     MinimumTariffAgent, MinimumAdaptiveAgent)
from stub import StubBackend


def market(price):
    return MarketInfo(0, 18, 0, price, 0.0, build_price_grid(price), None, 0.0)


def observation(**kwargs):
    o = StubBackend().observe()["S01"]
    return replace(o, **kwargs)


@pytest.mark.parametrize("cls", [NaiveSpotAgent, NaiveClockAgent,
                                 MinimumTariffAgent, MinimumAdaptiveAgent])
def test_empty_and_full_batteries_keep_actions_finite_and_bounded(cls):
    for soc in (0.0, 10.0):
        agent = cls("test", ["S01"])
        agent.reset()
        obs = {"S01": observation(battery_soc_kwh=soc)}
        mkt = market(0.30)
        bid = agent.bid(obs, mkt)
        assert np.isfinite(bid).all()
        assert not ((bid > 0).any() and (bid < 0).any())
        a = agent.dispatch(obs, mkt, TradeResult(None, 0.0))["S01"]
        assert -1 <= a.battery <= 1
        assert -1 <= a.hvac <= 1
        if soc == 0:
            assert a.battery >= 0


@pytest.mark.parametrize("cls", [MinimumTariffAgent, MinimumAdaptiveAgent])
def test_unsold_battery_export_is_retained(cls):
    agent = cls("test", ["S01"])
    agent.reset()
    obs = {"S01": observation(battery_soc_kwh=8, base_load_kwh=0,
                              solar_generation_kwh=0, indoor_temperature=23)}
    mkt = market(0.30)
    bid = agent.bid(obs, mkt)
    assert bid.sum() < 0
    assert agent.dispatch(obs, mkt, TradeResult(None, 0))["S01"].battery == pytest.approx(0)
    assert agent.dispatch(obs, mkt, TradeResult(0.20, -1))["S01"].battery == pytest.approx(-0.4)


def test_optional_charge_requires_a_cleared_purchase():
    agent = MinimumAdaptiveAgent("test", ["S01"])
    agent.reset()
    obs = {"S01": observation(battery_soc_kwh=2, base_load_kwh=0,
                              solar_generation_kwh=0, indoor_temperature=23)}
    mkt = market(0.18)
    assert agent.bid(obs, mkt).sum() > 0
    assert agent.dispatch(obs, mkt, TradeResult(None, 0))["S01"].battery == 0
    assert agent.dispatch(obs, mkt, TradeResult(0.12, 1))["S01"].battery == pytest.approx(0.4)
