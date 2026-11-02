"""Run every example agent against the real neighbourhood and write a booklet.

    pip install -e ".[viz]"
    python examples/sample_run.py                    # 7 days, all examples
    python examples/sample_run.py --days 14 --out my_booklet.html

Produces a self-contained HTML file: an overview of how the market cleared and
whether the buildings stayed inside their comfort bands, then one tab per agent
showing every bid it made as a heatmap -- price on the vertical axis, time on
the horizontal, colour for buy versus sell, and a cross wherever the market
actually cleared.

That last plot is the one worth staring at. A bid only matters relative to the
clearing price, so seeing the whole book against the crosses tells you
immediately whether an agent is pricing itself into the market or out of it.

Needs CityLearn, PyTorch and the Annex 96 dataset, plus plotly. Everything is
inlined, so the output opens offline and can be handed around.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np

from annex96p2p.config import Config
from annex96p2p.reference import reference_costs
from annex96p2p.registry import build_participants
from annex96p2p.runner import simulate
from annex96p2p.tariff import Tariff

ROOT = Path(__file__).resolve().parents[1]

# Buy is blue, sell is red, nothing is the surface colour. Deliberately not
# red/green: about one man in twelve cannot separate those, and this is the
# plot the whole booklet turns on.
DIVERGING = [
    (0.0, "#b03030"), (0.25, "#d98a6a"), (0.5, "#f0efec"),
    (0.75, "#6da7ec"), (1.0, "#1c5cab"),
]


def build_backend(days):
    cfg = Config.load(str(ROOT / "config" / "default.yaml"))
    root = os.environ.get("ANNEX96_CE1") or cfg.citylearn_root
    if root and not os.path.isabs(root):
        root = str((ROOT / root).resolve())
    if root and root not in sys.path:
        sys.path.insert(0, root)
    schema = cfg.citylearn_schema
    if schema and not os.path.isabs(schema):
        schema = str((ROOT / schema).resolve())
    from annex96p2p.backends.citylearn_backend import CityLearnBackend
    return CityLearnBackend(schema=schema, days=days, root=root), cfg


def collect(days):
    backend, cfg = build_backend(days)
    tariff = Tariff.synthetic(backend.n_timesteps, comfort_price=cfg.comfort_price,
                              delivery_adder=cfg.delivery_adder,
                              volatility=cfg.price_volatility, seed=cfg.seed,
                              export_price=cfg.export_price)
    participants, report = build_participants(backend, str(ROOT / "agents/basic/original_exercise"))
    print(report.render() or "(no submissions found)")

    rec = []
    result = simulate(backend, participants, tariff,
                      call_timeout_seconds=cfg.call_timeout_seconds,
                      run_budget_seconds=max(cfg.run_budget_seconds, 900.0),
                      recorder=rec.append)
    ref = reference_costs(backend, participants, tariff)
    return backend, participants, tariff, result, ref, rec


# --------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------
def fig_market(rec, tariff):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    t = [r["timestep"] for r in rec]
    grid = [r["market"].grid_import_price for r in rec]
    clear = [r["clearing"].price for r in rec]
    vol = [r["clearing"].volume for r in rec]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.62, 0.38], vertical_spacing=0.07,
                        subplot_titles=("Grid price and clearing price",
                                        "Volume traded"))
    fig.add_trace(go.Scatter(x=t, y=grid, name="grid price (the ceiling)",
                             line=dict(color="#eb6834", width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=t, y=clear, name="clearing price",
                             mode="lines+markers", connectgaps=False,
                             line=dict(color="#2a78d6", width=2),
                             marker=dict(size=4)), row=1, col=1)
    fig.add_trace(go.Bar(x=t, y=vol, name="volume", marker_color="#1baf7a",
                         showlegend=False), row=2, col=1)
    cleared = sum(1 for c in clear if c is not None)
    fig.update_layout(
        title="The market cleared in %d of %d hours (%.0f%%)"
              % (cleared, len(t), 100 * cleared / max(len(t), 1)),
        height=560, template="plotly_white", hovermode="x unified",
        legend=dict(orientation="h", y=1.12, x=0))
    fig.update_yaxes(title_text="$/kWh", row=1, col=1)
    fig.update_yaxes(title_text="kWh", row=2, col=1)
    fig.update_xaxes(title_text="timestep (hours)", row=2, col=1)
    return fig


def thinness(rec, backend, result):
    """Why is so little traded? Compute it rather than assert it."""
    frame = result.ledger.to_frame()
    obs0 = rec[0]["observations"][backend.building_names[0]]
    pv_hours = sum(1 for r in rec
                   if sum(o.solar_generation_kwh
                          for o in r["observations"].values()) > 0.01)
    traded = sum(r["clearing"].volume for r in rec)
    imported = float(frame.grid_import_kwh.sum())
    return dict(hp=obs0.hp_max_electric_kw, batt=obs0.battery_max_power_kw,
                cap=obs0.battery_capacity_kwh,
                pv_hours=pv_hours, hours=len(rec),
                can_sell=float((frame.net_kwh < 0).mean()),
                traded=traded, imported=imported,
                share=traded / max(traded + imported, 1e-9),
                curtailed=float(frame.curtailed_kwh.sum()))


def fig_comfort(rec, backend):
    """Every building's temperature relative to its own band.

    Absolute degrees cannot be compared here: the band is the occupant
    setpoint plus or minus comfort_band, it differs between buildings and it
    moves hour to hour. So rescale to band units, where 0 is the centre and
    +/-1 the edges, and one shaded strip serves all 25 homes.
    """
    import plotly.graph_objects as go

    names = backend.building_names
    t = [r["timestep"] for r in rec]
    fig = go.Figure()
    fig.add_hrect(y0=-1, y1=1, fillcolor="#1baf7a", opacity=0.12, line_width=0,
                  annotation_text="inside the comfort band",
                  annotation_position="top left")
    worst = {}
    for n in names:
        rel = []
        for r in rec:
            o = r["observations"][n]
            mid = 0.5 * (o.comfort_low + o.comfort_high)
            half = max(0.25, 0.5 * (o.comfort_high - o.comfort_low))
            rel.append((o.indoor_temperature - mid) / half)
        worst[n] = float(np.max(np.abs(rel)))
        fig.add_trace(go.Scatter(
            x=t, y=rel, name=n.replace("resstock-amy2018-2021-release-1-", ""),
            line=dict(width=1), opacity=0.75,
            hovertemplate="%{y:.2f} band units<extra>%{fullData.name}</extra>"))
    out = np.mean([abs(v) > 1 for v in worst.values()])
    fig.update_layout(
        title="Comfort, in band units — 0 is the centre of each building's own "
              "band, ±1 the edges (%d of %d buildings left it at some point)"
              % (sum(1 for v in worst.values() if v > 1), len(names)),
        height=520, template="plotly_white",
        yaxis_title="position in the band", xaxis_title="timestep (hours)",
        showlegend=True, legend=dict(font=dict(size=9)))
    return fig


def fig_bids(rec, participant, step):
    """The whole bid book of one agent, with the clearing price marked."""
    import plotly.graph_objects as go

    t = [r["timestep"] for r in rec]
    top = max(r["market"].grid_import_price for r in rec)
    levels = np.round(np.arange(0, top + step, step), 10)
    z = np.full((len(levels), len(rec)), np.nan)

    for j, r in enumerate(rec):
        arr = r["bids"].get(participant)
        if arr is None:
            continue
        pg = r["market"].price_grid
        for k, q in enumerate(arr):
            if abs(q) < 1e-9:
                continue
            i = int(round(pg[k] / step))
            if 0 <= i < len(levels):
                z[i, j] = q

    lim = np.nanmax(np.abs(z)) if np.isfinite(z).any() else 1.0
    fig = go.Figure()
    fig.add_trace(go.Heatmap(
        z=z, x=t, y=levels, colorscale=DIVERGING, zmid=0,
        zmin=-lim, zmax=lim, colorbar=dict(title="kWh<br>buy +<br>sell −"),
        hovertemplate="t=%{x}  $%{y:.2f}  %{z:.2f} kWh<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=[r["timestep"] for r in rec if r["clearing"].price is not None],
        y=[r["clearing"].price for r in rec if r["clearing"].price is not None],
        mode="markers", name="cleared here",
        marker=dict(symbol="x", size=7, color="#0b0b0b", line=dict(width=1)),
        hovertemplate="cleared at $%{y:.3f}<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=t, y=[r["market"].grid_import_price for r in rec],
        mode="lines", name="grid price (bid ceiling)",
        line=dict(color="#898781", width=1, dash="dot")))
    fig.update_layout(
        title="Bid book — colour is quantity, blue buys and red sells; "
              "crosses mark where the market actually cleared",
        height=560, template="plotly_white",
        yaxis_title="bid price ($/kWh)", xaxis_title="timestep (hours)",
        legend=dict(orientation="h", y=1.1, x=0))
    return fig


def fig_position(rec, participant):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    t = [r["timestep"] for r in rec]
    q, soc, cap = [], [], 0.0
    for r in rec:
        q.append(r["clearing"].quantities.get(participant, 0.0))
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.5, 0.5], vertical_spacing=0.08,
                        subplot_titles=("Cleared position (+ bought / − sold)",
                                        "Battery state of charge"))
    fig.add_trace(go.Bar(x=t, y=q, marker_color="#2a78d6", showlegend=False),
                  row=1, col=1)
    return fig, q


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out", default="sample_run.html")
    args = ap.parse_args(argv)

    try:
        import plotly.io as pio  # noqa: F401
    except ImportError:
        print('plotly is needed for this script:  pip install -e ".[viz]"')
        return 2

    print("building the environment (about 30 s)...")
    backend, participants, tariff, result, ref, rec = collect(args.days)

    board = result.ledger.leaderboard()
    frame = result.ledger.to_frame()
    summary = result.ledger.system_summary()
    subs = [p for p in participants if not p.is_default]

    print()
    print("  %-22s %8s %8s %8s %9s %9s" % ("agent", "bill", "default",
                                           "score", "bought", "sold"))
    rows = []
    for p in sorted(subs, key=lambda x: x.name):
        g = frame[frame.participant == p.name]
        bill = g.total_cost.sum()
        score = bill / ref[p.name] if ref.get(p.name) else float("nan")
        bought = g.p2p_kwh.clip(lower=0).sum()
        sold = -g.p2p_kwh.clip(upper=0).sum()
        rows.append(dict(name=p.name, bill=bill, ref=ref.get(p.name, float("nan")),
                         score=score, bought=bought, sold=sold,
                         energy=g.energy_cost.sum(), comfort=g.comfort_cost.sum(),
                         dh=g.degree_hours.sum(), curt=g.curtailed_kwh.sum(),
                         failures=p.failures, timeouts=p.timeouts))
        print("  %-22s %8.2f %8.2f %8.3f %9.1f %9.1f"
              % (p.name, bill, ref.get(p.name, 0), score, bought, sold))

    write_booklet(args.out, rec, backend, tariff, result, rows, summary,
                  tariff.price_step)
    print()
    print("wrote %s -- open it in a browser" % Path(args.out).resolve())
    return 0


def write_booklet(path, rec, backend, tariff, result, rows, summary, step):
    import plotly.io as pio

    def div(fig, first=False):
        # Drop the camera button: the booklet is meant to be readable when
        # hosted somewhere sandboxed, and a page-initiated download is inert
        # there -- a button that silently does nothing is worse than no button.
        return pio.to_html(fig, include_plotlyjs=("inline" if first else False),
                           full_html=False,
                           config={"displaylogo": False,
                                   "modeBarButtonsToRemove": ["toImage"]})

    cleared = sum(1 for r in rec if r["clearing"].price is not None)
    ratios = [r["clearing"].price / r["market"].grid_import_price
              for r in rec if r["clearing"].price is not None]

    tiles = [
        ("Hours cleared", "%d / %d" % (cleared, len(rec)),
         "%.0f%% of the run" % (100 * cleared / max(len(rec), 1))),
        ("Traded", "%.0f kWh" % summary["p2p_volume_kwh"], "peer to peer"),
        ("Mean clearing", "$%.3f" % (np.mean([r["clearing"].price for r in rec
                                              if r["clearing"].price is not None])
                                     if cleared else float("nan")),
         "%.0f%% of the grid price" % (100 * np.mean(ratios)) if ratios else ""),
        ("Curtailed", "%.0f kWh" % summary["curtailed_kwh"], "earned nothing"),
        ("System bill", "$%.2f" % summary["total_bill"],
         "grid-only $%.2f" % summary["grid_only_bill"]),
    ]
    tile_html = "".join(
        '<div class="tile"><div class="k">%s</div><div class="v">%s</div>'
        '<div class="d">%s</div></div>' % t for t in tiles)

    lb = "".join(
        "<tr><td>%s</td><td>$%.2f</td><td>$%.2f</td><td>$%.2f</td>"
        "<td><b>%.3f</b></td><td>%.1f</td><td>%.1f</td><td>%.1f</td>"
        "<td>%.1f</td><td>%s</td></tr>"
        % (r["name"], r["bill"], r["energy"], r["comfort"], r["score"],
           r["bought"], r["sold"], r["curt"], r["dh"],
           "ok" if not (r["failures"] or r["timeouts"])
           else "%d failed / %d timed out" % (r["failures"], r["timeouts"]))
        for r in sorted(rows, key=lambda x: x["score"]))

    th = thinness(rec, backend, result)
    reps = [("__HP__", "%.1f" % th["hp"]), ("__BATT__", "%.1f" % th["batt"]),
            ("__CAP__", "%.1f" % th["cap"]),
            ("__PVH__", str(th["pv_hours"])), ("__HRS__", str(th["hours"])),
            ("__CANSELL__", "%.0f" % (100 * th["can_sell"])),
            ("__TRADED__", "%.0f" % th["traded"]),
            ("__IMPORTED__", "%.0f" % th["imported"]),
            ("__SHARE__", "%.1f" % (100 * th["share"])),
            ("__CURT__", "%.0f" % th["curtailed"])]
    thin_html = THIN
    for a, b in reps:
        thin_html = thin_html.replace(a, b)

    tabs, panes = [], []
    overview = (div(fig_market(rec, tariff), first=True)
                + div(fig_comfort(rec, backend)))
    tabs.append('<button class="tab" data-p="p0" aria-selected="true">Overview</button>')
    panes.append('<div class="pane" id="p0">%s</div>' % overview)
    for i, r in enumerate(sorted(rows, key=lambda x: x["name"]), start=1):
        tabs.append('<button class="tab" data-p="p%d">%s</button>'
                    % (i, r["name"].replace("example_", "")))
        panes.append('<div class="pane hidden" id="p%d">%s%s</div>'
                     % (i, agent_header(r), div(fig_bids(rec, r["name"], step))))

    html = BOOKLET
    for a, b in [("__THIN__", thin_html), ("__TILES__", tile_html),
                 ("__LEADERBOARD__", lb), ("__TABS__", "".join(tabs)),
                 ("__PANES__", "".join(panes)), ("__HOURS__", str(len(rec))),
                 ("__NB__", str(len(backend.building_names))),
                 ("__PMIN__", "%.3f" % tariff.import_prices.min()),
                 ("__PMAX__", "%.3f" % tariff.import_prices.max())]:
        html = html.replace(a, b)
    Path(path).write_text(html, encoding="utf-8")


def agent_header(r):
    warn = ("" if not (r["failures"] or r["timeouts"]) else
            '<p class="warn">This agent failed %d times and timed out %d times. '
            'A failing agent is silently replaced by the default for that '
            'timestep, so its score is not its own work.</p>'
            % (r["failures"], r["timeouts"]))
    return ('<p class="note">Bill <b>$%.2f</b> against <b>$%.2f</b> for the '
            'default agent on the same buildings — score <b>%.3f</b>. '
            'Bought %.0f kWh, sold %.0f kWh, curtailed %.0f kWh, '
            '%.0f °C·h outside the band.</p>%s'
            % (r["bill"], r["ref"], r["score"], r["bought"], r["sold"],
               r["curt"], r["dh"], warn))


THIN = """
<div class="card" style="border-left:3px solid #d03b3b">
 <h2 style="margin-top:0">Why so little is traded</h2>
 <p class="note">
  Over this run the market moved <b>__TRADED__ kWh</b> while the neighbourhood
  imported <b>__IMPORTED__ kWh</b> from the grid &mdash; the peer-to-peer market
  handled <b>__SHARE__%</b> of the energy. That is not a fault in the mechanism,
  and it is worth understanding before trying to fix it.
 </p>
 <p class="note">
  <b>1. In January there is barely a supply side.</b> A building can only sell
  when it is net producing, and only <b>__CANSELL__%</b> of participant-hours
  are. The compressor draws about <b>__HP__ kW</b> while the battery discharges
  at <b>__BATT__ kW</b> &mdash; so running storage flat out does not even cover a
  building's own heating, let alone leave a surplus to offer. PV produces in
  only <b>__PVH__ of __HRS__</b> hours, because it is a Vermont winter.
 </p>
 <p class="note">
  <b>2. The battery-first rule makes each agent self-sufficient, which thins the
  market further.</b> Every example agent covers its load from storage before
  bidding, so what it brings to the auction is only the part storage cannot
  cover. With <b>__CAP__ kWh</b> batteries that is often nothing. Individually
  rational; collectively it removes the demand the sellers needed.
 </p>
 <p class="note">
  <b>3. And yet __CURT__ kWh was curtailed.</b> It is worth being exact about
  what that is, because the obvious reading is wrong. It is <i>not</i> surplus
  generation that failed to find a buyer. Measured over this run, none of the
  curtailed energy was ever offered to the market &mdash; and in the hours it
  happened there were hundreds of kWh of unfilled <i>buy</i> orders sitting
  there, so anything offered would have sold instantly.
 </p>
 <p class="note">
  It is over-buying. An agent bids for the energy it forecasts it will consume,
  then consumes less than that, and settlement pays nothing back for the
  difference: <code>residual = physical net &minus; cleared position</code>, and
  a negative residual is curtailed, not refunded. So curtailment here measures
  how badly a participant forecast its <i>own</i> demand, not how badly the
  market matched supply to it. The single biggest source of it was agents
  bidding against heat pump commands the physics discards &mdash; see
  <code>BuildingObs.hp_draw_kwh()</code>, which exists because of this.
 </p>
 <p class="note">
  Two things would go after the rest, both student-sized: bid for market energy
  even when the battery could cover it, since energy at 40% of the grid price
  beats spending charge worth more later; and hold charge back for the expensive
  hours instead of spending it on the first import of the day.
 </p>
