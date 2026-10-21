"""Fit seven-day RC surrogates with a published excitation schedule.

Run after `python -m annex96p2p.altered_stock build`.
"""
import argparse
import sys
from pathlib import Path

from annex96p2p.backends.citylearn_backend import CityLearnBackend
from annex96p2p.rc_thermal import identify
from annex96p2p.stock_dataset import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", default="../annex96_common_exercise_1")
    parser.add_argument("--dataset", default="generated/altered_stock/balanced")
    parser.add_argument("--out", default="scenarios/rc_models/balanced.json")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.upstream).resolve()))
    folder = Path(args.dataset).resolve()
    verify(folder)
    backend = CityLearnBackend(root=args.upstream, schema=str(folder/"schema.json"), days=7)
    result = identify(backend, folder/"schema.json", args.out)
    rows = result["models"]
    errors = sorted((r["validation_mae_c"], n) for n, r in rows.items())
    print("Fitted", len(rows), "RC models; median validation MAE",
          round(errors[len(errors)//2][0], 3), "C")
    print("Worst validation models:", [(n,round(e,2)) for e,n in errors[-5:]])
    print("Saved", args.out)


if __name__ == "__main__":
    main()
