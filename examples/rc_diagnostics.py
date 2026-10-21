"""Plot house temperatures and measure how RC preheating changed trading."""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

EVALUATION_START = 168
PEAK = (6, 7, 8, 16, 17, 18, 19, 20)
PRE_PEAK = (4, 5, 13, 14, 15)
COLORS = {"thermostat":"#287c9b", "Thermostat":"#287c9b",
          "RC preheat":"#c55d3d"}


def load_case(root, config, mode):
    folder = Path(root)/config/mode
    return (pd.read_csv(folder/"buildings_hourly.csv"),
            pd.read_csv(folder/"hourly_audit.csv"))


def comfort_bands(dataset, frame):
    """The outcome of transition t uses the setpoint at t+1."""
    pieces = []
    for name, group in frame.groupby("building", sort=False):
        source = pd.read_csv(Path(dataset)/(name+".csv"),
            usecols=["indoor_dry_bulb_temperature_cooling_set_point"])
        setpoint = source.iloc[group.timestep.to_numpy()+1, 0].to_numpy()
        rows = group[["building","timestep","indoor_temperature","degree_hours"]].copy()
        rows["comfort_low"] = setpoint-2.0
        rows["comfort_high"] = setpoint+2.0
        calculated = np.maximum(0, np.maximum(rows.comfort_low-rows.indoor_temperature,
                                              rows.indoor_temperature-rows.comfort_high))
        if np.max(np.abs(calculated-rows.degree_hours)) > 1e-3:
            raise ValueError("Comfort-band reconstruction does not match settlement for "+name)
        pieces.append(rows[["building","timestep","comfort_low","comfort_high"]])
    return pd.concat(pieces,ignore_index=True)


def house_effects(config, baseline, rc):
    rows = []
    for name in sorted(set(baseline.building)):
        a = baseline[(baseline.building==name)&(baseline.timestep>=EVALUATION_START)]
        b = rc[(rc.building==name)&(rc.timestep>=EVALUATION_START)]
        rows.append(dict(configuration=config, building=name, role=a.role.iloc[0],
            comfortable_hours_baseline=int((a.degree_hours<=1e-9).sum()),
            comfortable_hours_rc=int((b.degree_hours<=1e-9).sum()),
            comfort_hours_gained=int((b.degree_hours<=1e-9).sum()-(a.degree_hours<=1e-9).sum()),
            degree_hours_baseline=a.degree_hours.sum(), degree_hours_rc=b.degree_hours.sum(),
            rc_prediction_mae_c=np.abs(b.predicted_next_c-b.indoor_temperature).mean(),
            peak_hvac_delta_kwh=b[b.hour.isin(PEAK)].hvac_kwh.sum()-a[a.hour.isin(PEAK)].hvac_kwh.sum(),
            pre_peak_hvac_delta_kwh=b[b.hour.isin(PRE_PEAK)].hvac_kwh.sum()-a[a.hour.isin(PRE_PEAK)].hvac_kwh.sum()))
    return pd.DataFrame(rows)


def market_effects(config, baseline, rc):
    rows = []
    for label, data in (("thermostat",baseline),("RC preheat",rc)):
        data = data[data.timestep>=EVALUATION_START].copy()
        data["grid_backed_kwh"] = data.sold_kwh-data.backed_kwh
        for hour, group in data.groupby("hour"):
            sold = group.sold_kwh.sum()
            rows.append(dict(configuration=config, controller=label, hour=hour,
                transitions=len(group), sold_kwh=sold, backed_kwh=group.backed_kwh.sum(),
                grid_backed_kwh=group.grid_backed_kwh.sum(),
                mean_sold_kwh=group.sold_kwh.mean(),
                trading_hours=int((group.sold_kwh>1e-9).sum()),
                weighted_clearing_price=(group.sold_kwh*group.clearing_price.fillna(0)).sum()/sold
                  if sold else np.nan,
                mean_grid_price=group.grid_import_price.mean()))
    return pd.DataFrame(rows)


