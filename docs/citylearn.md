# The building stock

This is the only backend: the real Annex 96 Common Exercise 1 neighbourhood,
25 ResStock homes per dataset -- Vermont in January (743 hourly steps, the
course default) and Texas in June (719) -- with LSTM thermal dynamics per
building. The course market (README) is 20 of the Vermont homes with their PV
and batteries redistributed; everything below about the CityLearn adapter
applies to it unchanged.

Everything below was read off a live environment rather than assumed, and the
four items under "What the real stock does differently" were all wrong in the
adapter's first draft -- each of them produced plausible-looking numbers rather
than an error, which is why they are pinned by tests in
`tests/test_citylearn.py`.

> **Verified against the upstream Texas dataset**, end to end with a
> five-building portfolio. `python -m pytest tests/test_citylearn.py` re-checks
> it, and skips itself if the dataset is not installed.

## What the real stock does differently

Four things bit hard enough to be worth stating before you switch a lab over.

**1. The action space is not `[-1, 1]`.** The battery action is a fraction of
CAPACITY per timestep bounded by the power rating (2.5 kW over 15 kWh gives
0.1667), and the cooling action is one-sided `[0, 1]`. Our `Action.battery` is
a fraction of RATED POWER, so the adapter converts by
`nominal_power / capacity`.

**2. `cooling_device.efficiency` is not a COP.** It is 0.253 on building 0 --
a fraction-of-Carnot figure. `get_cop()` returns about 4.25. Reporting the
former as a COP is wrong by a factor of seventeen.

**3. The first 13 timesteps are a warmup.** The buildings are
`LSTMDynamicsBuilding` with `lookback = 13`, so the indoor temperature is
pinned at its initial value until the model has that much history -- the
cooling action provably does nothing before then. Harmless over 720 steps;
do not draw conclusions from a two-day run.

**4. Comfort is much harder here, and it needs a PI controller.** The band is
the observed setpoint plus or minus `comfort_band` — 16-20 degC on Vermont
building 0 — and it *moves*, by up to 7 K within a single building.

The heat pumps are enormous relative to an hourly step. On the Vermont stock
they draw up to **19.5 kW electric** at a heating COP of about 1.2, and one hour
at full command moves the indoor temperature roughly **15 K**. Anything that
saturates bang-bangs the building. Measured on all 25 Vermont buildings, aiming
at the centre of the band, skipping the first 48 h so startup is excluded:

| control | in band | degC-h | worst excursion |
|---|---:|---:|---:|
| proportional, band 1.6 K (what used to ship) | 33.3% | 7908 | 35.1 K |
| proportional, band widened to 16 K | 3.5% | 8427 | 9.8 K |
| **proportional + integral** (what ships) | **77.3%** | **3351** | — |

The middle row is the one to pause on, because it is counterintuitive:
widening the proportional band cuts the worst excursion nearly four-fold and
still makes in-band *worse*. It trades oscillation for a larger steady offset,
and that is structural — a P controller produces output only in proportion to
error, so against the large, steady load of a January heating season it settles
permanently below target no matter how it is tuned. Integral action is what
fixes this loop; gain tuning is not. `api.Thermostat` is therefore a PI
controller with anti-windup, and it is used by the default agent and all four
examples.

Two failure modes this cost real debugging time to find, both worth knowing
before you write your own controller:

- **the command is one-directional.** Each building actuates a heating device
  or a cooling one, never both, and the physics discards the infeasible half —
  on a Vermont building a negative command is simply "off". It is harmless to
  issue but ruinous to *bid* against: 62% of the thermostat's commands are the
  infeasible sign, so pricing them as full power invented 20,020 kWh of demand
  over five days. Use `BuildingObs.hp_draw_kwh()`.
- **the controller state must be idempotent per timestep.** Agents need the
  command twice an hour, once in `bid()` and once in `dispatch()`. Advancing an
  integrator twice per step winds it up at double rate.

`comfort_price` is **0.50** in `config/default.yaml`. It is set from a measured
breakeven, not by feel: better control pays for its own energy only above
roughly $0.18/degC-h, so below that ignoring comfort is the rational strategy
and the term is decorative. At 3.00 comfort would be 90% of the bill and the
market would be irrelevant.

### The market is thin here, and that is structural

Every one of the 25 homes has a PV array sized to 70-100% of its own annual
demand AND a battery holding 1.5-3.5 hours of its own daily peak. Relative to
the building it sits on, the equipment is near-uniform: PV varies only 1.64x
per MWh consumed across the whole neighbourhood.

So there is no structural seller to pair with a structural buyer. Everyone is
long solar in the same hours and everyone has somewhere to put it. No
partition of these buildings unlocks more trade than another, and the gains
available come from pricing your own inventory well rather than from finding a
counterparty who is short. Set expectations accordingly.

**And a result worth building a lab around.** On the real stock the naive
market *destroys* value -- aggregate gain is negative. The cause is measurable:
the default agent bids its passive net (load minus solar) and then runs the
compressor, so on buildings that have both PV and a large heat pump it sells
energy it is about to consume. Over 72 timesteps, **45.7 of 115.1 kWh sold were
never produced** -- 40% of all sales phantom, across 259 participant-hours. It
is paid $0.01 for them and buys the shortfall from the grid at full price.
Every real CE1 home has both PV and a big compressor, which is why this bites
here and barely showed on the synthetic stock.

Fixing it is a one-line change in a student's bid, and it is the single most
instructive thing in the exercise.

