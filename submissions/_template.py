"""Starting point for your agent. Copy it, do not edit it in place:

    cp submissions/_template.py submissions/team_yourname.py

Set NAME and BUILDINGS to the assignment posted on Canvas, then test it in the
course market (the altered Vermont winter stock, balanced configuration):

    python -m annex96p2p.altered_stock run --configurations balanced --modes competitive --days 7 --submissions submissions --out results/team_yourname

Add --field basic, --field advanced or --field diverse to play the other
houses against simple or complex opponents instead of the default agent
(agents/fields.py; "Choosing your opponents" in the README).

Files starting with "_" are ignored by the runner, which is why this one is not
loaded. This folder is for testing on your own machine; hand in your file on
Canvas. See "Testing your agent" and "Handing in" in the README.

The starter policy inherits StorageServiceAgent -- the same controller that
runs every house nobody claims. Read it in src/annex96p2p/stock_agents.py
before changing it, and explain your changes in your report.
"""
from annex96p2p.stock_agents import StorageServiceAgent

NAME = "team_yourname"
# Replace with your assigned buildings. These two are PV-rich homes, as a placeholder.
BUILDINGS = ["resstock-amy2018-2021-release-1-112208",
             "resstock-amy2018-2021-release-1-179247"]


class StudentAgent(StorageServiceAgent):
    pass


AGENT = StudentAgent
