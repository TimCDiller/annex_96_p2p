# Scenario gallery

What happens in the course market when participants try different things?
Each page below describes a set of experiments **in words** — what each
participant believes and does — and shows what happened over the full Vermont
January month: bills, prices, energy, and who gained at whose expense.

The agents behind these runs are not published. The rules are described
precisely enough that you can rebuild any of them yourself, and the
interactive pages show every bid they made — reproducing a result is a good
way to check you understand it, and a fair starting point for your own agent.

| page | the question | the short answer |
|---|---|---|
| [1. How strong is the default agent?](01_default_agent.md) | Can a student beat the agent that runs every unclaimed house? | On a storage-rich house with a fast battery, yes: two simple ideas save **14%**. On a slow battery about 4%. A broken thermostat loop was found and fixed along the way. |
| [2. PV-rich homes and the building as a battery](02_building_heat.md) | The PV-rich homes sell surplus at 7¢ and buy back at 22–32¢. Can they store the surplus as heat in the building? | Barely: heat stored at 14:00 has mostly leaked away by 18:00. What does work is **coasting** — sitting lower in the comfort band during the evening peak — worth about **5%**. |
| [3. Market power: players, cartels and cheaters](03_market_power.md) | What can strategic pricing achieve in a uniform-price market? | A lot of money moves — over **$1,100 a month** between groups — but almost no energy does. Rigid strategies that meet each other destroy value; learning ones recover most of it. Cheating on a cartel by a cent earns a little, and costs the loyal members more. |

## Ground rules for every page

- **Market:** the altered Vermont stock, `balanced` configuration, mode
  `competitive`, full month (743 hours) — the market you test in. See the
  [README](../../README.md#the-course-market).
- **Baseline:** every house on the current default agent (smoothed thermostat,
  battery top-ups before the peaks, real-time balancing). Every number is
  compared against it, on the same houses.
- **One participant per house** unless a page says otherwise, so a "cartel" is
  ten separate participants that agree on prices, not one merged portfolio.
- **Bills include comfort**: $0.50 per °C·hour outside the comfort band.

## Interactive pages

Each scenario page links an hourly page: trades, prices, the clearing price as
a share of the grid price, and — per house or group — battery charge, grid
imports and **the bid heatmap** (red = would buy at that price, green = would
sell). Participants that did not run the default agent are starred.

GitHub shows these files as source rather than as a page: open them from your
clone (`docs/scenarios/pages/` and `docs/runs/`) or download them. They load
the plotting library from the web, so you need a connection.

| page | what it shows |
|---|---|
| [`../runs/all_default.html`](../runs/all_default.html) | everyone on the default agent — the baseline |
| [`../runs/market_player.html`](../runs/market_player.html) | one storage owner playing the market |
| [`pages/pv_cartel.html`](pages/pv_cartel.html) | the ten PV-rich homes as a fixed-price cartel |
| [`pages/learning_cartel.html`](pages/learning_cartel.html) | the same cartel, learning its prices |
| [`pages/cartel_vs_hardliners.html`](pages/cartel_vs_hardliners.html) | the fixed cartel against storage owners who call bluffs — a standoff |
| [`pages/learning_vs_hardliners.html`](pages/learning_vs_hardliners.html) | the learning cartel against the same storage owners |
