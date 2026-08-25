"""Settlement arithmetic, and the incentives it creates.

The tests at the bottom are the important ones: they check that a
participant who bids dishonestly loses money automatically, which is why
this exercise needs no imbalance penalty and no obligation to deliver.
"""
import pytest

from annex96p2p.ledger import Ledger


def settle(net, p2p, price, grid_price=0.22, export=0.0, degree_hours=0.0,
           comfort_price=3.0):
    ledger = Ledger()
    return ledger.settle(timestep=0, day=0, hour=12, participant="p",
                         is_default=False, net_kwh=net, p2p_kwh=p2p,
                         clearing_price=price, grid_import_price=grid_price,
                         grid_export_price=export, degree_hours=degree_hours,
                         comfort_price=comfort_price)


def test_worked_example_seller():
    """5 kWh surplus, all of it sold at $0.11. Revenue, no grid interaction."""
    row = settle(net=-5.0, p2p=-5.0, price=0.11)
    assert row.grid_import_kwh == pytest.approx(0.0)
    assert row.curtailed_kwh == pytest.approx(0.0)
    assert row.energy_cost == pytest.approx(-0.55)


def test_worked_example_buyer():
    """8 kWh needed, 5 bought at $0.11, remaining 3 from the grid."""
    row = settle(net=8.0, p2p=5.0, price=0.11)
    assert row.grid_import_kwh == pytest.approx(3.0)
    assert row.energy_cost == pytest.approx(0.11 * 5 + 0.22 * 3)


def test_gains_from_trade_are_split():
    """Both sides beat their outside option, and the total gain is real."""
    seller = settle(net=-5.0, p2p=-5.0, price=0.11)
    buyer = settle(net=8.0, p2p=5.0, price=0.11)

    # Outside options: seller curtails for $0, buyer pays retail for all 8.
    assert seller.energy_cost < seller.baseline_cost
    assert buyer.energy_cost < buyer.baseline_cost
    total_gain = ((seller.baseline_cost - seller.energy_cost)
                  + (buyer.baseline_cost - buyer.energy_cost))
    assert total_gain == pytest.approx(5 * 0.22)      # 5 kWh saved from curtailment


def test_unsold_surplus_is_curtailed_not_paid():
    """With export banned, surplus that does not sell earns nothing."""
    row = settle(net=-4.0, p2p=0.0, price=None)
    assert row.curtailed_kwh == pytest.approx(4.0)
    assert row.export_revenue == pytest.approx(0.0)
    assert row.energy_cost == pytest.approx(0.0)


def test_no_trade_falls_back_to_the_grid():
    row = settle(net=6.0, p2p=0.0, price=None)
    assert row.energy_cost == pytest.approx(0.22 * 6)


def test_comfort_penalty_enters_the_bill():
    row = settle(net=0.0, p2p=0.0, price=None, degree_hours=2.0)
    assert row.comfort_cost == pytest.approx(6.0)
    assert row.total_cost == pytest.approx(6.0)


# --------------------------------------------------------------------------
# The self-policing property. This is why there is no imbalance charge.
# --------------------------------------------------------------------------
def test_overselling_loses_money():
    """Sell 5 kWh you do not have: you buy it back at the higher grid price."""
    honest = settle(net=0.0, p2p=0.0, price=0.11)
    greedy = settle(net=0.0, p2p=-5.0, price=0.11)   # sold 5, generated none
    assert greedy.energy_cost > honest.energy_cost
    # Paid 0.22/kWh to cover it, was paid only 0.11/kWh for it.
    assert greedy.energy_cost == pytest.approx(-0.11 * 5 + 0.22 * 5)


def test_overbuying_loses_money():
    """Buy 5 kWh you cannot use: it is curtailed and you paid for nothing."""
    honest = settle(net=0.0, p2p=0.0, price=0.11)
    hoarder = settle(net=0.0, p2p=5.0, price=0.11)
    assert hoarder.energy_cost > honest.energy_cost
    assert hoarder.curtailed_kwh == pytest.approx(5.0)


def test_honest_bidding_is_the_best_response():
    """Across a sweep of positions, matching your bid to your physics wins."""
    physical = 6.0
    costs = {q: settle(net=physical, p2p=q, price=0.11).energy_cost
             for q in [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]}
    # Buying exactly what you consume is the cheapest choice available.
    assert min(costs, key=costs.get) == pytest.approx(6.0)


def test_clearing_price_bound_is_what_makes_it_work():
    """If the clearing price could exceed the grid price, buying would be
    irrational -- the bound on the price grid is load-bearing."""
    at_bound = settle(net=5.0, p2p=5.0, price=0.22, grid_price=0.22)
    from_grid = settle(net=5.0, p2p=0.0, price=None, grid_price=0.22)
    assert at_bound.energy_cost == pytest.approx(from_grid.energy_cost)
