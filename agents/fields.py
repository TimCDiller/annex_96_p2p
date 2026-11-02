"""THE FIELD: who runs the houses your submission does not claim.

Choose it with --field when you run the market:

    python -m annex96p2p.altered_stock run --configurations balanced --modes competitive \
        --submissions submissions --field default      # <-- switch here

  --field default    every other house runs the default agent. The graded
                     setting, and what you get if you leave --field out.
  --field basic      SIMPLE opponents: the default agent, half_battery and
                     market_player (agents/basic/), spread over the houses.
  --field advanced   COMPLEX opponents on every house: market forecast battery
                     management, marginal value and team_opt_hp
                     (agents/advanced/).
  --field diverse    a mix of both: some houses default, some simple, some
                     complex agents -- the most varied market to test in.

Houses you claim are taken out of the field, so you always play your own
houses against whatever runs the rest. In the hourly page your participant
is starred (★) and the field's agents are marked with ◆.

Each entry puts one agent file from agents/ on a list of houses; see
src/annex96p2p/fields.py for the format. To build your own field, copy one
of these, change it, and run it with --field <its name>.
"""


def houses(*ids):
    return ["resstock-amy2018-2021-release-1-%s" % i for i in ids]


HALF_BATTERY = "basic/half_battery.py"
MARKET_PLAYER = "basic/market_player.py"
FORECAST = "advanced/market_forecast_battery.py"
MARGINAL = "advanced/marginal_value_storage.py"
TEAM_OPT_HP = "advanced/team_opt_hp.py"

# The stock: ten PV-rich homes, five storage-rich homes with a fast (80 kW)
# battery and five with a slow (20 kW) one.
#   PV-rich       112208 179247 20199 223581 247942 376570 409896 425540 460412 481052
#   fast storage  199613 245723 408344 4421 485614
#   slow storage  147002 216895 319918 411001 467125

FIELDS = {
    "default": [],

    # Simple agents: a third of each kind of house each, the rest default.
    "basic": [
        dict(agent=HALF_BATTERY, name="half battery",
             houses=houses("179247", "223581", "409896", "408344", "216895")),
        dict(agent=MARKET_PLAYER, name="market player",
             houses=houses("20199", "247942", "460412", "199613", "411001")),
    ],

    # Complex agents on every house. The forecast agent runs only on storage
    # homes, the strategy it was built for; team_opt_hp runs one portfolio.
    "advanced": [
        dict(agent=TEAM_OPT_HP, name="team_opt_hp", together=True,
             houses=houses("112208", "179247", "425540", "4421", "147002")),
        dict(agent=FORECAST, params=dict(FORECAST_HOURS=3),
             houses=houses("199613", "245723", "408344", "216895", "319918")),
        dict(agent=MARGINAL, name="marginal value",
             houses=houses("20199", "223581", "247942", "376570", "409896", "460412",
                           "481052", "485614", "411001", "467125")),
    ],

    # Simple and complex side by side, spread evenly over the kinds of house.
    "diverse": [
        dict(agent=TEAM_OPT_HP, name="team_opt_hp", together=True,
             houses=houses("112208", "179247", "425540", "199613", "147002")),
        dict(agent=MARGINAL, name="marginal value",
             houses=houses("20199", "223581", "247942", "4421", "411001")),
        dict(agent=FORECAST, params=dict(FORECAST_HOURS=3),
             houses=houses("245723", "408344", "216895", "319918")),
        dict(agent=HALF_BATTERY, name="half battery", houses=houses("409896")),
        dict(agent=MARKET_PLAYER, name="market player", houses=houses("485614")),
        # 376570 460412 481052 467125: the default agent
    ],
}
