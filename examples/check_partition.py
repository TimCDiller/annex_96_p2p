"""Will this partition produce a live market? Check before you assign it.

    python examples/check_partition.py

The single biggest way to break this exercise is to hand out portfolios that
are internally balanced. A team whose own buildings cancel each other out has
no net position, never needs the market, and the whole trading exercise
quietly becomes a single-agent control exercise instead.

This is counter-intuitive. Giving each team "one of each archetype" feels
fair and complementary -- and it is exactly what kills the market, because
the complementarity gets used up inside the team. What creates trade is
complementarity BETWEEN teams: specialised portfolios that must trade with
each other because they cannot balance themselves.

This script runs the same agent over several partitions and reports how much
actually gets traded, so you can see the effect rather than guess at it.
"""
import numpy as np

from annex96p2p.backends.mock import MockBackend, default_neighbourhood
from annex96p2p.baselines import SimpleRBCAgent
from annex96p2p.registry import Participant
from annex96p2p.runner import simulate
from annex96p2p.tariff import Tariff

DAYS = 7

PARTITIONS = {
    "specialised (each team one archetype)": {
        "solar_team": ["B01", "B05", "B09"],
        "storage_team": ["B02", "B06", "B10"],
        "mass_team": ["B03", "B07", "B11"],
        "balanced_team": ["B04", "B08", "B12"],
    },
    "semi-specialised (a dominant archetype each)": {
        "team_a": ["B01", "B05", "B03"],
        "team_b": ["B02", "B06", "B07"],
        "team_c": ["B09", "B04", "B08"],
        "team_d": ["B10", "B11", "B12"],
    },
    "mixed (one of each archetype per team)": {
        "team_a": ["B01", "B06", "B11"],
        "team_b": ["B02", "B07", "B12"],
        "team_c": ["B03", "B08", "B09"],
        "team_d": ["B04", "B05", "B10"],
    },
    "one participant per building (12 players)": {
        "b_%s" % name: [name] for name in
        ["B%02d" % i for i in range(1, 13)]
    },
}


def evaluate(assignment):
    backend = MockBackend(n_days=DAYS, specs=default_neighbourhood(3))
    tariff = Tariff.synthetic(backend.n_timesteps, comfort_price=3.0)
    participants = [
        Participant(name=name, agent=SimpleRBCAgent(name=name, buildings=buildings),
                    buildings=buildings)
        for name, buildings in assignment.items()
    ]
    result = simulate(backend, participants, tariff)
    summary = result.ledger.system_summary()
    frame = result.ledger.to_frame()

    # How much of the total energy that changed hands went through the market
    # rather than the meter? This is the number that matters.
    traded = summary["p2p_volume_kwh"]
    imported = summary["grid_import_kwh"]
    return dict(
        teams=len(participants),
        traded=traded,
        share=100.0 * traded / max(imported + traded, 1e-9),
        curtailed=summary["curtailed_kwh"],
        clearing_rate=100.0 * summary["clearing_rate"],
        gain=summary["system_market_gain"],
        bill=summary["total_bill"],
    )


print("""
Every row uses the SAME default agent. The only difference is who owns what.

  traded      kWh that changed hands peer-to-peer over one week
  share       that volume as a share of all energy delivered
  cleared     percentage of hours in which the market cleared at all
  curtailed   surplus PV that found no buyer and was thrown away
  gain        value the market created versus everyone buying from the grid
""")
print("  %-44s %5s %8s %7s %8s %10s %8s"
      % ("partition", "teams", "traded", "share", "cleared", "curtailed", "gain"))
print("  " + "-" * 96)

results = {}
for label, assignment in PARTITIONS.items():
    row = evaluate(assignment)
    results[label] = row
    print("  %-44s %5d %8.1f %6.1f%% %6.1f%% %10.1f %8s"
          % (label, row["teams"], row["traded"], row["share"],
             row["clearing_rate"], row["curtailed"], "$%.2f" % row["gain"]))

best = max(results, key=lambda k: results[k]["traded"])
worst = min(results, key=lambda k: results[k]["traded"])
ratio = results[best]["traded"] / max(results[worst]["traded"], 1e-9)

print("""
  "%s"
  trades %.0fx more than
  "%s".

Same buildings, same agent, same prices. Only the ownership boundary moved.

Two things follow for setting up a course:

  1. Specialise the portfolios. Give one team the PV and another the
     batteries. If a team can balance itself internally it will, and it
     should -- moving energy between two buildings you already own is free,
     and it never touches the market.

  2. More participants means a deeper market. Twelve players trade far more
     than four holding the same twelve buildings, because every ownership
     boundary is a boundary energy has to cross by trading. If your cohort
     is small, consider one building per student rather than three.
""" % (best, ratio, worst))
