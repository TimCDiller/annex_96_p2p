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

    #: True if this building's heat pump is plumbed for HEATING, False for
    #: cooling. Each building actuates exactly ONE device, so only half of the
    #: `hvac` range does anything: on a heating building a negative command is
    #: discarded by the physics, on a cooling building a positive one is. Use
    #: `hp_draw_kwh()` rather than reading this directly.
    hp_is_heating: bool = True

    last_net_kwh: float = 0.0       # your net energy in the PREVIOUS timestep

    def hp_draw_kwh(self, hvac: float) -> float:
        """Electricity the heat pump will ACTUALLY draw for this command.

        Use this to work out what you are about to consume. The obvious
        expression is wrong:

            abs(hvac) * o.hp_max_electric_kw          # WRONG

        Every building actuates one device, never both, so the infeasible half
        of the command is silently dropped. Asking a heating-only heat pump to
        cool is simply "off" -- it costs nothing and does nothing. Bidding as
        though it drew full power means buying energy you never consume, and
        the surplus is curtailed rather than refunded.

        Measured on the Vermont neighbourhood, 62% of the thermostat's commands
        are the infeasible sign, so the wrong expression over-predicted demand
        by 20,020 kWh across five days.
        """
        usable = max(0.0, hvac) if self.hp_is_heating else max(0.0, -hvac)
        return usable * self.hp_max_electric_kw

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

    net_target_kwh   optional. Instead of a fixed battery rate, the grid
              exchange you want for this building this hour: positive to
              draw, negative to deliver -- typically what you bought or sold.
              The battery then follows the load in real time, as a home
              inverter does: it charges whatever surplus would otherwise be
              lost and discharges whatever shortfall would otherwise come
              from the grid, within its power and charge limits. Whatever it
              cannot absorb goes to the grid or is curtailed as usual.
              `battery` is ignored when this is set. You still do not see the
              hour's load or PV -- the battery reacts to them physically.
    """

    hvac: float = 0.0
    battery: float = 0.0
    net_target_kwh: Optional[float] = None


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


class Thermostat:
    """A competent thermostat. Use it, or beat it.

    Thermal control on the real CE1 homes is harder than it looks, for three
    reasons that only show up on that dataset:

      * the heat pumps are enormous relative to an hourly timestep. On the
        Vermont stock they draw up to 19.5 kW ELECTRIC, and one hour at full
        power moves the indoor temperature about 15 K. Anything that slams the
        compressor on and off overshoots the band in both directions -- an
        earlier version of this class did exactly that, oscillating a building
        between 14 and 30 degC in consecutive hours.
      * the heating load is large and steady, and that breaks proportional
        control specifically. A P controller can only produce output in
        proportion to error, so against a constant load it settles with a
        permanent offset -- it sits BELOW target forever, and cranking the gain
        up does not fix it. That is why this is a PI controller.
      * the comfort band MOVES. It is the occupant setpoint plus or minus
        `comfort_band`, and the ResStock schedules jump it by up to 7 K within
        a single building, sometimes 6 K in three hours. Some exceedance right
        after a step is simply unavoidable.

    Measured on the original Vermont neighbourhood, all 25 buildings, before
    SMOOTHING (below) was added, aiming at the
    centre of the band and skipping the first 48 h so that startup is excluded:

        proportional only, band 1.6 K       33.3% of hours in band
        proportional only, band widened      3.5% of hours in band
        this, proportional + integral       77.3% of hours in band

    Note the middle row: widening the proportional band cuts the worst
    excursion from 35 K to 10 K and still makes in-band WORSE, because it
    trades oscillation for a bigger steady offset. Integral action is what
    fixes this loop, not gain tuning.

    ONE-DIRECTIONAL HEAT PUMPS
    --------------------------
    A building actuates either a heating device or a cooling one, never both,
    so half of the returned range is inert: on a Vermont building a negative
    command is simply "off". That is fine to issue -- but do not bid against
    it. `BuildingObs.hp_draw_kwh()` tells you what a command actually costs.

    Usage -- one instance per agent, it keeps per-building state:

        class MyAgent(Agent):
            def reset(self):
                self.thermostat = Thermostat()

            def dispatch(self, obs, mkt, result):
                return {n: Action(hvac=self.thermostat(o), battery=0.0)
                        for n, o in obs.items()}

    PRE-COOLING AND PRE-HEATING
    ---------------------------
    Nothing here is price-aware, and that is the open question in this
    exercise. `aim` is the one dial, and it can be passed per call so you need
    not mutate anything:

        cheap = mkt.grid_import_price < self.threshold
        hvac = self.thermostat(o, aim=-0.6 if cheap else 0.4)

    Negative aim sits below the centre of the band, positive above it. The
    building fabric is a thermal store nobody charges you for, and it is
    usually a larger one than the actual battery.

    Whether it pays is genuinely not obvious. Measured on the original stock
    with the bare PI loop, moving the aim off
    centre does save energy -- but it spends comfort margin to do it, and
    comfort is PRICED, so the two run against each other:

        aim  where the target sits    in band   degC-h   imported
        0.00   band centre              77.3%     3351      34678
       -0.50   1.0 K above the floor    71.2%     3628      31393
       -0.85   0.3 K above the floor    57.2%     4027      29813

    `aim` may also exceed +1, which puts the target ABOVE the top of the band.
    That is legal and sometimes correct: in a dear enough hour the degree-hours
    can be cheaper than the kWh needed to avoid them. Nothing here overrides
    that -- the controller has no reflex forcing you back inside the band,
    because once comfort is priced, being outside it is an economic decision
    and not an error.

    SMOOTHING
    ---------
    The PI loop alone settles into a two-hour limit cycle on these homes: it
    acts once an hour on a heat pump that moves them about 1 K per kWh, so it
    overshoots, switches off, undershoots, switches on. On the altered Vermont
    stock consecutive hourly temperature changes correlated at -0.63, and one
    house paid $1,221 a month in comfort penalties for it. Each command is
    therefore blended with the previous hour's: `smoothing` is the share
    carried over. Measured on the altered stock, days 8-31, all 20 houses on
    the default agent:

        smoothing   K/h swing   lag-1 corr   degC-h   hours in band   bill
          0.0         1.66        -0.63       3315       84.7%       $6,794
          0.5         0.26        +0.30        141       97.7%       $5,990

    Holding the band costs heating energy (+15%): the oscillating loop saved
    energy by running houses cold, and in calm houses the smoothed loop costs
    a few percent more. Running colder is still available, deliberately --
    through `aim`, where it is a priced decision rather than a side effect of
    a control fault. `smoothing=0` restores the bare PI loop.
    """

    def __init__(self, aim: float = 0.0, gain: float = 0.11,
                 integral: float = 0.03, smoothing: float = 0.5):
        #: Where to sit inside the band, as a fraction of the half-width:
        #: 0 is the centre, -1 the bottom edge, +1 the top. The default targets
        #: the MIDDLE of the band.
        #:
        #: The middle is the neutral choice and it is season-agnostic, which
        #: the edges are not: the two Annex 96 datasets disagree about which
        #: edge is cheap. Cooling in Texas the top edge is, since every degree
        #: colder than necessary leaks straight back in; heating in Vermont the
        #: bottom edge is, for the mirror-image reason. Aiming at an edge also
        #: spends your whole margin: a target 0.3 K inside a 4 K band sits
        #: outside it on any ripple. See the table above for the trade.
        self.aim = aim
        #: Proportional gain: command per half-band of error. The default is
        #: small on purpose. These compressors are worth roughly nine
        #: half-bands per unit of command, so a gain near 1.0 -- which looks
        #: reasonable, and is what this class used to ship -- saturates on any
        #: error over 1.6 K and bang-bangs the building.
        self.gain = gain
        #: Integral gain, same units, accumulated per hour. This is the part
        #: that holds the setpoint against a steady heating load; without it
        #: the loop sits permanently below target however the gain is set.
        self.integral = integral
        #: Share of last hour's command carried into this hour's, 0 to <1.
        #: 0.5 removes the limit cycle; see SMOOTHING above.
        self.smoothing = float(min(0.95, max(0.0, smoothing)))
        self._acc = {}
        self._cache = {}
        self._last = {}

    def __call__(self, o, aim: float = None) -> float:
        # IDEMPOTENT PER TIMESTEP. Agents routinely need the heat pump command
        # twice in one hour -- once in bid(), to work out how much energy they
        # are about to consume, and again in dispatch() to actually issue it.
        # This object carries integrator state, so advancing it twice per hour
        # would wind it up at double rate and wreck the control. Cache instead,
        # keyed on the building and timestep.
        aim = self.aim if aim is None else aim
        key = (o.name, o.timestep, round(aim, 4))
        if key in self._cache:
            return self._cache[key]

        mid = 0.5 * (o.comfort_low + o.comfort_high)
        half = max(0.25, 0.5 * (o.comfort_high - o.comfort_low))
        target = mid + aim * half

        # Error in half-band units, positive when the building needs HEAT.
        # Signed and bidirectional throughout: the Annex 96 datasets are not
        # the same problem -- Texas actuates a cooling device in June, Vermont
        # a heating device in January -- and the same expression covers both.
        error = (target - o.indoor_temperature) / half

        acc = self._acc.get(o.name, 0.0) + error
        raw = self.gain * error + self.integral * acc
        u = min(1.0, max(-1.0, raw))

        # Anti-windup: only accumulate while the command is inside its range.
        # Without this, a building that cannot reach its target -- because the
        # band just stepped 7 K, or the compressor is genuinely too small for
        # a -25 degC night -- keeps integrating, and the command stays pinned
        # for hours after the error itself is gone.
        if abs(raw) <= 1.0:
            self._acc[o.name] = acc

        # Smoothing: blend with last hour's command, if there was one.
        last = self._last.get(o.name)
        if self.smoothing > 0 and last is not None and last[0] == o.timestep - 1:
            u = self.smoothing * last[1] + (1.0 - self.smoothing) * u
        self._last[o.name] = (o.timestep, u)

        if len(self._cache) > 4096:
            self._cache.clear()
        self._cache[key] = u
        return u              # negative cools, positive heats

