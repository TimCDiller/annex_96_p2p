"""Create an offline interactive report from saved altered-stock experiments."""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="results/altered_stock")
    parser.add_argument("--dataset", default="generated/altered_stock")
    args = parser.parse_args()
    out, dataset = Path(args.out), Path(args.dataset)
    summary = pd.read_csv(out / "comparison.csv")
    try:
        effects = pd.read_csv(out / "strategic_effects.csv")
    except pd.errors.EmptyDataError:
        effects = pd.DataFrame()
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    first_config = summary.configuration.iloc[0]
    tariff = pd.read_csv(dataset / first_config / "duck_prices.csv").electricity_pricing
    duck = go.Figure(go.Scatter(x=list(range(24)), y=tariff.iloc[:24], mode="lines+markers",
        line=dict(color="#be7a19"), hovertemplate="%{x}:00 · $%{y:.3f}/kWh<extra></extra>"))
    duck.update_layout(title="Synthetic retail tariff: morning and evening peaks", template="plotly_white",
        height=310, xaxis_title="Hour", yaxis_title="$/kWh", xaxis=dict(dtick=1))

    hourly = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08)
    cases = []
    for row in summary.itertuples():
        folder = out / row.configuration / row.mode
        data = pd.read_csv(folder / "hourly_audit.csv")
        label = row.configuration + " / " + row.mode
        cases.append(label)
        visible = len(cases) == 1
        custom = ["Day %d, %02d:00" % (d+1,h) for d,h in zip(data.day,data.hour)]
        for y, name, color in ((data.backed_kwh,"Surplus-backed sales","#14816b"),
                              (data.sold_kwh-data.backed_kwh,"Grid-backed sales","#a9b3bd")):
            hourly.add_trace(go.Bar(x=data.timestep,y=y,name=name,marker_color=color,
                customdata=custom,visible=visible,
                hovertemplate="%{customdata}<br>%{y:.2f} kWh<extra>%{fullData.name}</extra>"),row=1,col=1)
        hourly.add_trace(go.Scatter(x=data.timestep,y=data.clearing_price.where(data.sold_kwh>1e-6),
            mode="markers",name="Clearing price",marker=dict(color="#287c9b",size=5),visible=visible,
            customdata=custom,hovertemplate="%{customdata}<br>$%{y:.3f}/kWh<extra></extra>"),row=2,col=1)
        hourly.add_trace(go.Scatter(x=data.timestep,y=data.grid_import_price,mode="lines",name="Grid ceiling",
            line=dict(color="#be7a19"),visible=visible,customdata=custom,
            hovertemplate="%{customdata}<br>$%{y:.3f}/kWh<extra></extra>"),row=2,col=1)
    buttons = []
    for i, label in enumerate(cases):
        buttons.append(dict(label=label,method="update",args=[
            {"visible":[j//4 == i for j in range(len(cases)*4)]},
            {"title.text":"Hourly trades: " + label}]))
    hourly.update_layout(template="plotly_white",height=740,barmode="stack",hovermode="x unified",
        title="Hourly trades: " + cases[0], margin=dict(t=130,b=80),
        updatemenus=[dict(buttons=buttons,x=1,y=1.16,xanchor="right")],
        legend=dict(orientation="h",y=-0.18))
    hourly.update_xaxes(range=[-0.5,167.5], title_text="Simulation hour (drag to zoom; double-click to reset)",row=2,col=1)
    hourly.update_yaxes(title_text="kWh sold",rangemode="tozero",row=1,col=1)
    hourly.update_yaxes(title_text="$/kWh",rangemode="tozero",row=2,col=1)
    table = summary[["configuration","mode","net_sold_kwh","backed_kwh","weighted_price",
        "days_with_trade","total_bill","curtailed_kwh","comfortable_pct"]].round(3)
    blocks = ["<h1>Altered-stock classroom market</h1>",
        "<p>20 homes: ten PV-rich, ten storage-rich. Six-house dominant owners control exactly 30% of homes. "
        "Same weather, thermal control and prices in matched strategic comparisons.</p>",
        "<h2>Equipment and tariff</h2>",
        "<p>PV-rich: 40 or 60 kW PV, 4 kWh / 1 kW battery. Storage-rich: 1 kW PV, 80 kWh battery; "
        "five at 80 kW (1C), five at 20 kW (0.25C). Batteries start empty, with 90% round-trip efficiency. "
        "These are synthetic community-sized assets; roof and feeder limits are not modelled.</p>",
        duck.to_html(full_html=False,include_plotlyjs=True,config={"displaylogo":False}),
        "<h2>Measured outcomes</h2>",table.to_html(index=False,border=0),
        "<p>Volume is net settled sales counted once. Surplus-backed energy requires actual portfolio export; "
        "the rest of a sale is covered by grid purchases. Bills include discomfort.</p>",
        "<p>Comfort remains a separate teaching objective: the comfortable_pct column counts building-hours "
        "inside the comfort band. More trading does not establish adequate thermal control. "
        "Compare students on common portfolios or rotate their assignments; raw bills across different "
        "building types are not directly comparable.</p>",
        "<h2>Did strategic behavior pay?</h2>",
        effects.round(3).to_html(index=False,border=0) if not effects.empty else "<p>No matched strategic runs yet.</p>",
        "<p>Positive owner_benefit means the strategy reduced the dominant owner's bill relative to an "
        "honest owner of exactly the same six houses. Positive others_extra_cost means the remaining "
        "participants paid more. Weighted price changes also reflect changes in trading hours and volume.</p>",
        "<h2>Inspect individual hours</h2><p>Select a configuration and strategy. The initial view shows the first week.</p>",
        hourly.to_html(full_html=False,include_plotlyjs=False,config={"displaylogo":False}),
        "<p>The first 13 hours include LSTM warmup. The full run has 743 transitions, so day 31 has 23 hours. "
        "No terminal battery credit or wear cost is applied; final SOC is in the CSV summaries. "
        "This is a winter teaching experiment, not a validated network planning model.</p>"]
    (out / "report.html").write_text('<!doctype html><html><head><meta charset="utf-8">'
        '<title>Altered-stock experiment</title><style>body{max-width:1400px;margin:30px auto;font:16px system-ui;'
        'color:#23303a;padding:0 20px}p{line-height:1.6}table{border-collapse:collapse;font-size:13px;width:100%}'
        'td,th{padding:9px;text-align:right;border-bottom:1px solid #ddd}th{background:#f0f4f6}'
        'h2{margin-top:36px}</style></head><body>' + "\n".join(blocks) + '</body></html>',encoding="utf-8")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig,axes = plt.subplots(2,2,figsize=(14,9))
    axes[0,0].plot(range(24),tariff.iloc[:24],color="#be7a19",marker=".")
    axes[0,0].set(title="Duck-shaped retail price (day 1)",xlabel="Hour",ylabel="$/kWh")
    axes[0,0].set_xticks(range(0,24,2))
    competitive = summary[summary["mode"]=="competitive"]
    x = np.arange(len(competitive))
    axes[0,1].bar(x-0.18,competitive.net_sold_kwh/1000,width=.36,label="Net sales",color="#287c9b")
    axes[0,1].bar(x+0.18,competitive.backed_kwh/1000,width=.36,label="Surplus-backed",color="#14816b")
    axes[0,1].set_xticks(x,competitive.configuration)
    axes[0,1].set(title="Competitive markets",ylabel="MWh over the run")
    axes[0,1].legend()
    if not effects.empty:
        labels = [r.configuration+"\n"+r.strategy.replace("_"," ") for r in effects.itertuples()]
        axes[1,0].bar(range(len(effects)),effects.owner_benefit,color=["#14816b" if v>0 else "#c75b4b" for v in effects.owner_benefit])
        axes[1,0].set_xticks(range(len(effects)),labels,rotation=25,ha="right",fontsize=8)
        axes[1,0].axhline(0,color="black",linewidth=.8)
    axes[1,0].set(title="Strategic owner's benefit vs matched honest owner",ylabel="$ saved (negative = loss)")
    for cfg in competitive.configuration:
        data = pd.read_csv(out/cfg/"competitive"/"buildings_hourly.csv")
        # Summed across houses, averaged across days for each hour.
        daily = data.groupby(["timestep","hour"]).battery_kwh.sum().reset_index()
        axes[1,1].plot(daily.groupby("hour").battery_kwh.mean(),label=cfg,marker=".")
    axes[1,1].axhline(0,color="black",linewidth=.8)
    axes[1,1].set(title="Mean aggregate battery operation",xlabel="Hour",ylabel="kWh/hour (+ charge, - discharge)")
    axes[1,1].legend()
    for ax in axes.flat:
        ax.grid(axis="y",alpha=.2)
    fig.tight_layout()
    fig.savefig(out/"overview.png",dpi=150)
    plt.close(fig)
    print("Wrote",out/"report.html", "and",out/"overview.png")


if __name__ == "__main__":
    main()
