"""Backend interface: whatever simulates the physics.

Two implementations ship with this repo:

  MockBackend       a small self-contained building simulator, pure numpy.
                    No CityLearn, no PyTorch, no dataset download. This is
                    what runs when you clone the repo and type `make smoke`.

  CityLearnBackend  the real Annex 96 Common Exercise 1 neighbourhood, via
                    the CityLearn environment. Opt-in, see docs/citylearn.md.

Agent code is identical against both. Develop against the mock backend
because it is fast, then confirm against CityLearn.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List

from ..api import Action, BuildingObs


@dataclass
class StepOutcome:
    """What one building did over one timestep."""

    net_kwh: float              # + consumed from the system, - produced
    degree_hours: float         # degC-hours outside the comfort band
    indoor_temperature: float
    battery_soc_kwh: float
    hp_electric_kwh: float
    battery_kwh: float          # + charged, - discharged
    base_load_kwh: float
    solar_kwh: float


class Backend(ABC):
    """A steppable neighbourhood of buildings."""

    #: Building names, in a stable order.
    building_names: List[str]

    #: Number of timesteps in the simulation.
    n_timesteps: int

    #: Hours per timestep.
    hours_per_step: float = 1.0

    @abstractmethod
    def reset(self) -> None:
        """Return to the initial state at timestep 0."""

    @abstractmethod
    def observe(self) -> Dict[str, BuildingObs]:
        """Observations for every building at the current timestep."""

    @abstractmethod
    def step(self, actions: Dict[str, Action]) -> Dict[str, StepOutcome]:
        """Apply one action per building and advance one timestep.

        Missing buildings are treated as doing nothing.
        """

    @property
    @abstractmethod
    def timestep(self) -> int:
        ...

    def metadata(self) -> Dict[str, dict]:
        """Static per-building facts, for printing and for choosing a portfolio."""
        return {name: {} for name in self.building_names}

    @property
    def done(self) -> bool:
        return self.timestep >= self.n_timesteps
