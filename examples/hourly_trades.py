"""Plot saved hourly settlements and prices without rerunning the simulation.

    python examples/hourly_trades.py --run results/altered_stock/balanced/competitive
    python examples/hourly_trades.py --run results/price_variation/shifting_peaks

Writes hourly_trades.html (self-contained, needs plotly), hourly_trades.png
(matplotlib) and hourly_trades.csv into the run folder. The HTML has seven panels:
peer-to-peer volume, prices, the clearing price as a percentage of the grid
price, and -- for one selectable building or group -- its buy/sell bars, its
battery state of charge, its bids as a price-time heatmap (from bids.csv,
written by the course runner), and its grid imports coloured by the grid price.

Trades are settled per participant, so a building's own trades are exact only
when it is its own participant -- every house in an altered-stock
`competitive` run. A participant owning several houses appears as one entry.
Participants that ran their own agent rather than the default one
(is_default is false in settlements.csv) are highlighted: marked with a star
in the selector, which opens on them, split out in the top panel, and ringed
in the price panels in the hours they sold.
Altered-stock runs (they write buildings_hourly.csv) also get the
generation_heavy (role pv) and storage_heavy (role storage) aggregates, and
per-house battery state of charge; capacities come from the generated
dataset's buildings.csv (--dataset), so SOC can be shown as a percentage.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROLE_GROUPS = {"pv": "generation_heavy", "storage": "storage_heavy"}
BUY_COLOR, SELL_COLOR, SOC_COLOR = "#2a6fb0", "#c4472f", "#2e8b57"
# Bid heatmap: green = willing to sell at that price, red = willing to buy.
BID_SCALE = [[0.0, "#0b6b2e"], [0.25, "#7cc68f"], [0.5, "#ffffff"],
             [0.75, "#f08a7e"], [1.0, "#a3121f"]]
PRICE_STEP = 0.01
PEAK_PRICE = 0.20     # $/kWh; the default agent's own definition of a peak hour
PLAYER_COLORS = ["#8e44ad", "#d35400", "#16a085", "#b8860b"]


def short(building):
    return building.rsplit("-", 1)[-1]


def selections(folder, ledger):
    """[(label, participants)] for the building selector, groups first.

    Participants running their own agent come right after the groups, starred.
    """
    owners = ledger.participant.unique().tolist()
    players = (sorted(ledger.loc[~ledger.is_default.astype(bool), "participant"].unique())
               if "is_default" in ledger else [])
    path = folder / "assignments.json"
    houses = (json.loads(path.read_text()) if path.exists()
              else {p: [p] for p in owners})
    roles = {}
    path = folder / "buildings_hourly.csv"
    if path.exists():
        frame = pd.read_csv(path, usecols=["building", "role"]).drop_duplicates()
        roles = dict(zip(frame.building, frame.role))

    def group_of(participant):
        found = {ROLE_GROUPS.get(roles.get(b)) for b in houses.get(participant, [])}
        return found.pop() if len(found) == 1 else None

    options = [("Whole market", owners)]
    for role, group in ROLE_GROUPS.items():
        members = [p for p in owners if group_of(p) == group]
        if members:
            count = sum(len(houses.get(p, [p])) for p in members)
            options.append(("%s (%d houses)" % (group, count), members))
    def label_of(p):
        own = houses.get(p, [p])
        tag = group_of(p) or ("mixed" if roles else "")
        if p in players:
            label = "★ %s — %s" % (p, ", ".join("house %s" % short(b) for b in own))
        elif len(own) == 1:
            label = "house %s" % short(own[0])
            if p not in (own[0], "default_" + own[0]):
                label += " — %s" % p
        else:
            label = "%s (%d houses)" % (p, len(own))
        return label + (" · %s" % tag if tag else "")

    options += [(label_of(p), [p]) for p in players]
    rest = [p for p in owners if p not in players]
    options += [(label_of(p), [p]) for p in sorted(rest, key=lambda p: (group_of(p) or "~", p))]
    return options, houses, players, label_of


def bid_curves(folder, timesteps, participants):
    """{participant: array[price level, hour]} of willingness to trade, or None.

    Positive = kWh it would BUY at that price (its buy bids at that price or
    dearer), negative = kWh it would SELL at that price (its asks at that price
    or cheaper) -- the same cumulative curves the market clears on.
    """
    path = folder / "bids.csv"
    if not path.exists():
        return None, None
    bids = pd.read_csv(path)
    levels = int(round(bids.price.max() / PRICE_STEP)) + 1
    column = {t: k for k, t in enumerate(timesteps)}
    curves = {}
    for name in participants:
        raw = np.zeros((levels, len(timesteps)))
        own = bids[bids.participant == name]
        rows = np.rint(own.price.to_numpy() / PRICE_STEP).astype(int)
        cols = own.timestep.map(column).to_numpy()
        np.add.at(raw, (rows, cols), own.quantity_kwh.to_numpy())
        buy = np.cumsum(np.clip(raw, 0, None)[::-1], axis=0)[::-1]
        sell = np.cumsum(np.clip(-raw, 0, None), axis=0)
        curves[name] = buy - sell
    return curves, np.arange(levels) * PRICE_STEP


def capacities(folder, dataset):
    """Battery capacity (kWh) per building, or {} when the dataset is not found."""
    path = Path(dataset) if dataset else Path("generated/altered_stock") / folder.parent.name
    path = path / "buildings.csv"
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    return dict(zip(frame.building, frame.battery_kwh))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", default="results/altered_stock/balanced/competitive")
    parser.add_argument("--dataset", help="generated dataset folder holding buildings.csv; "
                        "default generated/altered_stock/<configuration of the run>")
    parser.add_argument("--plotlyjs", choices=["inline", "cdn"], default="inline",
                        help="inline (default) works offline; cdn loads plotly from the web "
                             "and makes the page about 4.5 MB smaller, for sharing")
    parser.add_argument("--html", help="where to write the HTML; default <run>/hourly_trades.html")
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
    market["pct_of_grid"] = np.where(traded,
        100 * market.clearing_price / market.grid_import_price, np.nan)
    market["discount_pct"] = 100 - market.pct_of_grid
    labels = ["Day %d, %02d:00" % (d + 1, h) for d, h in zip(market.day, market.hour)]
    details = sellers.groupby("timestep")[["participant", "sold_kwh"]].apply(
        lambda g: "<br>".join("%s: %.2f kWh" % (short(r.participant), r.sold_kwh)
                              for r in g.itertuples()))
    breakdown = market.timestep.map(details).fillna("No sales")
    custom = np.column_stack([labels, breakdown])
    market.to_csv(folder / "hourly_trades.csv", index=False)

    # Per-participant hourly position, aligned to the market's timesteps.
    position = (ledger.pivot_table(index="timestep", columns="participant",
                                   values="p2p_kwh", aggfunc="sum")
                .reindex(market.timestep).fillna(0.0))
    imports = (ledger.pivot_table(index="timestep", columns="participant",
                                  values="grid_import_kwh", aggfunc="sum")
               .reindex(market.timestep).fillna(0.0))
    options, houses, players, label_of = selections(folder, ledger)
    first = len(options) - len(ledger.participant.unique()) if players else 0
    soc, capacity = None, capacities(folder, args.dataset)
    path = folder / "buildings_hourly.csv"
    if path.exists():
        soc = (pd.read_csv(path, usecols=["timestep", "building", "battery_soc_kwh"])
               .pivot_table(index="timestep", columns="building", values="battery_soc_kwh")
               .reindex(market.timestep))
    peak = (market.grid_import_price >= PEAK_PRICE).to_numpy()
    curves, bid_prices = bid_curves(folder, market.timestep.tolist(),
                                    ledger.participant.unique())
    if curves is not None:
        # Price levels above an hour's grid ceiling do not exist: leave blank.
        above = bid_prices[:, None] > market.grid_import_price.to_numpy()[None, :] + 1e-9

    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    fig = make_subplots(rows=7, cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        row_heights=[0.13, 0.12, 0.10, 0.17, 0.13, 0.19, 0.16],
                        subplot_titles=("Energy sold to peers, whole market",
                                        "Grid price and clearing price",
                                        "Clearing price as % of grid price",
                                        "Selection: bought from peers (up) and sold (down)",
                                        "Selection: battery state of charge",
                                        "Selection: bids \u2014 red = would buy at this price, "
                                        "green = would sell, white = no trade",
                                        "Selection: grid import, coloured by grid price"))
    player_sold = {p: (-position[p].clip(upper=0)).to_numpy() for p in players}
    others = market.sold_kwh.to_numpy() - sum(player_sold.values(), np.zeros(len(market)))
    custom = np.column_stack([labels, breakdown, market.sold_kwh])
    fig.add_trace(go.Bar(x=market.timestep, y=others,
        name="Energy sold to peers" + (" by everyone else" if players else ""),
        marker_color="#287c9b", customdata=custom,
        hovertemplate=("%{customdata[0]}<br>Sold, all sellers: %{customdata[2]:.2f} kWh"
                       "<br>%{customdata[1]}<extra></extra>")), row=1, col=1)
    for k, p in enumerate(players):
        fig.add_trace(go.Bar(x=market.timestep, y=player_sold[p],
            name="★ sold by %s" % p, marker_color=PLAYER_COLORS[k % len(PLAYER_COLORS)],
            customdata=labels, hovertemplate=("%{customdata}<br>★ " + p +
                                              ": %{y:.2f} kWh<extra></extra>")), row=1, col=1)
    fig.add_trace(go.Scatter(x=market.timestep, y=market.volume_kwh,
        name="Gross auction volume (before participant netting)", visible="legendonly",
        mode="lines", line=dict(color="#939393", dash="dot"),
        hovertemplate="Gross auction: %{y:.2f} kWh<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Scatter(x=market.timestep, y=market.grid_import_price,
        name="Grid price / price ceiling", mode="lines", line=dict(color="#c17b17", width=2),
        customdata=labels, hovertemplate="%{customdata}<br>Grid: $%{y:.4f}/kWh<extra></extra>"), row=2, col=1)
    fig.add_trace(go.Scatter(x=market.timestep, y=market.clearing_price.where(traded),
        name="Trade clearing price", mode="markers", marker=dict(color="#136e58", size=5),
        customdata=np.column_stack([labels, market.pct_of_grid]),
        hovertemplate="%{customdata[0]}<br>Trade: $%{y:.4f}/kWh<br>= %{customdata[1]:.1f}% of grid<extra></extra>"), row=2, col=1)
    fig.add_trace(go.Scatter(x=market.timestep, y=market.pct_of_grid,
        name="Clearing price, % of grid price", mode="markers",
        marker=dict(color="#6a4c93", size=5),
        customdata=np.column_stack([labels, market.clearing_price, market.grid_import_price]),
        hovertemplate=("%{customdata[0]}<br>%{y:.1f}% of grid<br>"
                       "Trade $%{customdata[1]:.4f} / grid $%{customdata[2]:.4f}<extra></extra>")),
        row=3, col=1)
    for k, p in enumerate(players):
        when = player_sold[p] > 1e-6
        color = PLAYER_COLORS[k % len(PLAYER_COLORS)]
        for row, values in ((2, market.clearing_price), (3, market.pct_of_grid)):
            fig.add_trace(go.Scatter(x=market.timestep[when], y=values[when], mode="markers",
                name="★ hours %s sold" % p, legendgroup="ring-" + p, showlegend=(row == 2),
                marker=dict(symbol="circle-open", size=11, color=color, line=dict(width=2)),
                customdata=np.column_stack([np.array(labels)[when], player_sold[p][when],
                                            market.pct_of_grid[when]]),
                hovertemplate=("%{customdata[0]}<br>★ " + p + " sold %{customdata[1]:.2f} kWh"
                               "<br>clearing at %{customdata[2]:.0f}% of grid<extra></extra>")),
                row=row, col=1)
    if curves is not None:
        # Orientation for the bid heatmap: the ceiling and where the market cleared.
        fig.add_trace(go.Scatter(x=market.timestep, y=market.grid_import_price, mode="lines",
            name="Grid price (bid ceiling)", showlegend=False,
            line=dict(color="#c17b17", width=1.2), hoverinfo="skip"), row=6, col=1)
        fig.add_trace(go.Scatter(x=market.timestep, y=market.clearing_price.where(traded),
            mode="markers", name="Clearing price", showlegend=False,
            marker=dict(symbol="x-thin", size=6, line=dict(width=1.5, color="#222")),
            hoverinfo="skip"), row=6, col=1)
    fixed = len(fig.data)

    # Five traces per selector option (bought, sold, SOC, bids, grid import);
    # one set visible.
    per_option = 5
    bottom = fig.layout.yaxis7.domain
    heat = fig.layout.yaxis6.domain
    prices = market.grid_import_price
    for i, (label, members) in enumerate(options):
        net = position[members]
        bought = net.clip(lower=0).sum(axis=1).to_numpy()
        sold_by = net.clip(upper=0).sum(axis=1).to_numpy()
        hover = np.column_stack([labels, market.clearing_price.fillna(np.nan)])
        for values, name, color in ((bought, "Bought from peers", BUY_COLOR),
                                    (sold_by, "Sold to peers", SELL_COLOR)):
            fig.add_trace(go.Bar(x=market.timestep, y=values, name=name,
                marker_color=color, visible=(i == first), legendgroup=name,
                showlegend=(i == first), customdata=hover,
                hovertemplate=("%{customdata[0]}<br>" + label + "<br>" + name +
                               ": %{y:.2f} kWh<br>Clearing: $%{customdata[1]:.4f}/kWh"
                               "<extra></extra>")), row=4, col=1)

        buildings = [b for m in members for b in houses.get(m, [m])]
        stored = (soc[[b for b in buildings if b in soc.columns]].sum(axis=1, min_count=1)
                  if soc is not None else pd.Series(np.nan, index=market.timestep))
        total_kwh = sum(capacity.get(b, 0.0) for b in buildings)
        if total_kwh > 0:
            level, unit = 100 * stored / total_kwh, "%"
        else:
            level, unit = stored, " kWh"
        fig.add_trace(go.Scatter(x=market.timestep, y=level, mode="lines",
            name="Battery state of charge" + (" (%.0f kWh capacity)" % total_kwh if total_kwh else ""),
            line=dict(color=SOC_COLOR, width=1.6), visible=(i == first), showlegend=(i == first),
            customdata=np.column_stack([labels, stored]),
            hovertemplate=("%{customdata[0]}<br>" + label + "<br>SOC: %{y:.1f}" + unit +
                           " (%{customdata[1]:.1f} kWh)<extra></extra>")), row=5, col=1)

        if curves is not None:
            z = sum(curves[m] for m in members if m in curves)
            z = np.where(above, np.nan, z).astype(np.float32)   # halves the file
            nonzero = np.abs(z[np.isfinite(z) & (np.abs(z) > 1e-6)])
            top = float(np.percentile(nonzero, 98)) if nonzero.size else 1.0
            fig.add_trace(go.Heatmap(x=market.timestep, y=bid_prices, z=z,
                zmin=-top, zmax=top, zmid=0, colorscale=BID_SCALE, hoverongaps=False,
                visible=(i == first), showscale=True, name="Bids",
                colorbar=dict(title=dict(text="kWh: sell (-) / buy (+)", side="right"),
                              thickness=12, len=heat[1] - heat[0], y=heat[0],
                              yanchor="bottom", x=1.01),
                hovertemplate=("hour %{x}, $%{y:.2f}/kWh<br>" + label +
                               "<br>%{z:+.1f} kWh  (+ would buy, - would sell)"
                               "<extra></extra>")), row=6, col=1)
        else:
            fig.add_trace(go.Scatter(x=[], y=[], visible=(i == first), showlegend=False),
                          row=6, col=1)

        drawn = imports[members].sum(axis=1).to_numpy()
        share = 100 * drawn[peak].sum() / drawn.sum() if drawn.sum() > 0 else 0.0
        fig.add_trace(go.Bar(x=market.timestep, y=drawn, visible=(i == first), showlegend=(i == first),
            name="Grid import: %.0f kWh, %.0f%% of it at peak prices (>= $%.2f)"
                 % (drawn.sum(), share, PEAK_PRICE),
            marker=dict(color=prices, colorscale="YlOrRd", cmin=prices.min(), cmax=prices.max(),
                        line=dict(width=0),
                        colorbar=dict(title=dict(text="Grid $/kWh", side="right"), thickness=12,
                                      len=bottom[1] - bottom[0], y=bottom[0], yanchor="bottom",
                                      x=1.01)),
            customdata=np.column_stack([labels, prices]),
            hovertemplate=("%{customdata[0]}<br>" + label + "<br>Grid import: %{y:.2f} kWh"
                           " at $%{customdata[1]:.4f}/kWh<extra></extra>")), row=7, col=1)

    def visibility(chosen):
        base = [trace.visible for trace in fig.data[:fixed]]
        return base + [j == chosen for j in range(len(options)) for _ in range(per_option)]

    def legend(chosen):
        return [trace.showlegend for trace in fig.data[:fixed]] + \
               [j == chosen for j in range(len(options)) for _ in range(per_option)]

    end = int(market.timestep.max()) + 0.5
    choices = [("Full run", [-0.5, end]), ("First week", [-0.5, min(end, 167.5)])]
    choices += [("Day %d" % (d + 1), [d * 24 - 0.5, min(end, (d + 1) * 24 - 0.5)])
                for d in sorted(market.day.unique())]
    axes = ["xaxis.range"] + ["xaxis%d.range" % k for k in range(2, 8)]
    days, hours = market.day.nunique(), len(market)
    fig.update_layout(template="plotly_white", height=2300, barmode="relative",
        title=dict(text="Hourly peer-to-peer trades and prices<br><sup>%s · %d participants · "
                        "%d hourly steps over %d days · click a legend item to toggle</sup>"
                        % (folder.as_posix(), ledger.participant.nunique(), hours, days), x=0.04),
        hovermode="x unified", bargap=0.08,
        legend=dict(orientation="h", y=-0.04, x=0),
        margin=dict(l=75, r=90, t=130, b=130),
        updatemenus=[
            dict(buttons=[dict(label=label, method="relayout",
                 args=[{a: bounds for a in axes}]) for label, bounds in choices],
                 x=1, y=1.07, xanchor="right", yanchor="top"),
            dict(buttons=[dict(label=label, method="restyle",
                 args=[{"visible": visibility(i), "showlegend": legend(i)}])
                 for i, (label, _) in enumerate(options)],
                 active=first, x=0, y=0, xanchor="left", yanchor="bottom", direction="down",
                 pad=dict(t=0, b=4))])
    # Sit the building selector just above the bottom panel, left of its title.
    fig.layout.updatemenus[1].y = fig.layout.yaxis4.domain[1]
    ticks = np.arange(0, hours, 24)
    fig.update_xaxes(tickvals=ticks, ticktext=["Day %d" % (t // 24 + 1) for t in ticks],
                     range=[-0.5, end])
    fig.update_xaxes(title_text="Simulation time (select a day above, or drag to zoom)", row=7, col=1)
    fig.update_yaxes(title_text="Sold (kWh/h)", rangemode="tozero", row=1, col=1)
    fig.update_yaxes(title_text="Price ($/kWh)", rangemode="tozero", row=2, col=1)
    fig.update_yaxes(title_text="% of grid", range=[0, 105], row=3, col=1)
    fig.update_yaxes(title_text="kWh/h  (+ bought, − sold)", zeroline=True,
                     zerolinecolor="#555", row=4, col=1)
    has_pct = soc is not None and bool(capacity)
    fig.update_yaxes(title_text="SOC (% of capacity)" if has_pct else "SOC (kWh)",
                     rangemode="tozero", row=5, col=1)
    if has_pct:
        fig.update_yaxes(range=[0, 102], row=5, col=1)
    fig.update_yaxes(title_text="Bid price ($/kWh)", row=6, col=1,
                     range=[0, float(market.grid_import_price.max()) + PRICE_STEP])
    fig.update_yaxes(title_text="Grid import (kWh/h)", rangemode="tozero", row=7, col=1)
    fig.add_hline(y=100, line=dict(color="#c17b17", dash="dot", width=1), row=3, col=1)

    total = market.sold_kwh.sum()
    average = (market.sold_kwh * market.clearing_price.fillna(0)).sum() / total if total else 0
    grid_average = (market.sold_kwh * market.grid_import_price).sum() / total if total else 0
    pct = 100 * average / grid_average if grid_average else float("nan")
    subtitle = ("<p><b>%.1f kWh sold across %d trading hours.</b> "
                "Volume-weighted trade price: $%.3f/kWh, which is %.1f%% of the grid price "
                "in those same hours, weighted by traded volume ($%.3f/kWh).</p>" %
                (total, traded.sum(), average, pct, grid_average))
    if players:
        subtitle += ("<p style=\"border-left:4px solid %s;padding-left:12px\"><b>Highlighted:</b> %s "
                     "%s its own agent; every other house runs the default agent. "
                     "The selector below opens on it.</p>" % (
                         PLAYER_COLORS[0], ", ".join(label_of(p) for p in players),
                         "runs" if len(players) == 1 else "each run"))
    groups = [label for label, _ in options[1:] if label.split(" ")[0] in ROLE_GROUPS.values()]
    note = ("<p>Top panel counts sellers' net settled sales once, not both buyer and seller. "
            "All trades within an hour receive the same clearing price; missing price markers "
            "mean no trade. Gross auction volume is available in the legend. Sales include "
            "grid-backed deliveries, not only household surplus.</p>"
            "<p>Bottom four panels: choose the whole market, %s or a single participant from "
            "the menu above them. Bars show settled peer-to-peer trades: up for energy bought, down "
            "for energy sold. Battery state of charge is after each hour's step; for a group it is "
            "the group's stored energy over its total capacity. The bid heatmap shows, per hour "
            "and price, how much the selection would buy (red) or sell (green) at that price: "
            "a buy bid counts at its own price and every cheaper one, an ask at its own price "
            "and every dearer one, exactly as the market adds them up; a group sums its "
            "members. Above the orange line is blank because no bid can exceed the grid price; "
            "crosses mark the clearing price; colours saturate at the 98th percentile. "
            "Grid imports are coloured by that "
            "hour's grid price, and the legend gives the share imported at peak prices "
            "(&ge; $%.2f/kWh, the default agent's own peak threshold). Trades and imports are "
            "settled per participant, so a participant that owns several houses is shown as one "
            "entry; a group sums the participants whose houses all have that role.%s</p>" %
            (" or ".join(groups) if groups else "a participant", PEAK_PRICE,
             "" if groups else " This run has no generation/storage roles, so no group aggregates."
             if soc is not None else " This run has no generation/storage roles and records no "
             "per-house battery state, so no group aggregates and no SOC."))
    plot = fig.to_html(full_html=False, include_plotlyjs=True if args.plotlyjs == "inline" else "cdn",
                       config={"displaylogo": False, "responsive": True,
                               "toImageButtonOptions": {"filename": "hourly_trades", "scale": 2}})
    html_path = Path(args.html) if args.html else folder / "hourly_trades.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(
        '<!doctype html><html><head><meta charset="utf-8"><title>Hourly electricity trades</title>'
        '<style>body{font:16px system-ui;margin:24px auto;max-width:1400px;color:#23303a}'
        'p{padding:0 24px;line-height:1.5}</style></head><body>' + subtitle + plot + note +
        '</body></html>', encoding="utf-8")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 1, figsize=(15, 9.5), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1, 0.8]})
    x = 1 + market.timestep / 24
    axes[0].bar(x, market.sold_kwh, width=0.037, color="#287c9b")
    axes[0].set_ylabel("Sold (kWh per hour)")
    axes[0].set_title("Hourly peer-to-peer trades — %s" % folder.as_posix(), loc="left", fontsize=15)
    axes[1].plot(x, market.grid_import_price, color="#c17b17", linewidth=1.2,
                 label="Grid price / ceiling")
    axes[1].scatter(x[traded], market.loc[traded, "clearing_price"], color="#136e58",
                    s=9, label="Trade clearing price", zorder=3)
    axes[1].set_ylabel("Price ($/kWh)")
    axes[1].legend(loc="upper right", ncol=2)
    axes[2].scatter(x[traded], market.loc[traded, "pct_of_grid"], color="#6a4c93", s=9)
    axes[2].axhline(100, color="#c17b17", linestyle=":", linewidth=1)
    axes[2].set_ylim(0, 105)
    axes[2].set_ylabel("Clearing, % of grid")
    axes[2].set_xlabel("Simulation day; each bar/marker represents one hour")
    axes[2].set_xticks(np.arange(1, days + 2))
    for ax in axes:
        ax.set_ylim(bottom=0)
        ax.set_xlim(0.7, days + 1)
        ax.grid(axis="y", alpha=0.2)
        ax.spines[["top", "right"]].set_visible(False)
    fig.text(0.065, 0.012, "Net settled sales; missing price markers = no trade. "
             "Open the HTML for hourly hover details, day selection and per-building trades.",
             fontsize=9)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(folder / "hourly_trades.png", dpi=160)
    plt.close(fig)
    print("Sold %.3f kWh in %d hours; weighted clearing $%.4f (%.1f%% of weighted grid $%.4f)" %
          (total, traded.sum(), average, pct, grid_average))
    shown = ", ".join(label for label, _ in options[:4 + len(players)])
    print("Selector:", shown.encode("ascii", "replace").decode(), "... (%d entries)" % len(options))
    print("Saved", html_path)


if __name__ == "__main__":
    main()
