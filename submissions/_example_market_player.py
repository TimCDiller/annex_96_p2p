"""A storage owner who tries to play the market -- a worked example.

Copy it to start from it:

    cp submissions/_example_market_player.py submissions/team_yourname.py

It runs exactly the default agent's physical plan -- same thermostat, same
battery schedule, same grid top-ups before the peaks, same real-time
balancing -- and changes only the PRICES it bids. So comparing it with the
default agent on the same house measures the bidding strategy and nothing
else.

Its beliefs about the other participants:

  * PV owners will sell surplus at almost nothing, because unsold PV is
    curtailed. So it buys energy FOR STORAGE only when it is really cheap,
    at CHEAP_PRICE or less. Its own load it still buys at up to the grid
    price -- any market price below the grid's is a saving, and what does not
    clear comes from the grid anyway.
  * In the peaks, short households will pay almost the grid price. So it
    offers stored energy only at SELL_FRACTION of the grid price or more.
    What does not sell stays in the battery for a later hour.

Outside the peaks it sells like the default agent (at $0.01): there is no
one to hold out against then.

Two things to watch for in the results, because a uniform-price market does
not reward bids the way intuition suggests:
  * The clearing price is the middle of the range of prices that trade the
    most energy. A high ask that still clears drags that range -- and the price
    paid to EVERY seller in that hour -- upwards. Holding out can help the
    other sellers as much as yourself.
  * An ask that does not clear earns nothing that hour; the energy waits, and
    loses 10% on the round trip if it is later used at home instead.
"""
from annex96p2p.stock_agents import StorageServiceAgent

NAME = "market_player"
# A storage-rich home: 80 kWh battery, 80 kW (1C). Replace with your assignment.
BUILDINGS = ["resstock-amy2018-2021-release-1-199613"]

CHEAP_PRICE = 0.02      # $/kWh: the most it pays for energy to store
SELL_FRACTION = 0.80    # in a peak, the least it accepts, as a share of the grid price
PEAK_PRICE = 0.20       # $/kWh: same peak definition as the default agent


class MarketPlayer(StorageServiceAgent):

    def bid(self, obs, mkt):
        net = self.plan_hour(obs, mkt)          # the default agent's physical plan
        grid = mkt.grid_import_price
        peak = grid >= PEAK_PRICE
        bid = mkt.new_bid()

        if net >= 0:
            # What it must draw anyway (load after planned discharge, plus any
            # planned grid top-up) is worth up to the grid price. Anything on
            # top is energy for storage: only if it is really cheap.
            must = max(0.0, sum(p["floor"] for p in self.plan.values()))
            must = min(must, net)
            bid.buy(grid, must)
            bid.buy(min(CHEAP_PRICE, grid), net - must)
            price = CHEAP_PRICE if net > must else grid
        else:
            price = SELL_FRACTION * grid if peak else 0.01
            bid.sell(price, -net)

        self.offer_log.append(dict(timestep=mkt.timestep, planned_net_kwh=net,
            offered_kwh=abs(net), offer_price=price, behavior="market_player"))
        return bid.array


AGENT = MarketPlayer
