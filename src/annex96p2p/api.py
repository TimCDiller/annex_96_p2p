"""Everything a student submission needs.

    from annex96p2p import Agent, Action, Bid

The three objects you interact with:

  BuildingObs   what you know about one of your buildings right now
  MarketInfo    grid price, the legal price grid for bids, last step's clearing
  TradeResult   what YOUR bid actually cleared at this timestep

Sign conventions, used everywhere in this package:

  energy      positive = consuming from the system, negative = producing
  bid vector  positive = BUY that many kWh at that price level
              negative = SELL that many kWh at that price level
  quantity    positive = you bought from peers, negative = you sold to peers
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np


# --------------------------------------------------------------------------
# What you observe
# --------------------------------------------------------------------------
@dataclass
class BuildingObs:
    """State of one building at the start of the current timestep.

    You receive one of these per building you control, and nothing at all
    about buildings you do not control.
    """

    name: str
    timestep: int
    hour: int                       # 0..23
    day: int                        # 0-based day index of the simulation

    outdoor_temperature: float      # degC
    solar_irradiance: float         # 0..1, fraction of peak

    indoor_temperature: float       # degC
    comfort_low: float              # degC, lower edge of the comfort band
    comfort_high: float             # degC, upper edge of the comfort band

    base_load_kwh: float            # non-shiftable load THIS timestep
    solar_generation_kwh: float     # PV output THIS timestep (uncontrollable)

    battery_soc_kwh: float
    battery_capacity_kwh: float
    battery_max_power_kw: float

    hp_max_electric_kw: float       # heat pump electrical draw at full output
    hp_cop: float

    last_net_kwh: float             # your net energy in the PREVIOUS timestep

    @property
    def has_battery(self) -> bool:
        return self.battery_capacity_kwh > 0.0

    @property
    def has_pv(self) -> bool:
        return self.solar_generation_kwh > 0.0

    @property
    def passive_net_kwh(self) -> float:
        """Net energy this timestep if heat pump and battery stay idle.

        Positive means you will need to buy, negative means you have a
        surplus. This is the single most useful number for building a bid.
        """
        return self.base_load_kwh - self.solar_generation_kwh

    @property
    def comfort_mid(self) -> float:
        return 0.5 * (self.comfort_low + self.comfort_high)

    @property
    def is_uncomfortable(self) -> bool:
        return not (self.comfort_low <= self.indoor_temperature <= self.comfort_high)


# --------------------------------------------------------------------------
# What you control
# --------------------------------------------------------------------------
@dataclass
class Action:
    """Your control decision for one building for one timestep.

    hvac      -1.0 = heat pump at full COOLING
               0.0 = heat pump off
              +1.0 = heat pump at full HEATING
              values in between modulate the output linearly

    battery   -1.0 = discharge at full power
               0.0 = idle
              +1.0 = charge at full power

    Both are clipped to [-1, 1]. The battery is additionally limited by its
    state of charge and remaining capacity. A building with no battery
    ignores the battery command.
    """

    hvac: float = 0.0
    battery: float = 0.0


# --------------------------------------------------------------------------
# What you know about the market
# --------------------------------------------------------------------------
@dataclass
class MarketInfo:
    """Public market information. Every participant sees exactly this.

    Note what is NOT here: you never see another participant's bid, their
    load, or who you traded with. The only signal about the rest of the
    market is the previous timestep's clearing price and volume.
    """

    timestep: int
    hour: int
    day: int

    grid_import_price: float        # $/kWh you pay the utility this timestep
    grid_export_price: float        # $/kWh the utility pays you (0.0 = export banned)
    price_grid: np.ndarray          # legal bid prices, 0.00 .. grid_import_price

    last_clearing_price: Optional[float]   # None if the market did not clear
    last_clearing_volume: float            # kWh traded in the previous timestep

    @property
    def n_levels(self) -> int:
        return len(self.price_grid)

    def empty_bid(self) -> np.ndarray:
        """A zero bid vector of the right length for this timestep."""
        return np.zeros(self.n_levels, dtype=float)

    def level(self, price: float) -> int:
        """Index of the price level nearest to `price`, clipped to the grid."""
        idx = int(np.argmin(np.abs(self.price_grid - float(price))))
        return int(np.clip(idx, 0, self.n_levels - 1))

    def new_bid(self) -> "Bid":
        return Bid(self)


class Bid:
    """Convenience builder for a bid vector.

    A bid is one signed number per price level. You may build the numpy
    array yourself, or use this helper::

        bid = mkt.new_bid()
        bid.buy(price=0.18, quantity=4.0)    # buy up to 4 kWh at <= $0.18/kWh
        bid.sell(price=0.06, quantity=2.0)   # sell up to 2 kWh at >= $0.06/kWh
        return bid.array

    Prices snap to the nearest legal level. Because each level holds one
    signed number, buying and selling at the same level cancels out.
    """

    def __init__(self, mkt: MarketInfo):
        self._mkt = mkt
        self.array = mkt.empty_bid()

    def buy(self, price: float, quantity: float) -> "Bid":
        if quantity > 0:
            self.array[self._mkt.level(price)] += float(quantity)
        return self

    def sell(self, price: float, quantity: float) -> "Bid":
        if quantity > 0:
            self.array[self._mkt.level(price)] -= float(quantity)
        return self

    def __repr__(self) -> str:
        parts = []
        for p, q in zip(self._mkt.price_grid, self.array):
            if abs(q) > 1e-9:
                side = "buy" if q > 0 else "sell"
                parts.append("%s %.2f kWh @ $%.2f" % (side, abs(q), p))
        return "Bid(" + "; ".join(parts) + ")" if parts else "Bid(empty)"


@dataclass
class TradeResult:
    """The outcome of the clearing for YOU, at the current timestep.

    price     the uniform clearing price, or None if nothing traded
    quantity  your net position: >0 you bought, <0 you sold, 0 no trade

    Whatever you do not settle in the market is settled with the grid:
    your grid position is (physical net energy - quantity).
    """

    price: Optional[float]
    quantity: float

    @property
    def traded(self) -> bool:
        return self.price is not None and abs(self.quantity) > 1e-9


# --------------------------------------------------------------------------
# What you write
# --------------------------------------------------------------------------
class Agent(ABC):
    """Base class for a submission.

    Subclass it, implement `bid` and `dispatch`, and expose the class as
    `AGENT` in your submission module. Both methods are called once per
    timestep, in this order:

        1. bid(obs, mkt)                 -> your bid vector
        2. ... the market clears centrally ...
        3. dispatch(obs, mkt, result)    -> your control actions

    So your dispatch already knows what price you cleared at. Use it.
    """

    def __init__(self, name: str, buildings: List[str]):
        self.name = name
        self.buildings = list(buildings)

    @abstractmethod
    def bid(self, obs: Dict[str, BuildingObs], mkt: MarketInfo) -> np.ndarray:
        """Return a bid vector of length `mkt.n_levels`.

        Element k is your signed quantity at price `mkt.price_grid[k]`:
        positive to buy, negative to sell, zero to do nothing at that price.
        """

    @abstractmethod
    def dispatch(self, obs: Dict[str, BuildingObs], mkt: MarketInfo,
                 result: TradeResult) -> Dict[str, Action]:
        """Return one Action per building you control, keyed by building name."""

    def reset(self) -> None:
        """Optional hook, called once before the simulation starts."""
