"""Marginal value of storage -- an opponent for a PV-rich home.

The same agent as marginal_value_storage.py (strategy in _marginal_value.py)
on a home with 40 kW of PV and a 4 kWh / 1 kW battery. Here the battery is
small, so most of the bid is the house itself: surplus PV it cannot store is
offered from $0.00, and its load is bought at up to the grid price.

    python examples/advanced_study.py marginal_generation
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _marginal_value import MarginalValueAgent      # noqa: E402

NAME = "marginal value generation"
# PV-rich home: 40 kW PV, 4 kWh / 1 kW battery.
BUILDINGS = ["resstock-amy2018-2021-release-1-112208"]

AGENT = MarginalValueAgent
