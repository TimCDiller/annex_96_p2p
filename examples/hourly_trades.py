"""Plot saved hourly settlements and prices without rerunning the simulation.

    python examples/hourly_trades.py --run results/daily_trading/new_agents

HTML is self-contained and requires plotly; the PNG preview uses matplotlib.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", default="results/daily_trading/new_agents")
    args = parser.parse_args()
    folder = Path(args.run)
    market = pd.read_csv(folder / "market.csv")
    ledger = pd.read_csv(folder / "settlements.csv")
    sellers = ledger[ledger.p2p_kwh < -1e-9].copy()
    sellers["sold_kwh"] = -sellers.p2p_kwh
    sold = sellers.groupby("timestep").sold_kwh.sum()
    market["sold_kwh"] = market.timestep.map(sold).fillna(0.0)
    assert np.allclose(ledger.groupby("timestep").p2p_kwh.sum(), 0.0, atol=1e-6)
    traded = market.sold_kwh > 1e-9
    assert market.loc[traded, "clearing_price"].notna().all()
    assert (market.loc[traded, "clearing_price"] <=
            market.loc[traded, "grid_import_price"] + 1e-9).all()
    market["discount_pct"] = np.where(traded,
        100 * (1 - market.clearing_price / market.grid_import_price), np.nan)
    labels = ["Day %d, %02d:00" % (d + 1, h) for d, h in zip(market.day, market.hour)]
    details = sellers.groupby("timestep")[["participant", "sold_kwh"]].apply(
        lambda g: "<br>".join("%s: %.2f kWh" % (r.participant, r.sold_kwh)
                              for r in g.itertuples()))
    breakdown = market.timestep.map(details).fillna("No sales")
    custom = np.column_stack([labels, breakdown])
    market.to_csv(folder / "hourly_trades.csv", index=False)

    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        vertical_spacing=0.10, row_heights=[0.55, 0.45])
    fig.add_trace(go.Bar(x=market.timestep, y=market.sold_kwh,
        name="Energy sold to peers", marker_color="#287c9b", customdata=custom,
        hovertemplate="%{customdata[0]}<br>Sold: %{y:.2f} kWh<br>%{customdata[1]}<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Scatter(x=market.timestep, y=market.volume_kwh,
        name="Gross auction volume (before participant netting)", visible="legendonly",
        mode="lines", line=dict(color="#939393", dash="dot"),
        hovertemplate="Gross auction: %{y:.2f} kWh<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Scatter(x=market.timestep, y=market.grid_import_price,
        name="Grid price / price ceiling", mode="lines", line=dict(color="#c17b17", width=2),
        customdata=labels, hovertemplate="%{customdata}<br>Grid: $%{y:.4f}/kWh<extra></extra>"), row=2, col=1)
    fig.add_trace(go.Scatter(x=market.timestep, y=market.clearing_price.where(traded),
        name="Trade clearing price", mode="markers", marker=dict(color="#136e58", size=5),
        customdata=np.column_stack([labels, market.discount_pct]),
        hovertemplate="%{customdata[0]}<br>Trade: $%{y:.4f}/kWh<br>Below grid: %{customdata[1]:.1f}%<extra></extra>"), row=2, col=1)
    end = int(market.timestep.max()) + 0.5
    choices = [("Full month", [-0.5, end]), ("First week", [-0.5, 167.5])]
    choices += [("Day %d" % (d + 1), [d * 24 - 0.5, min(end, (d + 1) * 24 - 0.5)])
                for d in sorted(market.day.unique())]
    fig.update_layout(template="plotly_white", height=780,
        title=dict(text="Hourly peer-to-peer sales and prices — four new strategies<br>"
                         "<sup>Vermont January · 25 houses · 743 hourly transitions · click a legend item to toggle</sup>", x=0.04),
        hovermode="x unified", bargap=0.08,
        legend=dict(orientation="h", y=-0.23, x=0),
        margin=dict(l=75, r=35, t=120, b=150),
        updatemenus=[dict(buttons=[dict(label=label, method="relayout",
            args=[{"xaxis.range": bounds, "xaxis2.range": bounds}]) for label, bounds in choices],
            x=1, y=1.13, xanchor="right", yanchor="top")])
    ticks = np.arange(0, len(market), 24)
    fig.update_xaxes(tickvals=ticks, ticktext=["Day %d" % (t // 24 + 1) for t in ticks],
                     range=[-0.5, end])
    fig.update_xaxes(title_text="Simulation time (select a day above, or drag to zoom)", row=2, col=1)
    fig.update_yaxes(title_text="Sold (kWh per hour)", rangemode="tozero", row=1, col=1)
    fig.update_yaxes(title_text="Price ($/kWh)", rangemode="tozero", row=2, col=1)
    total = market.sold_kwh.sum()
    average = (market.sold_kwh * market.clearing_price.fillna(0)).sum() / total
    grid_average = (market.sold_kwh * market.grid_import_price).sum() / total
    subtitle = ("<p><b>%.1f kWh sold across %d trading hours.</b> "
                "Volume-weighted trade price: $%.3f/kWh; grid price in those same hours, "
                "weighted by traded volume: $%.3f/kWh.</p>" %
                (total, traded.sum(), average, grid_average))
    note = ("<p>Bars count sellers' net settled sales once, not both the buyer and seller. "
            "All trades within an hour receive the same clearing price. Missing price markers "
            "mean no trade. Hover over a bar for the sellers and their quantities. "
            "Gross auction volume is available in the legend; it can be higher when an "
            "agent bids on both sides. Sales include grid-backed deliveries, not only household surplus. "
            "Day 31 contains 23 transitions. Times follow the simulator's hourly ledger.</p>")
    plot = fig.to_html(full_html=False, include_plotlyjs=True,
                       config={"displaylogo": False, "responsive": True,
                               "toImageButtonOptions": {"filename": "hourly_trades", "scale": 2}})
    (folder / "hourly_trades.html").write_text(
        '<!doctype html><html><head><meta charset="utf-8"><title>Hourly electricity trades</title>'
        '<style>body{font:16px system-ui;margin:24px auto;max-width:1400px;color:#23303a}'
        'p{padding:0 24px;line-height:1.5}</style></head><body>' + subtitle + plot + note +
        '</body></html>', encoding="utf-8")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(15, 7.5), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1]})
    x = 1 + market.timestep / 24
    axes[0].bar(x, market.sold_kwh, width=0.037, color="#287c9b")
    axes[0].set_ylabel("Sold (kWh per hour)")
    axes[0].set_title("Hourly peer-to-peer trades — Vermont, four new strategies", loc="left", fontsize=16)
    axes[1].plot(x, market.grid_import_price, color="#c17b17", linewidth=1.2,
                 label="Grid price / ceiling")
    axes[1].scatter(x[traded], market.loc[traded, "clearing_price"], color="#136e58",
                    s=9, label="Trade clearing price", zorder=3)
    axes[1].set_ylabel("Price ($/kWh)")
    axes[1].set_xlabel("Simulation day; each bar/marker represents one hour")
    axes[1].legend(loc="upper right", ncol=2)
    axes[1].set_xticks(np.arange(1, 32))
    for ax in axes:
        ax.set_ylim(bottom=0)
        ax.set_xlim(0.7, 32)
        ax.grid(axis="y", alpha=0.2)
        ax.spines[["top", "right"]].set_visible(False)
    fig.text(0.065, 0.015, "Net settled sales; missing price markers = no trade. "
             "Open the HTML for hourly hover details and day selection. Final day: 23 hours.", fontsize=9)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(folder / "hourly_trades.png", dpi=160)
    plt.close(fig)
    print("Sold %.3f kWh in %d hours; weighted clearing $%.4f, weighted grid $%.4f" %
          (total, traded.sum(), average, grid_average))
    print("Saved", folder / "hourly_trades.html")


if __name__ == "__main__":
    main()
