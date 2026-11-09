"""Market clearing: the arithmetic everything else depends on."""
import numpy as np
import pytest

from annex96p2p.market import TOL, build_price_grid, clear, validate_bid


def grid(top=0.20):
    return build_price_grid(top)


# --------------------------------------------------------------------------
# price grid
# --------------------------------------------------------------------------
def test_price_grid_spans_zero_to_grid_price():
    g = build_price_grid(0.20)
    assert g[0] == pytest.approx(0.0)
    assert g[-1] <= 0.20 + 1e-12
    assert np.allclose(np.diff(g), 0.01)


def test_price_grid_never_exceeds_the_grid_price():
    # A buyer must never be able to bid above their outside option.
    for top in [0.094, 0.1, 0.237, 0.385, 0.999]:
        assert build_price_grid(top)[-1] <= top + 1e-12


def test_price_grid_degenerate_price():
    assert len(build_price_grid(0.0)) == 1


# --------------------------------------------------------------------------
# clearing
# --------------------------------------------------------------------------
def test_simple_cross():
    """Seller wants >= 0.02, buyer will pay <= 0.20. They should trade."""
    g = grid()
    seller, buyer = np.zeros(len(g)), np.zeros(len(g))
    seller[2] = -5.0                     # sell 5 kWh at $0.02
    buyer[18] = +8.0                     # buy 8 kWh at $0.18

    result = clear({"s": seller, "b": buyer}, g)

    assert result.traded
    assert result.volume == pytest.approx(5.0)     # short side sets the volume
    assert 0.02 <= result.price <= 0.18
    assert result.quantities["s"] == pytest.approx(-5.0)
    assert result.quantities["b"] == pytest.approx(+5.0)


def test_no_cross_means_no_trade():
    """Seller wants more than the buyer will pay."""
    g = grid()
    seller, buyer = np.zeros(len(g)), np.zeros(len(g))
    seller[15] = -5.0                    # sell at $0.15 or above
    buyer[5] = +5.0                      # buy at $0.05 or below

    result = clear({"s": seller, "b": buyer}, g)

    assert not result.traded
    assert result.price is None
    assert result.quantities["s"] == 0.0
    assert result.quantities["b"] == 0.0


def test_only_buyers_no_trade():
    g = grid()
    result = clear({"a": np.full(len(g), 0.0), "b": np.full(len(g), 0.0)}, g)
    assert not result.traded


def test_single_participant_cannot_trade_with_itself_for_free():
    """A lone participant bidding both sides nets to zero, not to profit."""
    g = grid()
    both = np.zeros(len(g))
    both[2] = -5.0
    both[18] = +5.0
    result = clear({"solo": both}, g)
    assert result.quantities["solo"] == pytest.approx(0.0)


def test_quantities_always_sum_to_zero():
    """The market must balance exactly, for any bids at all."""
    rng = np.random.default_rng(0)
    g = grid(0.30)
    for _ in range(300):
        bids = {"p%d" % i: rng.normal(0, 3, len(g)) for i in range(rng.integers(2, 7))}
        result = clear(bids, g)
        assert abs(sum(result.quantities.values())) < 1e-6


def test_volume_never_exceeds_either_side():
    rng = np.random.default_rng(1)
    g = grid(0.30)
    for _ in range(200):
        bids = {"p%d" % i: rng.normal(0, 3, len(g)) for i in range(4)}
        result = clear(bids, g)
        offered = sum(np.maximum(-b, 0).sum() for b in bids.values())
        wanted = sum(np.maximum(b, 0).sum() for b in bids.values())
        assert result.volume <= offered + 1e-9
        assert result.volume <= wanted + 1e-9


def test_pro_rata_rationing_on_the_long_side():
    """Two identical sellers, not enough demand: each gets half."""
    g = grid()
    s1, s2, buyer = np.zeros(len(g)), np.zeros(len(g)), np.zeros(len(g))
    s1[2] = -10.0
    s2[2] = -10.0
    buyer[18] = +6.0

    result = clear({"s1": s1, "s2": s2, "b": buyer}, g)

    assert result.volume == pytest.approx(6.0)
    assert result.quantities["s1"] == pytest.approx(-3.0)
    assert result.quantities["s2"] == pytest.approx(-3.0)


def test_clearing_price_lies_between_the_marginal_bids():
    g = grid()
    seller, buyer = np.zeros(len(g)), np.zeros(len(g))
    seller[4] = -5.0
    buyer[16] = +5.0
    result = clear({"s": seller, "b": buyer}, g)
    assert 0.04 - TOL <= result.price <= 0.16 + TOL


