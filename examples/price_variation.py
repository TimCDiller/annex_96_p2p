"""Controlled synthetic tariff experiment on the four-new-agent market.

Run from the repo root with CityLearn dependencies:
    python examples/price_variation.py

All three tariffs have the same mean and delivery floor. High spread retains
peak timing; shifting peaks also moves that shape between hours each day.
No agents are retuned or given future prices. These are synthetic stress tests,
not historical tariffs. The original default configuration is unchanged.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples"))

import numpy as np
import pandas as pd

from daily_trading import participants
from annex96p2p.cli import make_backend, make_tariff
from annex96p2p.config import Config
from annex96p2p.report import save
from annex96p2p.runner import simulate
from annex96p2p.tariff import Tariff


def price_profiles(prices, floor=0.075, seed=96):
    """Increase spread without changing mean, then separately vary peak timing."""
    prices = np.asarray(prices, dtype=float)
    wholesale = np.maximum(prices - floor, 0.0)
    mean = wholesale.mean()
    shaped = wholesale ** 1.8
    amplified = floor + shaped * mean / shaped.mean()
    rng = np.random.default_rng(seed)
    shifted = amplified.copy()
    shifts = []
    for start in range(0, len(prices), 24):
        shift = int(rng.choice([-8, -4, 0, 4, 8]))
        shifts.append(shift)
        shifted[start:start + 24] = np.roll(amplified[start:start + 24], shift)
    profiles = {"baseline": prices.copy(), "high_spread": amplified,
                "shifting_peaks": shifted}
    assert all(np.isclose(p.mean(), prices.mean()) and (p >= floor).all()
               for p in profiles.values())
    return profiles, shifts


def summarize(folder, result, physical):
    market = result.ledger.market_frame().copy()
    ledger = result.ledger.to_frame()
    sales = ledger.assign(sold_kwh=(-ledger.p2p_kwh).clip(lower=0))
    market["sold_kwh"] = market.timestep.map(sales.groupby("timestep").sold_kwh.sum())
    market["backed_kwh"] = market.timestep.map(
        physical.groupby("timestep").physically_backed_sales_kwh.sum())
    night = market.hour.between(0, 6)
    traded = market.sold_kwh > 1e-6
    daily = market.groupby("day").agg(sold_kwh=("sold_kwh", "sum"),
        backed_kwh=("backed_kwh", "sum"), trading_hours=("sold_kwh", lambda s: int((s > 1e-6).sum())))
    daily.to_csv(folder / "daily.csv")
    market.to_csv(folder / "hourly_audit.csv", index=False)
    s = dict(result.summary)
    s.update(price_mean=market.grid_import_price.mean(), price_std=market.grid_import_price.std(ddof=0),
        price_min=market.grid_import_price.min(), price_max=market.grid_import_price.max(),
        net_sold_kwh=market.sold_kwh.sum(), surplus_backed_kwh=market.backed_kwh.sum(),
        trading_hours=int(traded.sum()), days_with_trade=int((daily.sold_kwh > 1e-6).sum()),
        days_with_surplus=int((daily.backed_kwh > 1e-6).sum()),
        night_trading_hours=int((traded & night).sum()), night_sold_kwh=market.loc[night, "sold_kwh"].sum(),
        night_backed_kwh=market.loc[night, "backed_kwh"].sum(),
        weighted_trade_price=(market.clearing_price.fillna(0) * market.sold_kwh).sum() / market.sold_kwh.sum(),
        comfortable_pct=100 * physical.comfortable_building_hours.sum() / physical.building_hours.sum(),
        final_battery_kwh=physical.groupby("participant").battery_soc_kwh.last().sum(),
        failures=sum(p.failures for p in result.participants), timeouts=sum(p.timeouts for p in result.participants))
    return s


def report(out):
    summary = pd.read_csv(out / "comparison.csv", index_col=0)
    columns = ["price_mean", "price_std", "price_min", "price_max", "net_sold_kwh",
        "surplus_backed_kwh", "trading_hours", "days_with_trade", "days_with_surplus",
        "night_trading_hours", "night_sold_kwh", "night_backed_kwh", "total_bill",
        "total_energy_cost", "total_comfort_cost", "grid_import_kwh", "curtailed_kwh",
        "comfortable_pct", "final_battery_kwh"]
    lines = ["Synthetic grid-price experiment: identical buildings and agents", "",
        summary[columns].T.round(3).to_string(), "",
        "All profiles have the same unweighted monthly mean. High spread preserves peak timing;",
        "shifting peaks moves the amplified daily profiles by seeded offsets (-8,-4,0,4,8 hours).",
        "Night = 00:00 through 06:00 (217 hours). Agents have no future-price access and are not retuned.",
        "Bills include $0.50/degree-hour discomfort, but no terminal battery credit or degradation charge.",
        "Startup and the final partial day are included. This is one synthetic price realization.",
        "Equal mean price does not imply equal bills for the same consumption: load timing matters.", ""]
    boards = []
    for name in summary.index:
        board = pd.read_csv(out / name / "leaderboard.csv")
        board.insert(0, "scenario", name)
        boards.append(board)
    agents = pd.concat(boards)
    agents.to_csv(out / "agent_comparison.csv", index=False)
    names = ["naive_spot", "naive_clock", "minimum_tariff", "minimum_adaptive"]
    lines += ["Bills of the four new agents:", agents[agents.participant.isin(names)].pivot(
        index="participant", columns="scenario", values="total_cost").round(2).to_string()]
    (out / "comparison.txt").write_text("\n".join(lines), encoding="utf-8")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 1, figsize=(13, 10))
    labels = {"baseline": "Original prices", "high_spread": "Larger spread, same timing",
              "shifting_peaks": "Larger spread + shifting peaks"}
    for name, label in labels.items():
        hourly = pd.read_csv(out / name / "hourly_audit.csv")
        week = hourly.iloc[:168]
        axes[0].plot(1 + week.timestep / 24, week.grid_import_price, label=label, alpha=0.85)
        daily = pd.read_csv(out / name / "daily.csv")
        axes[1].plot(daily.day + 1, daily.backed_kwh, label=label)
        profile = hourly.groupby("hour").backed_kwh.sum()
        axes[2].plot(profile.index, profile, marker=".", label=label)
    axes[0].set(title="Price experiment — same mean price, unchanged agents", ylabel="Grid price ($/kWh)", xlabel="Day (first week)")
    axes[0].legend(fontsize=9)
    axes[1].set(ylabel="Surplus-backed sales (kWh/day)", xlabel="Day (full month)")
    axes[2].set(ylabel="Surplus-backed sales (kWh/month)", xlabel="Hour of day")
    axes[2].set_xticks(range(24))
    axes[2].axvspan(-0.5, 6.5, alpha=0.08, color="navy")
    for ax in axes:
        ax.set_ylim(bottom=0)
        ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(out / "price_comparison.png", dpi=150)
    plt.close(fig)


def main():
    cfg = Config.load(str(ROOT / "config/default.yaml"))
    cfg.days = None
    cfg.citylearn_root = str((ROOT / cfg.citylearn_root).resolve())
    cfg.citylearn_schema = str((ROOT / cfg.citylearn_schema).resolve())
    sys.path.insert(0, cfg.citylearn_root)
    import torch
    torch.set_num_threads(1)
    out = ROOT / "results/price_variation"
    out.mkdir(parents=True, exist_ok=True)
    backend = make_backend(cfg)
    base = make_tariff(cfg, backend.n_timesteps)
    profiles, shifts = price_profiles(base.import_prices, cfg.delivery_adder)
    (out / "experiment.json").write_text(json.dumps(dict(config=vars(cfg), seed=96,
        exponent=1.8, daily_shifts_hours=shifts), indent=2), encoding="utf-8")
    summaries = {}
    for name, prices in profiles.items():
        print("Starting", name, "range:", prices.min(), prices.max(), "mean:", prices.mean(), flush=True)
        folder = out / name
        folder.mkdir(exist_ok=True)
        pd.DataFrame({"electricity_pricing": prices}).to_csv(folder / "prices.csv", index=False)
        tariff = Tariff(prices, export_price=cfg.export_price, comfort_price=cfg.comfort_price,
                        price_step=cfg.price_step)
        parts = participants(backend, "new_agents")
        physical = []

        def record(step):
            for p in parts:
                outcomes = [step["outcomes"][b] for b in p.buildings]
                net = sum(o.net_kwh for o in outcomes)
                sold = max(0.0, -step["clearing"].quantities.get(p.name, 0.0))
                physical.append(dict(timestep=step["timestep"], participant=p.name,
                    physically_backed_sales_kwh=min(sold, max(0.0, -net)),
                    comfortable_building_hours=sum(o.degree_hours <= 1e-9 for o in outcomes),
                    building_hours=len(outcomes), battery_soc_kwh=sum(o.battery_soc_kwh for o in outcomes)))

        result = simulate(backend, parts, tariff, recorder=record,
            call_timeout_seconds=cfg.call_timeout_seconds, run_budget_seconds=cfg.run_budget_seconds)
        assert not result.ledger.check_balance()
        assert all(not p.failures and not p.timeouts and not p.disqualified for p in parts)
        save(result, str(folder))
        physical = pd.DataFrame(physical)
        physical.to_csv(folder / "physical.csv", index=False)
        (folder / "assignments.json").write_text(json.dumps({p.name:p.buildings for p in parts}, indent=2))
        summaries[name] = summarize(folder, result, physical)
        pd.DataFrame(summaries).T.to_csv(out / "comparison.csv")
        print(name, json.dumps(summaries[name]), flush=True)
    report(out)
    print("Results:", out, flush=True)


if __name__ == "__main__":
    main()
