"""Compare matched thermostat and RC runs; write CSV and a shareable chart."""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PEAK = (6, 7, 8, 16, 17, 18, 19, 20)
PRE_PEAK = (4, 5, 13, 14, 15)
EVALUATION_START = 168


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-runs", default="results/rc_comparison")
    parser.add_argument("--rc-runs", default="results/rc_comparison")
    parser.add_argument("--out", default="results/rc_preheat_report")
    args = parser.parse_args()
    baseline_root, rc_root, out = map(Path,
        (args.baseline_runs, args.rc_runs, args.out))
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    curves = {}
    for config in ("balanced", "solar_abundant"):
        records = {}
        for label, root, mode in (("thermostat", baseline_root, "competitive"),
                                  ("RC preheat", rc_root, "rc_preheat")):
            folder = root/config/mode
            summary = pd.read_json(folder/"summary.json", typ="series")
            hourly = pd.read_csv(folder/"buildings_hourly.csv")
            evaluation = hourly[hourly.timestep >= EVALUATION_START]
            peak = evaluation[evaluation.hour.isin(PEAK)]
            pre = evaluation[evaluation.hour.isin(PRE_PEAK)]
            rows.append(dict(configuration=config, controller=label,
                full_month_bill_usd=summary.total_bill,
                full_month_comfort_pct=summary.comfortable_pct,
                evaluation_comfort_pct=100*(evaluation.degree_hours<=1e-9).mean(),
                evaluation_degree_hours=evaluation.degree_hours.sum(),
                evaluation_hvac_kwh=evaluation.hvac_kwh.sum(),
                peak_hvac_kwh=peak.hvac_kwh.sum(),
                pre_peak_hvac_kwh=pre.hvac_kwh.sum(),
                days_with_backed_sales=summary.days_with_backed_sales,
                agent_failures=summary.failures))
            records[label] = (evaluation, folder)
        curves[config] = records
    table = pd.DataFrame(rows)
    table.to_csv(out/"comparison.csv", index=False)

    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    for ax, config in zip(axes, ("balanced", "solar_abundant")):
        records = curves[config]
        for label, color in (("thermostat", "#287c9b"),("RC preheat", "#c55d3d")):
            hourly = records[label][0]
            # Mean per calendar hour across the held-out evaluation days.
            daily = hourly.groupby(["day","hour"]).hvac_kwh.sum().reset_index()
            profile = daily.groupby("hour").hvac_kwh.mean().reindex(range(24))
            ax.plot(range(24),profile,marker=".",color=color,label=label)
        for hour in PEAK:
            ax.axvspan(hour-.5,hour+.5,color="#b7c5d2",alpha=.22)
        ax.set(title=config.replace("_"," ").title(),ylabel="Mean heating kWh / hour")
        ax.grid(axis="y",alpha=.25)
        ax.legend(loc="upper left")
        if config == "balanced":
            price = pd.read_csv(records["thermostat"][1]/"hourly_audit.csv")
            price = price[price.timestep>=EVALUATION_START].groupby("hour").grid_import_price.mean()
            right = ax.twinx()
            right.plot(range(24),price.reindex(range(24)),color="#a87815",alpha=.7,
                       linestyle="--",label="Grid price")
            right.set_ylabel("Mean grid price ($/kWh)")
            right.legend(loc="upper right")
    axes[-1].set(xlabel="Hour of day",xticks=range(0,24,2))
    fig.suptitle("RC preheating after the seven-day identification window")
    fig.tight_layout()
    fig.savefig(out/"hourly_heating.png",dpi=160)
    plt.close(fig)
    lines = ["RC PREHEATING VS ORDINARY THERMOSTAT", "",
        "Identification: first five days; model validation: next two days.",
        "Evaluation: timestep 168 onward, same buildings, weather and tariff.",
        "Peak hours: 06-08 and 16-20. Pre-peak hours: 04-05 and 13-15.",
        "HVAC kWh is electric heat-pump input. Bills cover all 743 transitions.", "",
        table.round(2).to_string(index=False)]
    for config, group in table.groupby("configuration"):
        b, r = (group[group.controller==label].iloc[0]
                for label in ("thermostat","RC preheat"))
        lines.extend(("",config+" changes (RC minus thermostat):",
            "comfortable hours: %+.2f percentage points" % (r.evaluation_comfort_pct-b.evaluation_comfort_pct),
            "peak heating: %+.1f kWh" % (r.peak_hvac_kwh-b.peak_hvac_kwh),
            "pre-peak heating: %+.1f kWh" % (r.pre_peak_hvac_kwh-b.pre_peak_hvac_kwh),
            "total heating: %+.1f kWh" % (r.evaluation_hvac_kwh-b.evaluation_hvac_kwh),
            "full-month bill: $%+.2f" % (r.full_month_bill_usd-b.full_month_bill_usd)))
    (out/"comparison.txt").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print("Wrote",out/"comparison.csv",out/"hourly_heating.png",flush=True)


if __name__ == "__main__":
    main()