## Setup

```bash
python -m pip install -r scenarios/requirements.txt
python -m pip install -e .
git clone -c core.longpaths=true https://github.com/kkaspar10/annex96_common_exercise_1 ../annex96_common_exercise_1
git -C ../annex96_common_exercise_1 checkout 28ab31e88f9f4e1559bca7be3aaae8c918a20f83
```

Do not install CityLearn from PyPI: the runner imports the exercise's own
fork from the upstream checkout, and `scenarios/requirements.txt` pins what
that fork needs.

Point a config at the schema. Both keys are needed: the schema references its
data files by relative path, so the runner has to know the repo root too.

```yaml
citylearn_root: ../annex96_common_exercise_1
citylearn_schema: ../annex96_common_exercise_1/data/datasets/annex96_ce1_vt_neighborhood/schema.json
days: 7          # blank runs the dataset's full length
```

Swap `vt` for `tx` to run the Texas summer instead. The two datasets share no
building ids, so every submission's `BUILDINGS` list has to be reissued when
you switch — run `python -m annex96p2p buildings` to list them.

The CE1 buildings carry LSTM dynamics models, so this also needs PyTorch and is
slow. Iterate with a small `days:`, then confirm on the full run.

## What has to change in the dataset

### 1. Prices

The CE1 schema ships with `"pricing": null` on every building, and every
`electricity_pricing` observation set to `"active": false`. The hook exists,
it is simply switched off.

This repo does not need it — prices come from the `Tariff` object, which is
independent of the backend. But if you would rather drive prices through
CityLearn so agents read them from the observation vector, author a
`pricing.csv` next to `weather.csv` with the four columns CityLearn expects:

```
electricity_pricing,electricity_pricing_predicted_1,electricity_pricing_predicted_2,electricity_pricing_predicted_3
```

then set `"pricing": "pricing.csv"` and flip those four observations to
`"active": true`. The `predicted_*` columns are 6, 12 and 24 hours ahead in
most CityLearn datasets. `noise_std` on the data class gives you a
forecast-quality dial for a later lab.

### 2. Keep the export ban

CE1 forbids exporting PV to the grid. **Keep that rule.** It is exactly what
makes the peer-to-peer market worth having: unsold surplus is curtailed and
earns nothing, so any peer paying more than zero beats the grid. Setting
`export_price` above zero in the config weakens the market considerably.

### 3. The district target

CE1 has a per-building `district_load_target: district_target.csv`, which is
the flat reference load the original exercise asks you to track. This exercise
replaces that objective with bill minimisation, so the field is unused. Leave
it in place; nothing reads it.

## How the mapping works

CityLearn returns a flat float vector per building, and the meaning of each
slot depends on which observations the schema has switched on. The adapter
therefore reads `env.observation_names` and zips it into a dict, rather than
indexing by position. That is the only version-stable way to do it, and it is
why the adapter survives schema changes that would silently corrupt an
index-based mapping.

Actions go the other way: `Action.hvac` and `Action.battery` are matched
against `env.action_names` by substring, so a schema exposing
`electrical_storage`, `cooling_device` and `heating_device` gets all three
filled correctly and anything else is left at zero.

## Verification checklist

Work through this once against the real dataset.

- [ ] `CityLearnEnv(schema, central_agent=False)` constructs, and
      `env.buildings` returns 25. Both the TX and VT schemas define exactly
      25 buildings, all with `include: true` and all carrying a PV array and
      a battery. The dataset folder holds a few more ResStock CSVs than that;
      the schema is the authority on which are actually used.
- [ ] `env.observation_names[0]` contains the names the adapter looks for:
      `indoor_dry_bulb_temperature`, `non_shiftable_load`, `solar_generation`,
      `electrical_storage_soc`, `outdoor_dry_bulb_temperature`, `hour`.
      Any that are missing come back as `0.0` via `_first()` — check none are
      silently defaulting.
- [ ] `env.action_names[0]` — confirm the substring matching in `_encode()`
      covers every action the schema exposes, and that the sign convention for
      cooling matches (the adapter assumes cooling takes a positive action in
      `[0, 1]`).
- [ ] `env.reset()` return shape: newer Gymnasium returns `(obs, info)`, older
      returns just `obs`. The adapter handles both; confirm which you get.
- [ ] Comfort band: the adapter derives it as the cooling setpoint plus or
      minus 2 K. CE1 specifies 20–24 °C heating and 22–26 °C cooling. Set
      `comfort_low` and `comfort_high` explicitly if that derivation is wrong.
- [ ] Units. Everything is kWh per hourly timestep throughout.
      Confirm `net_electricity_consumption` is kWh and not kW, and that
      `solar_generation` sign matches (the adapter takes `abs()`).
- [ ] Run `python -m annex96p2p run` with no submissions and confirm the
      ledger balances — `check_balance()` must return an empty list.
- [ ] Confirm the default agent produces sane bills and comfort numbers before
      trusting any student result.

## Sizing the market

CE1's 25 homes support a cohort of five teams of five buildings each.
With 5 buildings each that is five teams; with 2 each, twelve and one spare.

Whatever you choose, **make the partitions deliberately unequal**. Stratify
the homes by PV-to-load ratio, battery capacity and thermal mass, then hand
out lopsided bundles — one team PV-rich and storage-poor, one the reverse, one
with neither but heavy fabric. If every portfolio looks the same, everyone
wants to buy and sell at the same hours and the market clears almost nothing.