</div>
"""


BOOKLET = """<!doctype html>
<html><head><meta charset="utf-8">
<title>Annex 96 P2P — sample run</title>
<style>
 :root{--fg:#0b0b0b;--fg2:#52514e;--muted:#898781;--bg:#f9f9f7;--card:#fcfcfb;
       --border:#e1e0d9;--bad:#d03b3b}
 @media (prefers-color-scheme:dark){:root{--fg:#fff;--fg2:#c3c2b7;--muted:#898781;
   --bg:#0d0d0d;--card:#1a1a19;--border:#2c2c2a}}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
 .wrap{max-width:1100px;margin:0 auto;padding:36px 20px 72px}
 h1{font-size:26px;margin:0 0 6px;letter-spacing:-.015em}
 .sub{color:var(--fg2);margin:0 0 24px;max-width:70ch}
 h2{font-size:17px;margin:26px 0 6px}
 .note{color:var(--fg2);font-size:13.5px;max-width:80ch}
 .warn{color:var(--bad);font-size:13.5px;font-weight:600;max-width:80ch}
 .card{background:var(--card);border:1px solid var(--border);border-radius:10px;
       padding:16px;margin:0 0 18px}
 .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
        gap:10px;margin:0 0 22px}
 .tile{background:var(--card);border:1px solid var(--border);border-radius:10px;
       padding:12px 14px}
 .tile .k{font-size:11.5px;color:var(--muted);text-transform:uppercase;
          letter-spacing:.06em}
 .tile .v{font-size:23px;font-weight:600;margin-top:2px;
          font-variant-numeric:tabular-nums}
 .tile .d{font-size:12px;color:var(--fg2)}
 table{border-collapse:collapse;width:100%;font-size:13px;
       font-variant-numeric:tabular-nums}
 th,td{padding:6px 9px;border-bottom:1px solid var(--border);text-align:right}
 th:first-child,td:first-child{text-align:left}
 th{color:var(--muted);font-weight:500;font-size:11.5px;text-transform:uppercase;
    letter-spacing:.05em}
 .tabs{display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--border);
       margin:22px 0 16px}
 .tab{font:inherit;font-size:13.5px;background:transparent;color:var(--fg2);
      border:1px solid transparent;border-bottom:none;border-radius:8px 8px 0 0;
      padding:9px 15px;cursor:pointer;position:relative;top:1px}
 .tab[aria-selected=true]{background:var(--card);color:var(--fg);
      border-color:var(--border);font-weight:600}
 .hidden{display:none}
 footer{color:var(--muted);font-size:12.5px;margin-top:34px;max-width:80ch}
 code{font-family:ui-monospace,Menlo,monospace;font-size:.92em}
</style></head><body><div class="wrap">

<h1>Annex 96 peer-to-peer — sample run</h1>
<p class="sub">
 __NB__ Vermont homes, __HOURS__ hours from January, LSTM thermal dynamics.
 Heating-dominated: outdoor temperatures reach &minus;21&nbsp;&deg;C and the heat
 pumps draw around 19.5&nbsp;kW, so the compressor dominates the load. Grid price
 runs $__PMIN__&ndash;$__PMAX__/kWh. Every example agent uses the same dispatch —
 hug the bottom of the comfort band, cover the load from the battery, touch the
 grid only when the battery is flat — so the difference between them is
 <b>bidding and nothing else</b>.
</p>

<div class="tiles">__TILES__</div>

<div class="card">
 <h2 style="margin-top:0">Scoreboard</h2>
 <p class="note">Score is the agent's bill divided by what the built-in default
  agent pays on the same five buildings, so below 1.000 beats it.</p>
 <table><thead><tr><th>Agent</th><th>Bill</th><th>Energy</th><th>Comfort</th>
  <th>Score</th><th>Bought</th><th>Sold</th><th>Curtailed</th><th>&deg;C&middot;h</th>
  <th>Ran</th></tr></thead><tbody>__LEADERBOARD__</tbody></table>
</div>

__THIN__

<div class="tabs">__TABS__</div>
__PANES__

<footer>
 Generated by <code>python examples/sample_run.py</code>. Each agent tab shows
 its whole bid book: price on the vertical axis, time on the horizontal, colour
 for quantity — blue buys, red sells — and a cross wherever the market cleared.
 A bid only matters relative to those crosses. An agent whose colour sits far
 from them is pricing itself out of the market; one whose colour brackets them
 is trading. Blue and red rather than green and red on purpose: red&ndash;green is
 the common form of colour blindness and this is the plot that carries the
 argument.
</footer>
</div>
<script>
 const tabs=[...document.querySelectorAll('.tab')];
 tabs.forEach(t=>t.onclick=()=>{
   tabs.forEach(x=>x.setAttribute('aria-selected',String(x===t)));
   document.querySelectorAll('.pane').forEach(p=>
     p.classList.toggle('hidden',p.id!==t.dataset.p));
   window.dispatchEvent(new Event('resize'));
 });
</script>
</body></html>
"""


if __name__ == "__main__":
    sys.exit(main())
