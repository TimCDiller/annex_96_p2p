"""Walk through one market clearing, step by step, in real numbers.

    python examples/explain_market.py

Read this before you write an agent. It is the whole mechanism in one page.
"""
import numpy as np

from annex96p2p import build_price_grid, clear
from annex96p2p.ledger import Ledger

GRID_PRICE = 0.22          # what the utility charges this hour, $/kWh
EXPORT_PRICE = 0.00        # what the utility pays for exports: nothing


def describe(clearing, width: int = 46) -> str:
    """A plain-text supply/demand picture, for teaching and for debugging."""
    pg, D, S = clearing.price_grid, clearing.demand_curve, clearing.supply_curve
    if len(pg) == 0:
        return "(no price grid)"
    scale = max(float(np.max(D)) if len(D) else 0.0,
                float(np.max(S)) if len(S) else 0.0, 1e-9)
    lines = ["  price   demand   supply", "  ------------------------" + "-" * width]
    for j, p in enumerate(pg):
        d, s = float(D[j]), float(S[j])
        bar_d = "D" * int(round(width * d / scale))
        bar_s = "s" * int(round(width * s / scale))
        mark = "  <== clears here" if clearing.level_index == j else ""
        lines.append("  $%.2f  %7.2f  %7.2f  |%s%s" %
                     (p, d, s, (bar_d if d >= s else bar_s), mark))
    if clearing.traded:
        lines.append("  cleared %.3f kWh at $%.2f/kWh" % (clearing.volume, clearing.price))
    else:
        lines.append("  no trade this timestep")
    return "\n".join(lines)


def rule(title=""):
    print("\n" + "=" * 74)
    if title:
        print(title)
        print("=" * 74)


# ==========================================================================
rule("1. THE PRICE GRID")
# ==========================================================================
price_grid = build_price_grid(GRID_PRICE)
print("""
Every hour you submit ONE signed number per price level. The levels run in
one cent steps from $0.00 up to the current grid price:

    $0.00   you cannot export to the grid, so surplus that does not sell is
            curtailed and earns nothing. Any price above zero beats that.
    $%.2f   you can always buy from the utility at this price, so paying
            more than this inside the market would be irrational.

This hour the grid price is $%.2f, so there are %d legal price levels:
""" % (GRID_PRICE, GRID_PRICE, len(price_grid)))
print("   ", " ".join("%.2f" % p for p in price_grid))
print("""
A positive number at a level means BUY that much at that price or cheaper.
A negative number means SELL that much at that price or dearer.""")


# ==========================================================================
rule("2. THREE PARTICIPANTS BID")
# ==========================================================================
n = len(price_grid)

# Alice: rooftop solar, 5 kWh spare. It is curtailed if it does not sell, so
# she is willing to take almost anything -- but she asks 3 cents, not 1.
alice = np.zeros(n)
alice[3] = -5.0

# Bob: needs 8 kWh. He will pay up to 20 cents rather than 22 at the meter,
# but he would rather pay less, so he splits his demand across two levels.
bob = np.zeros(n)
bob[20] = +3.0        # will pay up to $0.20 for the first 3 kWh
bob[10] = +5.0        # only wants the other 5 kWh if it is $0.10 or less

# Carol: has a battery. She is a bargain hunter -- she only charges if the
# price is genuinely low, so she bids a large quantity at a low price.
carol = np.zeros(n)
carol[4] = +6.0

for name, bid in [("Alice", alice), ("Bob", bob), ("Carol", carol)]:
    orders = ["%s %.1f kWh @ $%.2f" % ("buy" if q > 0 else "sell", abs(q), p)
              for p, q in zip(price_grid, bid) if abs(q) > 1e-9]
    print("  %-6s %s" % (name + ":", "  and  ".join(orders)))


# ==========================================================================
rule("3. CLEARING")
# ==========================================================================
print("""
Demand at price p = everything bid at p or ABOVE (those buyers are happy to
pay p). Supply at price p = everything offered at p or BELOW. The clearing
price is the one that maximises the traded volume -- the rule real power
exchanges use.
""")
clearing = clear({"Alice": alice, "Bob": bob, "Carol": carol}, price_grid)
print(describe(clearing))

