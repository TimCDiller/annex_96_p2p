"""Market forecast battery management -- an opponent for a fast storage home.

Strategy, every hour:

  1. Keep the battery between two limits: at least 1.5 hours of the house's
     own load (base load + heat pump) as a reserve, and at least 1.5 hours of
     its own PV generation as free room at the top.
  2. Forecast the internal market's clearing price for this hour and for the
     next FORECAST_HOURS hours (PriceForecaster: per hour of day, the share
     of the grid price it cleared at before, times the grid price ahead).
  3. Bid a step: at any price BELOW the forecast for the hours ahead, buy the
     house's load plus as much as the battery can take; at any price ABOVE
     it, cover the load from the battery and sell as much as it can give.
     Energy needed to get back up to the reserve is bought at any price.

The forecast for this hour does not change the bid -- a bid is a whole curve,
so it already says what to do at every price this hour might clear at. It is
logged, so its error can be checked against what the hour actually did.

FORECAST_HOURS = 1 compares each hour with the next only; 3 compares it with
the average of the next three, a calmer reference. SPREAD > 0 widens the step
into a dead band around the forecast (buy below F*(1-SPREAD), sell above
F*(1+SPREAD)); 0 is the strategy as stated, which ignores the 10% round-trip
loss.

Play against it with --field advanced or --field diverse (agents/fields.py),
or study it alone among default agents:

    python examples/advanced_study.py forecast_h1 forecast_h3
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _advanced_common import ETA, CurveAgent, battery_room      # noqa: E402

NAME = "market forecast battery management"
# Storage-rich home: 80 kWh, 80 kW (1C) -- the fast battery.
BUILDINGS = ["resstock-amy2018-2021-release-1-199613"]

FORECAST_HOURS = 1      # hours ahead the reference price averages over
RESERVE_HOURS = 1.5     # least charge kept: hours of the house's own load
HEADROOM_HOURS = 1.5    # least free room kept: hours of its own PV
SPREAD = 0.0            # dead band around the forecast, as a share of it


class MarketForecastBattery(CurveAgent):

    strategy = "market forecast battery management"
    FORECAST_HOURS = FORECAST_HOURS
    RESERVE_HOURS = RESERVE_HOURS
    HEADROOM_HOURS = HEADROOM_HOURS
    SPREAD = SPREAD

    def curve(self, o, mkt, hvac, need):
        prices = mkt.price_grid
        own_load = o.base_load_kwh + o.hp_draw_kwh(hvac)
        reserve = min(0.5 * o.battery_capacity_kwh, self.RESERVE_HOURS * own_load)
        pv = max(o.solar_generation_kwh, self.profiles[o.name].pv.get((o.hour + 2) % 24, 0.0))
        top = max(reserve, o.battery_capacity_kwh - self.HEADROOM_HOURS * pv)
        charge, discharge = battery_room(o, low=reserve, high=top)

        # Below the reserve: refill it at any price, as fast as the battery allows.
        must = min(o.battery_max_power_kw, max(0.0, reserve - o.battery_soc_kwh) / ETA)
        charge = max(0.0, charge - must)
        # Above the top: make room at any price.
        spill = min(o.battery_max_power_kw, max(0.0, o.battery_soc_kwh - top) * ETA)

        ahead = np.arange(1, self.FORECAST_HOURS + 1)
        reference = float(np.mean(self.prices.clearing(ahead)))
        now = float(self.prices.clearing(0))
        buy_below = reference * (1 - self.SPREAD)
        sell_above = reference * (1 + self.SPREAD)

        demand = np.full(len(prices), need + must - spill)
        demand[prices < buy_below - 1e-9] += charge
        demand[prices > sell_above + 1e-9] -= discharge

        self.offer_log.append(dict(
            timestep=mkt.timestep, building=o.name, strategy=self.strategy,
            forecast_now=now, forecast_ahead=reference, buy_below=buy_below,
            sell_above=sell_above, soc_kwh=o.battery_soc_kwh, reserve_kwh=reserve,
            top_kwh=top, need_kwh=need, charge_kwh=charge + must, discharge_kwh=discharge,
            planned_net_kwh=float(demand[0])))
        return demand


AGENT = MarketForecastBattery
