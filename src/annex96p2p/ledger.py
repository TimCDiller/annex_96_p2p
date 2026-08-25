"""Settlement, and the one table everything else is derived from.

For each participant i at each timestep:

    e_i   physical net energy of the buildings they control  (+consume/-produce)
    q_i   cleared peer-to-peer position                      (+bought/-sold)
    r_i   residual settled with the grid = e_i - q_i

    bill_i =  p_clear * q_i
            + p_import * max(0, r_i)          buy the shortfall from the grid
            - p_export * max(0, -r_i)         (p_export = 0 -> surplus curtailed)
            + comfort_price * degree_hours_i

There is no imbalance charge and no obligation to deliver what you bid,
because none is needed. The price grid is bounded by the grid import price
above and zero below, so over-selling means covering the gap at a higher
price than you were paid, and over-buying means curtailing energy you paid
for. Both lose money on their own. The grid is always the balancing party.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class Settlement:
    """One participant, one timestep."""

    timestep: int
    day: int
    hour: int
    participant: str
    is_default: bool

    net_kwh: float              # e_i, physical
    p2p_kwh: float              # q_i, cleared
    grid_import_kwh: float      # max(0, r_i)
    curtailed_kwh: float        # max(0, -r_i), exported/wasted

    clearing_price: Optional[float]
    grid_import_price: float

    p2p_cost: float
    grid_cost: float
    export_revenue: float
    degree_hours: float
    comfort_cost: float

    baseline_cost: float        # what the grid alone would have charged for e_i

    @property
    def energy_cost(self) -> float:
        return self.p2p_cost + self.grid_cost - self.export_revenue

    @property
    def total_cost(self) -> float:
        return self.energy_cost + self.comfort_cost


class Ledger:
    """Append-only record of every settlement. The single source of truth."""

    def __init__(self) -> None:
        self.rows: List[Settlement] = []
        self.market_rows: List[dict] = []
        self.warnings: List[str] = []

    # ---------------------------------------------------------------- record
    def settle(self, timestep: int, day: int, hour: int, participant: str,
               is_default: bool, net_kwh: float, p2p_kwh: float,
               clearing_price: Optional[float], grid_import_price: float,
               grid_export_price: float, degree_hours: float,
               comfort_price: float) -> Settlement:
        residual = net_kwh - p2p_kwh
        imported = max(0.0, residual)
        curtailed = max(0.0, -residual)

        price = 0.0 if clearing_price is None else float(clearing_price)
        row = Settlement(
            timestep=int(timestep), day=int(day), hour=int(hour),
            participant=participant, is_default=bool(is_default),
            net_kwh=float(net_kwh), p2p_kwh=float(p2p_kwh),
            grid_import_kwh=imported, curtailed_kwh=curtailed,
            clearing_price=clearing_price, grid_import_price=float(grid_import_price),
            p2p_cost=price * float(p2p_kwh),
            grid_cost=float(grid_import_price) * imported,
            export_revenue=float(grid_export_price) * curtailed,
            degree_hours=float(degree_hours),
            comfort_cost=float(comfort_price) * max(0.0, float(degree_hours)),
            # Reference case: no market at all, same physical energy.
            baseline_cost=(float(grid_import_price) * max(0.0, float(net_kwh))
                           - float(grid_export_price) * max(0.0, -float(net_kwh))),
        )
        self.rows.append(row)
        return row

    def record_market(self, timestep: int, day: int, hour: int,
                      price: Optional[float], volume: float,
                      grid_import_price: float, n_participants: int) -> None:
        self.market_rows.append(dict(
            timestep=int(timestep), day=int(day), hour=int(hour),
            clearing_price=price, volume_kwh=float(volume),
            grid_import_price=float(grid_import_price),
            n_participants=int(n_participants),
        ))

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    # ----------------------------------------------------------------- views
    def to_frame(self) -> pd.DataFrame:
        if not self.rows:
            return pd.DataFrame()
        frame = pd.DataFrame([r.__dict__ for r in self.rows])
        frame["energy_cost"] = frame.p2p_cost + frame.grid_cost - frame.export_revenue
        frame["total_cost"] = frame.energy_cost + frame.comfort_cost
        frame["savings_vs_grid_only"] = frame.baseline_cost - frame.energy_cost
        return frame

    def market_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.market_rows)

    def leaderboard(self) -> pd.DataFrame:
        """Per-participant summary, cheapest total bill first."""
        frame = self.to_frame()
        if frame.empty:
            return pd.DataFrame()

        agg = frame.groupby(["participant", "is_default"], as_index=False).agg(
            energy_cost=("energy_cost", "sum"),
            comfort_cost=("comfort_cost", "sum"),
            total_cost=("total_cost", "sum"),
            grid_only_cost=("baseline_cost", "sum"),
            net_kwh=("net_kwh", "sum"),
            # The `+ 0.0` turns an empty-slice -0.0 into a plain 0.0, which
            # otherwise prints as "-0.0" in the leaderboard.
            bought_kwh=("p2p_kwh", lambda s: float(s[s > 0].sum()) + 0.0),
            sold_kwh=("p2p_kwh", lambda s: float(-s[s < 0].sum()) + 0.0),
            grid_import_kwh=("grid_import_kwh", "sum"),
            curtailed_kwh=("curtailed_kwh", "sum"),
            degree_hours=("degree_hours", "sum"),
        )
        agg["market_gain"] = agg.grid_only_cost - agg.energy_cost
        with np.errstate(divide="ignore", invalid="ignore"):
            agg["market_gain_pct"] = np.where(
                agg.grid_only_cost.abs() > 1e-9,
                100.0 * agg.market_gain / agg.grid_only_cost.abs(), 0.0)
        agg = agg.sort_values("total_cost").reset_index(drop=True)
        agg.insert(0, "rank", agg.index + 1)
        return agg

    def system_summary(self) -> dict:
        frame = self.to_frame()
        market = self.market_frame()
        if frame.empty:
            return {}
        traded = market[market.clearing_price.notna()] if not market.empty else market
        return dict(
            timesteps=int(frame.timestep.nunique()),
            participants=int(frame.participant.nunique()),
            total_bill=float(frame.total_cost.sum()),
            total_energy_cost=float(frame.energy_cost.sum()),
            total_comfort_cost=float(frame.comfort_cost.sum()),
            grid_only_bill=float(frame.baseline_cost.sum()),
            system_market_gain=float(frame.baseline_cost.sum() - frame.energy_cost.sum()),
            p2p_volume_kwh=float(market.volume_kwh.sum()) if not market.empty else 0.0,
            grid_import_kwh=float(frame.grid_import_kwh.sum()),
            curtailed_kwh=float(frame.curtailed_kwh.sum()),
            degree_hours=float(frame.degree_hours.sum()),
            clearing_rate=(float(len(traded)) / len(market)) if len(market) else 0.0,
            mean_clearing_price=(float(traded.clearing_price.mean())
                                 if len(traded) else float("nan")),
        )

    # ------------------------------------------------------------ invariants
    def check_balance(self, tol: float = 1e-6) -> List[str]:
        """Every timestep must have peer-to-peer positions summing to zero."""
        frame = self.to_frame()
        if frame.empty:
            return []
        sums = frame.groupby("timestep").p2p_kwh.sum()
        bad = sums[sums.abs() > tol]
        return ["timestep %d: p2p positions sum to %.3g" % (t, v)
                for t, v in bad.items()]