print("""
Everyone in the money trades at the SAME price, whatever they bid. Alice
asked $0.03 and gets $%.2f. Bob offered $0.20 for his first 3 kWh and pays
$%.2f. Bidding your true reservation price does not mean paying it.
""" % (clearing.price, clearing.price))

print("  cleared positions (+ bought, - sold):")
for name, quantity in clearing.quantities.items():
    print("    %-6s %+7.3f kWh" % (name, quantity))
print("    %-6s %+7.3f kWh   <-- always exactly zero: the market balances"
      % ("SUM", sum(clearing.quantities.values())))


# ==========================================================================
rule("4. THEN YOU DISPATCH, AND THE GRID SETTLES THE REST")
# ==========================================================================
print("""
Now -- and only now -- you choose heat pump and battery actions, knowing the
price you cleared at. Whatever your buildings physically do, the difference
between your physical energy and your cleared position is settled with the
utility:

    residual = physical net energy - cleared peer-to-peer position

    residual > 0  you import it at the grid price
    residual < 0  it is curtailed, and earns you nothing
""")

# Physical outcomes after dispatch. Alice generated her 5 kWh surplus as
# expected; Bob used slightly more than he bought; Carol charged her battery.
physical = {"Alice": -5.0, "Bob": +9.0, "Carol": +6.0}

ledger = Ledger()
print("  %-6s %9s %9s %9s %9s %9s" %
      ("", "physical", "p2p", "residual", "bill", "grid-only"))
for name, net in physical.items():
    row = ledger.settle(timestep=0, day=0, hour=12, participant=name,
                        is_default=False, net_kwh=net,
                        p2p_kwh=clearing.quantities[name],
                        clearing_price=clearing.price,
                        grid_import_price=GRID_PRICE,
                        grid_export_price=EXPORT_PRICE,
                        degree_hours=0.0, comfort_price=3.0)
    print("  %-6s %+9.2f %+9.2f %+9.2f %9s %9s"
          % (name, row.net_kwh, row.p2p_kwh, row.net_kwh - row.p2p_kwh,
             "$%.3f" % row.energy_cost, "$%.3f" % row.baseline_cost))

frame = ledger.to_frame()
print("""
  everyone paid       $%.3f
  without a market    $%.3f
  the market created  $%.3f of value out of energy that would have been
                      curtailed""" % (frame.energy_cost.sum(),
                                      frame.baseline_cost.sum(),
                                      frame.baseline_cost.sum() - frame.energy_cost.sum()))


# ==========================================================================
rule("5. WHY THERE IS NO PENALTY FOR BIDDING BADLY")
# ==========================================================================
print("""
There is no obligation to deliver what you bid, and no imbalance charge.
None is needed, because the price bounds already make dishonesty lose money
on its own.
""")

honest = ledger.settle(0, 0, 12, "honest", False, 5.0, 5.0, clearing.price,
                       GRID_PRICE, EXPORT_PRICE, 0.0, 3.0)
oversold = ledger.settle(0, 0, 12, "oversold", False, 0.0, -5.0, clearing.price,
                         GRID_PRICE, EXPORT_PRICE, 0.0, 3.0)
overbought = ledger.settle(0, 0, 12, "overbought", False, 0.0, 5.0, clearing.price,
                           GRID_PRICE, EXPORT_PRICE, 0.0, 3.0)

print("  sell 5 kWh you do not have:")
print("     you were paid $%.3f for it, then bought it back from the grid for $%.3f"
      % (5 * clearing.price, 5 * GRID_PRICE))
print("     net cost $%.3f  -- a straight loss, because the clearing price"
      % oversold.energy_cost)
print("     can never exceed the grid price.\n")
print("  buy 5 kWh you cannot use:")
print("     paid $%.3f, and %.0f kWh was curtailed for nothing."
      % (overbought.energy_cost, overbought.curtailed_kwh))
print("     net cost $%.3f  -- again a straight loss.\n" % overbought.energy_cost)
print("""So the grid is always the balancing party, the market never has to
enforce anything, and your only real job is to make your bid and your
dispatch agree with each other.

Next:  python -m annex96p2p run --config config/default.yaml
       then open agents/basic/original_exercise/example_team.py""")
