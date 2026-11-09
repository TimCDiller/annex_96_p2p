"""The market: a discrete-price uniform-price double auction.

Every timestep, each participant submits ONE signed quantity per price level.
Price levels run from $0.00/kWh up to the current grid import price, in
$0.01 steps. The two ends of that range are not arbitrary:

  $0.00                 you cannot export to the grid, so unsold surplus is
                        simply curtailed and earns nothing. A seller will
                        therefore accept any price above zero.
  grid import price     you can always buy from the grid at this price, so
                        no buyer will ever pay more than it inside the market.

Clearing follows the rule real power exchanges use: pick the price that
maximises the traded volume.

  demand(p)  = all BUY  quantities bid at a price >= p   (non-increasing in p)
  supply(p)  = all SELL quantities bid at a price <= p   (non-decreasing in p)
  volume(p)  = min(demand(p), supply(p))
  p*         = the price maximising volume(p)

Often several prices trade the same, maximal volume -- the bids are steps, so
the curves overlap along a stretch rather than crossing at a point. Ties are
broken the way exchanges break them:

  1. most volume;
  2. then the smallest imbalance |demand(p) - supply(p)|, i.e. the price
     closest to where the two curves cross. When supply is short this moves
     the price up the tied range, when demand is short down it: excess demand
     pushes the price up, excess supply pulls it down;
  3. then, if still tied, the middle price.

Everyone who is "in the money" at p* trades at p*, regardless of what they
bid. The short side of the market is filled completely, and the long side is
rationed pro rata. That guarantees the market balances exactly:

    sum of all cleared quantities == 0
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

TOL = 1e-9


@dataclass
class Clearing:
    """Result of one market clearing."""

    price: Optional[float]              # None when nothing traded
    volume: float                       # kWh traded
    quantities: Dict[str, float]        # participant -> net purchase (+buy/-sell)
    price_grid: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    demand_curve: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    supply_curve: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0))
    level_index: Optional[int] = None

    @property
    def traded(self) -> bool:
        return self.price is not None and self.volume > TOL

    def result_for(self, name: str):
        from .api import TradeResult
        return TradeResult(price=self.price, quantity=float(self.quantities.get(name, 0.0)))


def build_price_grid(import_price: float, step: float = 0.01) -> np.ndarray:
    """Legal bid prices for a timestep: 0.00, 0.01, ... up to the grid price.

    The top level never exceeds the grid import price, so a bid can never
    price itself above the outside option.
    """
    if import_price <= 0:
        return np.array([0.0])
    n = int(np.floor(round(import_price / step, 6))) + 1
    n = max(n, 1)
    return np.round(np.arange(n) * step, 10)


def validate_bid(raw, n_levels: int, max_kwh: float, who: str) -> Tuple[np.ndarray, List[str]]:
    """Coerce whatever a participant returned into a legal bid vector.

    A malformed bid never raises. It is repaired, the problem is recorded,
    and the simulation continues -- one broken submission must not be able
    to take down a tournament run.
    """
    notes: List[str] = []
    try:
        arr = np.asarray(raw, dtype=float).ravel()
    except Exception as exc:                                  # noqa: BLE001
        return np.zeros(n_levels), ["%s: bid not array-like (%s)" % (who, exc)]

    if arr.size != n_levels:
        notes.append("%s: bid length %d, expected %d -- padded/truncated"
                     % (who, arr.size, n_levels))
        fixed = np.zeros(n_levels)
        k = min(arr.size, n_levels)
        fixed[:k] = arr[:k]
        arr = fixed

    bad = ~np.isfinite(arr)
    if bad.any():
        notes.append("%s: %d non-finite bid entries zeroed" % (who, int(bad.sum())))
        arr = np.where(bad, 0.0, arr)

    if max_kwh is not None and max_kwh > 0:
        over = np.abs(arr) > max_kwh
        if over.any():
            notes.append("%s: %d bid entries clipped to +/-%g kWh"
                         % (who, int(over.sum()), max_kwh))
            arr = np.clip(arr, -max_kwh, max_kwh)

    return arr, notes


def clear(bids: Dict[str, np.ndarray], price_grid: np.ndarray) -> Clearing:
    """Clear the market for one timestep.

    `bids` maps participant name -> signed quantity per price level.
    Returns a Clearing whose quantities always sum to zero.
    """
    price_grid = np.asarray(price_grid, dtype=float)
    n = len(price_grid)
    names = list(bids.keys())

    if n == 0 or not names:
        return Clearing(None, 0.0, {k: 0.0 for k in names}, price_grid,
                        np.zeros(n), np.zeros(n))

    stacked = np.zeros((len(names), n))
    for i, name in enumerate(names):
        arr = np.asarray(bids[name], dtype=float).ravel()
        stacked[i, :min(n, arr.size)] = arr[:n]

    buy_at = np.maximum(stacked, 0.0)          # per participant, per level
    sell_at = np.maximum(-stacked, 0.0)

    # demand[j]: everyone willing to pay price_grid[j] or more
    demand = np.cumsum(buy_at.sum(axis=0)[::-1])[::-1]
    # supply[j]: everyone willing to accept price_grid[j] or less
    supply = np.cumsum(sell_at.sum(axis=0))
    volume = np.minimum(demand, supply)

    best = float(volume.max())
    if best <= TOL:
        return Clearing(None, 0.0, {k: 0.0 for k in names}, price_grid,
                        demand, supply)

    # Several prices can maximise volume. Take the one where demand and supply
    # are closest -- nearest the crossing -- and the middle of any remaining tie.
    tied = np.flatnonzero(volume >= best - TOL)
    gap = np.abs(demand[tied] - supply[tied])
    tied = tied[gap <= gap.min() + TOL]
    j = int(tied[len(tied) // 2])

    total_buy = float(demand[j])
    total_sell = float(supply[j])
    cleared = float(volume[j])

    buy_fill = cleared / total_buy if total_buy > TOL else 0.0
    sell_fill = cleared / total_sell if total_sell > TOL else 0.0

    quantities: Dict[str, float] = {}
    for i, name in enumerate(names):
        eligible_buy = float(buy_at[i, j:].sum())     # bid at price_grid[j] or above
        eligible_sell = float(sell_at[i, :j + 1].sum())  # bid at price_grid[j] or below
        quantities[name] = eligible_buy * buy_fill - eligible_sell * sell_fill

    # Kill floating point drift so the ledger balances to the cent.
    residual = sum(quantities.values())
    if abs(residual) > TOL and names:
        biggest = max(names, key=lambda k: abs(quantities[k]))
        quantities[biggest] -= residual

    return Clearing(price=float(price_grid[j]), volume=cleared, quantities=quantities,
                    price_grid=price_grid, demand_curve=demand, supply_curve=supply,
                    level_index=j)
