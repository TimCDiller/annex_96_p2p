"""Peer-to-peer electricity trading exercise.

Adapted from IEA EBC Annex 96 Common Exercise 1. The original exercise asks
a single controller to keep a whole portfolio tracking a flat reference load.
This version changes two things:

  1. electricity has a time-varying price, and the objective is to minimise
     the sum of everyone's bills rather than to flatten a load profile;
  2. each participant controls only a subset of the buildings, and they
     trade energy with each other in a peer-to-peer market.

Student-facing API::

    from annex96p2p import Agent, Action

    class MyAgent(Agent):
        def bid(self, obs, mkt): ...
        def dispatch(self, obs, mkt, result): ...

    BUILDINGS = ["B01", "B02"]
    AGENT = MyAgent
"""
from .api import Action, Agent, Bid, BuildingObs, MarketInfo, TradeResult
from .baselines import DoNothingAgent, PriceTakerAgent, SimpleRBCAgent
from .config import Config
from .ledger import Ledger, Settlement
from .market import Clearing, build_price_grid, clear, describe
from .registry import Participant, build_participants
from .runner import RunResult, simulate
from .tariff import Tariff

__version__ = "0.1.0"

__all__ = [
    "Action", "Agent", "Bid", "BuildingObs", "MarketInfo", "TradeResult",
    "SimpleRBCAgent", "PriceTakerAgent", "DoNothingAgent",
    "Config", "Ledger", "Settlement",
    "Clearing", "clear", "build_price_grid", "describe",
    "Participant", "build_participants",
    "RunResult", "simulate", "Tariff",
]
