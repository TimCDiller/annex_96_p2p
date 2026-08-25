# Running against the real Annex 96 neighbourhood

The default backend is a small self-contained building simulator. It exists so
the repo runs the instant it is cloned, with no heavy dependencies and no
dataset download. It is a teaching model, not a research model.

This document covers swapping in the actual Common Exercise 1 homes.

> **Status: the adapter is written but not yet verified against the upstream
> dataset.** Everything else in the repo is tested and runs. Work through the
> checklist at the bottom once, then delete this warning.

## Setup

```bash
pip install -e ".[citylearn]"
git clone https://github.com/kkaspar10/annex96_common_exercise_1 ../annex96_ce1
```

Point a config at the schema:

```yaml
backend: citylearn
citylearn_schema: ../annex96_ce1/data/datasets/annex96_ce1_tx_neighborhood/schema.json
days: 7
```

The CE1 buildings carry LSTM dynamics models, so this also needs PyTorch and
is far slower than the mock backend. Develop against the mock backend, then
confirm here.

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
      `env.buildings` has the expected count. CE1 advertises 25 homes; the TX
      folder contains 26 building CSVs. Confirm which the schema actually uses.
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
- [ ] Units. The mock backend works in kWh per hourly timestep throughout.
      Confirm `net_electricity_consumption` is kWh and not kW, and that
      `solar_generation` sign matches (the adapter takes `abs()`).
- [ ] Run `python -m annex96p2p run` with no submissions and confirm the
      ledger balances — `check_balance()` must return an empty list.
- [ ] Confirm the default agent produces sane bills and comfort numbers before
      trusting any student result.

## Sizing the market

CE1's 25–26 homes support a much larger cohort than the mock backend's 12.
With 5 buildings each that is five teams; with 2 each, twelve.

Whatever you choose, **make the partitions deliberately unequal**. Stratify
the homes by PV-to-load ratio, battery capacity and thermal mass, then hand
out lopsided bundles — one team PV-rich and storage-poor, one the reverse, one
with neither but heavy fabric. If every portfolio looks the same, everyone
wants to buy and sell at the same hours and the market clears almost nothing.
