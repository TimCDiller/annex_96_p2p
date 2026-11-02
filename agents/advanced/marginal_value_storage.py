"""Marginal value of storage -- an opponent for a storage-rich home.

Bids every slice of its battery at the marginal value of stored energy, found
by re-running a 30-hour cost optimisation from a slightly lower and a
slightly higher starting charge. The strategy is described in full in
_marginal_value.py; marginal_value_generation.py runs the same agent on a
PV-rich home.

    python examples/advanced_study.py marginal_storage
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _marginal_value import MarginalValueAgent      # noqa: E402

NAME = "marginal value storage"
# Storage-rich home: 80 kWh, 20 kW (0.25C).
BUILDINGS = ["resstock-amy2018-2021-release-1-147002"]

AGENT = MarginalValueAgent