def plot_temperatures(out, config, dataset, baseline, rc, market_base, market_rc, effects):
    bands = comfort_bands(dataset, baseline)
    baseline = baseline.merge(bands,on=["building","timestep"],validate="one_to_one")
    rc = rc.merge(bands,on=["building","timestep"],validate="one_to_one")
    examples = effects[(effects.peak_hvac_delta_kwh < 0) &
                       (effects.pre_peak_hvac_delta_kwh > 0)]
    candidates = examples if not examples.empty else effects
    exemplar = candidates.sort_values("comfort_hours_gained",ascending=False).building.iloc[0]
    names = [exemplar] + [n for n in effects.sort_values(
        "comfort_hours_gained",ascending=False).building if n != exemplar]
    figure = make_subplots(rows=3,cols=1,shared_xaxes=True,
        row_heights=[.52,.23,.25],vertical_spacing=.055,
        specs=[[{}],[{"secondary_y":True}],[{"secondary_y":True}]])
    common = 0
    for label, data, color in (("Thermostat trades",market_base,COLORS["thermostat"]),
                               ("RC trades",market_rc,COLORS["RC preheat"])):
        figure.add_trace(go.Bar(x=data.timestep,y=data.sold_kwh,name=label,
            marker_color=color,opacity=.65,
            hovertemplate="Transition %{x}<br>%{y:.1f} kWh sold<extra>%{fullData.name}</extra>"),
            row=3,col=1)
        common += 1
    for label,data,color in (("Thermostat clearing",market_base,COLORS["thermostat"]),
                             ("RC clearing",market_rc,COLORS["RC preheat"])):
        figure.add_trace(go.Scatter(x=data.timestep,
            y=data.clearing_price.where(data.sold_kwh>1e-9),mode="markers",
            marker=dict(size=4,color=color),name=label,
            hovertemplate="Transition %{x}<br>$%{y:.3f}/kWh<extra>%{fullData.name}</extra>"),
            row=3,col=1,secondary_y=True)
        common += 1
    for row in (2,3):
        figure.add_trace(go.Scatter(x=market_base.timestep,y=market_base.grid_import_price,
            name="Grid price" if row==2 else "Grid price (market)",mode="lines",
            line=dict(color="#b68b29",width=1.5,dash="dash"),
            showlegend=row==2,
            hovertemplate="Transition %{x}<br>$%{y:.3f}/kWh<extra>Grid price</extra>"),
            row=row,col=1,secondary_y=True)
        common += 1
    for i,name in enumerate(names):
        a = baseline[baseline.building==name].sort_values("timestep")
        b = rc[rc.building==name].sort_values("timestep")
        visible = i==0
        x = a.timestep+1
        figure.add_trace(go.Scatter(x=x,y=a.comfort_low,name="Comfort lower edge",
            mode="lines",line=dict(color="#819187",width=1,dash="dot"),visible=visible,
            hovertemplate="After transition %{x}<br>%{y:.1f} °C<extra>Lower edge</extra>"),row=1,col=1)
        figure.add_trace(go.Scatter(x=x,y=a.comfort_high,name="Comfort upper edge",
            mode="lines",line=dict(color="#819187",width=1,dash="dot"),
            fill="tonexty",fillcolor="rgba(135,177,145,0.13)",visible=visible,
            hovertemplate="After transition %{x}<br>%{y:.1f} °C<extra>Upper edge</extra>"),row=1,col=1)
        for label,data in (("Thermostat",a),("RC preheat",b)):
            figure.add_trace(go.Scatter(x=data.timestep+1,y=data.indoor_temperature,
                name=label+" temperature",mode="lines",
                line=dict(color=COLORS[label],width=1.7),visible=visible,
                hovertemplate="After transition %{x}<br>%{y:.2f} °C<extra>%{fullData.name}</extra>"),
                row=1,col=1)
        figure.add_trace(go.Scatter(x=b.timestep+1,y=b.predicted_next_c,
            name="Raw RC prediction",mode="lines",
            line=dict(color="#8758a7",width=1,dash="dot"),visible=visible,
            hovertemplate="After transition %{x}<br>%{y:.2f} °C<extra>Raw RC prediction</extra>"),
            row=1,col=1)
        for label,data in (("Thermostat",a),("RC preheat",b)):
            figure.add_trace(go.Scatter(x=data.timestep,y=data.hvac_kwh,
                name=label+" heating",mode="lines",
                line=dict(color=COLORS[label],width=1.3),visible=visible,
                hovertemplate="Transition %{x}<br>%{y:.2f} kWh<extra>%{fullData.name}</extra>"),
                row=2,col=1)
    buttons = []
    for i,name in enumerate(names):
        role = effects.set_index("building").loc[name,"role"]
        gained = effects.set_index("building").loc[name,"comfort_hours_gained"]
        buttons.append(dict(label="%s (%s, %+d comfort hours)" %
                            (name.rsplit("-",1)[-1],role,gained),method="update",args=[
            {"visible":[True]*common+[j//7==i for j in range(len(names)*7)]},
            {"title.text":"%s: house %s" % (config.replace("_"," ").title(),name.rsplit("-",1)[-1])}]))
    figure.update_layout(template="plotly_white",height=1000,barmode="overlay",
        title="%s: house %s" % (config.replace("_"," ").title(),names[0].rsplit("-",1)[-1]),
        updatemenus=[dict(buttons=buttons,x=1,y=1.11,xanchor="right")],
        margin=dict(t=120,b=115,l=75,r=85),hovermode="x unified",
        legend=dict(orientation="h",y=-.10))
    figure.update_xaxes(range=[168,336],title_text="Simulation transition (drag to zoom; double-click to reset)",row=3,col=1)
    figure.update_yaxes(title_text="Indoor temperature (°C)",row=1,col=1)
    figure.update_yaxes(title_text="House heating (kWh)",row=2,col=1,secondary_y=False)
    figure.update_yaxes(title_text="Grid $/kWh",row=2,col=1,secondary_y=True)
    figure.update_yaxes(title_text="Market sales (kWh)",row=3,col=1,secondary_y=False)
    figure.update_yaxes(title_text="Price ($/kWh)",row=3,col=1,secondary_y=True)
    note = ("<p>Select a house, then zoom or pan through all 31 days. The opening view starts "
            "when RC control begins, after seven calibration days. The green band is the actual "
            "comfort band used to score each temperature. The dotted purple line is the raw "
            "one-step RC prediction, absent for thermostat fallback houses. A temperature point is at the end of "
            "its numbered transition; heating, trading, and price are during that transition. "
            "No clearing-price point appears when nothing traded.</p>")
    html = figure.to_html(full_html=False,include_plotlyjs=True,config={"displaylogo":False})
    (out/("temperatures_"+config+".html")).write_text(
        '<!doctype html><html><head><meta charset="utf-8"><title>RC temperatures</title>'
        '<style>body{font:16px system-ui;margin:20px auto;max-width:1500px;padding:0 16px}'
        'p{line-height:1.5}</style></head><body><h1>Building temperatures, preheating and trades</h1>'
        +note+html+'</body></html>',encoding="utf-8")
    if config == "balanced":
        example_temperature(out,baseline[baseline.building==exemplar],
                            rc[rc.building==exemplar],market_base,exemplar)


def example_temperature(out,baseline,rc,market,name):
    """A compact two-day view of a house with measured load shifting."""
    a=baseline[(baseline.timestep>=168)&(baseline.timestep<216)].sort_values("timestep")
    b=rc[(rc.timestep>=168)&(rc.timestep<216)].sort_values("timestep")
    p=market[(market.timestep>=168)&(market.timestep<216)]
    fig,axes=plt.subplots(2,1,figsize=(15,7),sharex=True,
                          gridspec_kw={"height_ratios":[2,1]})
    t=a.timestep.to_numpy()
    axes[0].fill_between(t+1,a.comfort_low.to_numpy(),a.comfort_high.to_numpy(),
                         color="#86b194",alpha=.25,label="Comfort band")
    axes[0].plot(t+1,a.indoor_temperature,color=COLORS["thermostat"],
                 marker=".",label="Thermostat temperature")
    axes[0].plot(t+1,b.indoor_temperature,color=COLORS["RC preheat"],
                 marker=".",label="RC temperature")
    axes[0].set(ylabel="Indoor temperature (°C)",title="House "+name.rsplit("-",1)[-1]+
                ": preheating and comfort, days 8–9")
    axes[0].legend(ncol=3,fontsize=9)
    axes[1].step(t,a.hvac_kwh,where="mid",color=COLORS["thermostat"],
                 label="Thermostat heat")
    axes[1].step(t,b.hvac_kwh,where="mid",color=COLORS["RC preheat"],
                 label="RC heat")
    axes[1].set(ylabel="Heat-pump electricity (kWh)",xlabel="Simulation transition")
    price=axes[1].twinx()
    price.plot(p.timestep,p.grid_import_price,color="#b68b29",linestyle="--",
               alpha=.8,label="Grid price")
    price.set_ylabel("Grid price ($/kWh)")
    for ax in axes:
        for row in p[p.hour.isin(PEAK)].itertuples():
            ax.axvspan(row.timestep-.5,row.timestep+.5,color="#b7c5d2",alpha=.2)
        ax.grid(axis="y",alpha=.2)
    axes[1].legend(loc="upper left",fontsize=9)
    price.legend(loc="upper right",fontsize=9)
    fig.tight_layout()
    fig.savefig(out/"temperature_example.png",dpi=160)
    plt.close(fig)


def plot_trading(out, hourly):
    fig,axes=plt.subplots(2,2,figsize=(15,8),sharex=True)
    for row,config in enumerate(("balanced","solar_abundant")):
        data=hourly[hourly.configuration==config]
        base=data[data.controller=="thermostat"].set_index("hour").reindex(range(24))
        rc=data[data.controller=="RC preheat"].set_index("hour").reindex(range(24))
        x=np.arange(24)
        axes[row,0].bar(x-.19,base.mean_sold_kwh,width=.38,color=COLORS["thermostat"],label="Thermostat")
        axes[row,0].bar(x+.19,rc.mean_sold_kwh,width=.38,color=COLORS["RC preheat"],label="RC preheat")
        axes[row,1].plot(x,base.weighted_clearing_price,marker="o",markersize=5,
                         markerfacecolor="white",linestyle="--",color=COLORS["thermostat"],
                         label="Thermostat clearing")
        axes[row,1].plot(x,rc.weighted_clearing_price,marker=".",color=COLORS["RC preheat"],
                         label="RC clearing")
        axes[row,1].plot(x,base.mean_grid_price,linestyle="--",color="#b68b29",label="Grid ceiling")
        for ax in axes[row]:
            for hour in PEAK:
                ax.axvspan(hour-.5,hour+.5,color="#b7c5d2",alpha=.20)
            ax.grid(axis="y",alpha=.2)
            ax.set_xticks(range(0,24,2))
        axes[row,0].set(ylabel="Mean settled sales (kWh/hour)",title=config.replace("_"," ").title()+" volume")
        axes[row,1].set(ylabel="$/kWh",title=config.replace("_"," ").title()+" trade and grid prices")
        axes[row,0].legend(fontsize=8)
        axes[row,1].legend(fontsize=8)
    axes[-1,0].set_xlabel("Hour of day")
    axes[-1,1].set_xlabel("Hour of day")
    fig.suptitle("Trading after RC calibration (days 8–31); shaded hours are price peaks")
    fig.tight_layout()
    fig.savefig(out/"trading_impact.png",dpi=160)
    plt.close(fig)


def write_ablation(out, baseline_root, rc_root, ablation_root):
    """Separate advance warming from the shared model and peak-hour guard."""
    rows=[]
    profiles={}
    for config in ("balanced","solar_abundant"):
        for label,root,mode in (("thermostat",baseline_root,"competitive"),
                                ("RC without advance warming",ablation_root,"rc_no_preheat"),
                                ("RC preheat",rc_root,"rc_preheat")):
            houses,market=load_case(root,config,mode)
            h=houses[houses.timestep>=EVALUATION_START]
            m=market[market.timestep>=EVALUATION_START]
            profiles[(config,label)]=h.groupby(["day","hour"]).hvac_kwh.sum().reset_index().groupby("hour").hvac_kwh.mean()
            import json
            full=json.loads((Path(root)/config/mode/"summary.json").read_text())
            rows.append(dict(configuration=config,controller=label,
                comfortable_pct=100*(h.degree_hours<=1e-9).mean(),
                degree_hours=h.degree_hours.sum(),hvac_kwh=h.hvac_kwh.sum(),
                peak_hvac_kwh=h[h.hour.isin(PEAK)].hvac_kwh.sum(),
                pre_peak_hvac_kwh=h[h.hour.isin(PRE_PEAK)].hvac_kwh.sum(),
                traded_kwh=m.sold_kwh.sum(),backed_kwh=m.backed_kwh.sum(),
                full_month_bill_usd=full["total_bill"]))
    table=pd.DataFrame(rows)
    table.to_csv(out/"preheat_ablation.csv",index=False)
    lines=["RC ADVANCE-WARMING ABLATION", "",
           "All three cases use the same buildings and tariff. The two RC cases use the same fitted models and peak-hour guard.",
           "Only RC preheat raises the target ahead of a peak. All metrics except the bill start on day 8.", "",
           table.round(2).to_string(index=False)]
    for config,group in table.groupby("configuration"):
        warm=group[group.controller=="RC preheat"].iloc[0]
        no=group[group.controller=="RC without advance warming"].iloc[0]
        lines.extend(("",config+" advance warming effect (preheat minus no-preheat):",
                      "comfortable hours: %+.2f percentage points" % (warm.comfortable_pct-no.comfortable_pct),
                      "peak heat: %+.1f kWh" % (warm.peak_hvac_kwh-no.peak_hvac_kwh),
                      "pre-peak heat: %+.1f kWh" % (warm.pre_peak_hvac_kwh-no.pre_peak_hvac_kwh),
                      "settled sales: %+.1f kWh" % (warm.traded_kwh-no.traded_kwh),
                      "full-month bill: $%+.2f" % (warm.full_month_bill_usd-no.full_month_bill_usd)))
    (out/"preheat_ablation.txt").write_text("\n".join(lines)+"\n",encoding="utf-8")
    fig,axes=plt.subplots(2,1,figsize=(13,8),sharex=True)
    styles=(("thermostat",COLORS["thermostat"],"-"),
            ("RC without advance warming","#77629b","--"),
            ("RC preheat",COLORS["RC preheat"],"-"))
    for ax,config in zip(axes,("balanced","solar_abundant")):
        for label,color,style in styles:
            ax.plot(range(24),profiles[(config,label)].reindex(range(24)),
                    label=label,color=color,linestyle=style,marker=".")
        for hour in PEAK:
            ax.axvspan(hour-.5,hour+.5,color="#b7c5d2",alpha=.2)
        ax.set(title=config.replace("_"," ").title(),ylabel="Mean heating (kWh/hour)")
        ax.grid(axis="y",alpha=.2)
        ax.legend(fontsize=9)
    axes[-1].set(xlabel="Hour of day",xticks=range(0,24,2))
    fig.suptitle("Advance warming vs the same RC controller without it; days 8–31")
    fig.tight_layout()
    fig.savefig(out/"preheat_ablation.png",dpi=160)
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-runs",default="results/rc_comparison")
    parser.add_argument("--rc-runs",default="results/rc_comparison")
    parser.add_argument("--dataset",default="generated/altered_stock")
    parser.add_argument("--ablation-runs",help="Optional RC run with advance warming disabled")
    parser.add_argument("--out",default="results/rc_preheat_report")
    args=parser.parse_args()
    out=Path(args.out)
    out.mkdir(parents=True,exist_ok=True)
    all_market=[]
    all_houses=[]
    summary=[]
    for config in ("balanced","solar_abundant"):
        base,market_base=load_case(args.baseline_runs,config,"competitive")
        rc,market_rc=load_case(args.rc_runs,config,"rc_preheat")
        plan=pd.read_csv(Path(args.rc_runs)/config/"rc_preheat"/"thermal_plan.csv",
                         usecols=["building","timestep","predicted_next_c"])
        rc=rc.merge(plan,on=["building","timestep"],how="left",validate="one_to_one")
        assert base[['timestep','building']].equals(rc[['timestep','building']]), "Misaligned building runs"
        assert market_base[['timestep','hour','grid_import_price']].equals(
            market_rc[['timestep','hour','grid_import_price']]), "Mismatched market clocks or tariff"
        effects=house_effects(config,base,rc)
        market=market_effects(config,market_base,market_rc)
        all_houses.append(effects)
        all_market.append(market)
        plot_temperatures(out,config,Path(args.dataset)/config,base,rc,market_base,market_rc,effects)
        for label,data in (("thermostat",market_base),("RC preheat",market_rc)):
            e=data[data.timestep>=EVALUATION_START]
            sold=e.sold_kwh.sum()
            summary.append(dict(configuration=config,controller=label,
                settled_sales_kwh=sold,physically_backed_kwh=e.backed_kwh.sum(),
                grid_backed_kwh=sold-e.backed_kwh.sum(),
                trading_hours=int((e.sold_kwh>1e-9).sum()),
                peak_sales_kwh=e[e.hour.isin(PEAK)].sold_kwh.sum(),
                pre_peak_sales_kwh=e[e.hour.isin(PRE_PEAK)].sold_kwh.sum(),
                weighted_price_usd_per_kwh=(e.sold_kwh*e.clearing_price.fillna(0)).sum()/sold
                   if sold else np.nan))
    hourly=pd.concat(all_market,ignore_index=True)
    hourly.to_csv(out/"trading_by_hour.csv",index=False)
    houses=pd.concat(all_houses,ignore_index=True)
    houses.to_csv(out/"house_effects.csv",index=False)
    summary=pd.DataFrame(summary)
    summary.to_csv(out/"trading_comparison.csv",index=False)
    plot_trading(out,hourly)
    lines=["EVALUATION AFTER IDENTIFICATION: DAYS 8–31", "",
        "Volumes are net settled sales counted once. Backed sales have measured same-hour portfolio export; other sales are grid-backed.",
        "Prices are weighted by settled volume. The grid price is the ceiling. One winter month, two synthetic equipment configurations.", "",
        summary.round(3).to_string(index=False)]
    for config,group in summary.groupby("configuration"):
        a=group[group.controller=="thermostat"].iloc[0]
        b=group[group.controller=="RC preheat"].iloc[0]
        lines.extend(("",config+" (RC minus thermostat):",
            "settled sales: %+.1f kWh" % (b.settled_sales_kwh-a.settled_sales_kwh),
            "physically backed sales: %+.1f kWh" % (b.physically_backed_kwh-a.physically_backed_kwh),
            "grid-backed sales: %+.1f kWh" % (b.grid_backed_kwh-a.grid_backed_kwh),
            "peak-hour sales: %+.1f kWh" % (b.peak_sales_kwh-a.peak_sales_kwh),
            "pre-peak sales: %+.1f kWh" % (b.pre_peak_sales_kwh-a.pre_peak_sales_kwh),
            "trading hours: %+d" % (b.trading_hours-a.trading_hours),
            "weighted clearing price: $%+.4f/kWh" % (b.weighted_price_usd_per_kwh-a.weighted_price_usd_per_kwh)))
    (out/"trading_comparison.txt").write_text("\n".join(lines)+"\n",encoding="utf-8")
    if args.ablation_runs:
        write_ablation(out,args.baseline_runs,args.rc_runs,args.ablation_runs)
    print("Wrote temperature browsers, house effects, hourly trading chart and audit to",out)


if __name__=="__main__":
    main()
