"""Make tests/stub.py importable as `stub` from any test module."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
