"""Prices: what the grid charges, what it pays, and what discomfort costs.

The import price is time varying. It is built as a wholesale energy price
plus a flat delivery adder, which is how a real retail bill is put together:

    import price = wholesale energy + delivery adder

The export price defaults to 0.00 $/kWh. That is the Common Exercise 1 rule
that PV may not be sold to the grid, carried over unchanged -- and it is
exactly what makes the peer-to-peer market worth having. A surplus that
cannot be exported is curtailed and earns nothing, so any peer willing to
pay more than zero is better than the grid.

Discomfort is priced rather than merely reported. Without that, the cheapest
possible strategy is to switch the heat pump off and let the building drift,
which is not a strategy anyone wants students to discover. Every degree-hour
outside the comfort band is added to the bill at `comfort_price`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from .market import build_price_grid

#: Hourly wholesale shape ($/kWh), loosely modelled on an ERCOT summer day:
#: cheap overnight, a long flat morning, a steep ramp into an evening peak.
DEFAULT_WHOLESALE_SHAPE = np.array([
    0.022, 0.020, 0.019, 0.019, 0.021, 0.026, 0.032, 0.035,
    0.033, 0.030, 0.030, 0.033, 0.040, 0.055, 0.080, 0.120,
    0.190, 0.280, 0.310, 0.240, 0.140, 0.080, 0.045, 0.030,
])


@dataclass
class Tariff:
    """Price signals for the whole simulation."""

    import_prices: np.ndarray          # $/kWh, one entry per timestep
    export_price: float = 0.0          # $/kWh paid for grid export (0 = banned)
    comfort_price: float = 3.0         # $ per degC-hour outside the comfort band
    price_step: float = 0.01           # bid price resolution

    def import_price(self, t: int) -> float:
        return float(self.import_prices[int(t) % len(self.import_prices)])

    def price_grid(self, t: int) -> np.ndarray:
        return build_price_grid(self.import_price(t), self.price_step)

    @classmethod
    def synthetic(cls, n_timesteps: int, delivery_adder: float = 0.075,
                  volatility: float = 0.15, seed: int = 0,
                  wholesale_shape: Optional[Sequence[float]] = None,
                  **kwargs) -> "Tariff":
        """Build a deterministic, reproducible price series.

        Day-to-day variation comes from a fixed sinusoid rather than random
        draws, so every student sees an identical price series and results
        can be compared directly.
        """
        shape = np.asarray(wholesale_shape if wholesale_shape is not None
                           else DEFAULT_WHOLESALE_SHAPE, dtype=float)
        hours = np.arange(n_timesteps)
        day = hours // len(shape)
        wholesale = shape[hours % len(shape)]
        # Deterministic multi-day cycle: some days are expensive, some are not.
        multiplier = 1.0 + volatility * np.sin(2 * np.pi * (day + seed) / 5.0)
        prices = np.round(wholesale * multiplier + delivery_adder, 4)
        return cls(import_prices=np.maximum(prices, 0.01), **kwargs)

    @classmethod
    def from_csv(cls, path: str, column: str = "electricity_pricing", **kwargs) -> "Tariff":
        """Load an hourly price series from CSV, e.g. real ISO day-ahead data."""
        import pandas as pd
        frame = pd.read_csv(path)
        return cls(import_prices=frame[column].to_numpy(dtype=float), **kwargs)

    def comfort_cost(self, degree_hours: float) -> float:
        return self.comfort_price * max(0.0, float(degree_hours))