def test_short_supply_clears_at_the_top_of_the_tie():
    """Hour 645 of a course run, in miniature: cheap supply is short, most
    buyers drop out above 0.11 and the next sellers want 0.12. Every price from
    0.01 to 0.11 trades the same volume; the market takes the one where
    demand and supply are closest -- 0.11, next to the crossing -- not the
    middle of the range."""
    g = grid()
    cheap = np.zeros(len(g)); cheap[1] = -43.6
    dear = np.zeros(len(g)); dear[12] = -130.6
    keen = np.zeros(len(g)); keen[19] = +35.6
    eleven = np.zeros(len(g)); eleven[11] = +164.2
    ten = np.zeros(len(g)); ten[10] = +19.1
    nine = np.zeros(len(g)); nine[9] = +5.1

    result = clear({"cheap": cheap, "dear": dear, "keen": keen,
                    "eleven": eleven, "ten": ten, "nine": nine}, g)

    assert result.volume == pytest.approx(43.6)
    assert result.price == pytest.approx(0.11)
    assert result.quantities["dear"] == 0.0


def test_short_demand_clears_at_the_bottom_of_the_tie():
    """The mirror image: plenty of supply, little demand. Excess supply pulls
    the price to the bottom of the tied range."""
    g = grid()
    seller = np.zeros(len(g)); seller[3] = -2.0
    flood = np.zeros(len(g)); flood[5] = -12.0; flood[6] = -10.0; flood[7] = -20.0
    buyer = np.zeros(len(g)); buyer[15] = +10.0

    result = clear({"s": seller, "flood": flood, "b": buyer}, g)

    assert result.volume == pytest.approx(10.0)
    assert result.price == pytest.approx(0.05)


def test_flat_tie_clears_in_the_middle():
    """Demand and supply both flat across the tied range: the imbalance does
    not pick a price, so the middle one is taken."""
    g = grid()
    seller = np.zeros(len(g)); seller[4] = -5.0
    buyer = np.zeros(len(g)); buyer[16] = +3.0

    result = clear({"s": seller, "b": buyer}, g)

    assert result.volume == pytest.approx(3.0)
    assert result.price == pytest.approx(0.10)


def test_tie_break_never_trades_less_than_the_most_possible():
    """Breaking ties by imbalance moves the price, never the volume."""
    rng = np.random.default_rng(2)
    g = grid(0.30)
    for _ in range(300):
        bids = {"p%d" % i: rng.normal(0, 3, len(g)) * (rng.random(len(g)) < 0.2)
                for i in range(rng.integers(2, 7))}
        result = clear(bids, g)
        most = float(np.minimum(result.demand_curve, result.supply_curve).max())
        assert result.volume == pytest.approx(most)


def test_higher_bidder_is_served_first():
    """Scarce supply should go to whoever values it most."""
    g = grid()
    seller = np.zeros(len(g)); seller[1] = -4.0
    keen = np.zeros(len(g)); keen[19] = +4.0     # will pay $0.19
    stingy = np.zeros(len(g)); stingy[2] = +4.0  # will only pay $0.02

    result = clear({"s": seller, "keen": keen, "stingy": stingy}, g)

    assert result.quantities["keen"] > result.quantities["stingy"]


def test_empty_market():
    result = clear({}, grid())
    assert not result.traded
    assert result.quantities == {}


# --------------------------------------------------------------------------
# bid validation: a broken submission must never crash the run
# --------------------------------------------------------------------------
def test_validate_wrong_length_is_repaired():
    arr, notes = validate_bid([1.0, 2.0], 10, 500.0, "who")
    assert len(arr) == 10
    assert notes


def test_validate_nan_is_zeroed():
    arr, notes = validate_bid([np.nan, np.inf, 1.0], 3, 500.0, "who")
    assert np.isfinite(arr).all()
    assert arr[2] == 1.0
    assert notes


def test_validate_clips_absurd_quantities():
    arr, notes = validate_bid([1e9, -1e9], 2, 500.0, "who")
    assert arr.max() <= 500.0
    assert arr.min() >= -500.0
    assert notes


def test_validate_garbage_returns_zero_bid():
    arr, notes = validate_bid("not a bid", 5, 500.0, "who")
    assert len(arr) == 5
    assert not arr.any()
    assert notes
