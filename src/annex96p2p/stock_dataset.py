"""Build portable altered-stock schemas without modifying upstream files."""
import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_recipe(path):
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if data["building_count"] != 2 * data["pv_house_count"]:
        raise ValueError("This recipe requires equal numbers of PV and storage houses")
    if data["evil_house_count"] / data["building_count"] != 0.30:
        raise ValueError("The strategic owner must have exactly 30% of the stock")
    if len(data["duck_prices"]) != 24 or min(data["duck_prices"]) <= 0:
        raise ValueError("Provide 24 positive hourly retail prices")
    for spec in data["configurations"].values():
        if any(float(spec[k]) <= 0 for k in ("pv_house_pv_kw", "pv_house_battery_kwh",
                "pv_house_c_rate", "storage_house_battery_kwh")):
            raise ValueError("Equipment sizes must be positive")
        if sorted(spec["storage_house_c_rates"]) != [0.25, 1.0]:
            raise ValueError("Storage houses must include both 0.25C and 1C")
    return data


def duck_prices(recipe, steps):
    t = np.arange(steps)
    shape = np.asarray(recipe["duck_prices"], dtype=float)
    return shape[t % 24] * (1 + recipe["daily_price_variation"] * np.sin(2*np.pi*(t//24)/7))


def battery_spec(capacity, rate, recipe):
    efficiency = recipe["round_trip_efficiency"]
    return dict(type="citylearn.energy_model.Battery", autosize=False, attributes=dict(
        capacity=capacity, nominal_power=capacity * rate, efficiency=efficiency,
        initial_soc=recipe["initial_soc"], depth_of_discharge=1.0,
        loss_coefficient=0.0, capacity_loss_coefficient=0.0,
        power_efficiency_curve=[[0.0, efficiency], [1.0, efficiency]],
        capacity_power_curve=[[0.0, 1.0], [1.0, 1.0]]))


def build(upstream, recipe_path, output):
    from .backends.citylearn_backend import CityLearnBackend
    upstream, output = Path(upstream).resolve(), Path(output).resolve()
    recipe = load_recipe(recipe_path)
    source = upstream / "data/datasets" / recipe["dataset"]
    source_schema = source / "schema.json"
    revision = subprocess.check_output(["git", "-c", "safe.directory=" + str(upstream),
        "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    if revision != recipe["upstream_commit"]:
        raise ValueError("Wrong upstream revision: checkout " + recipe["upstream_commit"])
    # Reject tracked source/data modifications so the recorded commit is useful.
    dirty = subprocess.check_output(["git", "-c", "safe.directory=" + str(upstream),
        "-C", str(upstream), "status", "--porcelain", "--untracked-files=no"], text=True).strip()
    if dirty:
        raise ValueError("Upstream has tracked changes; use a clean pinned checkout")
    original = json.loads(source_schema.read_text())
    names = [n for n,b in original["buildings"].items() if b.get("include", True)][:recipe["building_count"]]
    if len(names) != recipe["building_count"]:
        raise ValueError("Not enough buildings in source schema")
    output.mkdir(parents=True, exist_ok=True)
    print("Generating fixed PV profiles from the pinned upstream model...", flush=True)
    base = CityLearnBackend(root=str(upstream), schema=str(source_schema))
    originals = base.metadata()
    by_name = {b.name:b for b in base.env.buildings}
    source_hashes = {"schema.json": digest(source_schema)}
    for config_name, spec in recipe["configurations"].items():
        folder = output / config_name
        folder.mkdir(exist_ok=True)
        schema = copy.deepcopy(original)
        schema["root_directory"] = None
        schema["buildings"] = {n:schema["buildings"][n] for n in names}
        manifest = []
        for index, name in enumerate(names):
            row = schema["buildings"][name]
            role = "pv" if index % 2 == 0 else "storage"
            cohort_index = index // 2
            if role == "pv":
                pv, cap, rate = (spec["pv_house_pv_kw"], spec["pv_house_battery_kwh"], spec["pv_house_c_rate"])
            else:
                pv, cap = spec["storage_house_pv_kw"], spec["storage_house_battery_kwh"]
                rate = spec["storage_house_c_rates"][cohort_index % 2]
            frame = pd.read_csv(source / row["energy_simulation"])
            source_hashes[row["energy_simulation"]] = digest(source / row["energy_simulation"])
            # Raw upstream CSVs contain zero PV; autosizing creates the actual
            # W/kW generation profile. Bake that profile, then set explicit kW.
            b = by_name[name]
            profile = b.energy_simulation.__getattr__("solar_generation",
                start_time_step=0, end_time_step=len(frame)-1)
            if len(profile) != len(frame):
                raise ValueError("PV profile length differs from source data")
            if not np.isfinite(profile).all() or np.max(profile) <= 0:
                raise ValueError("PV profile is empty or non-finite")
            frame["solar_generation"] = profile
            frame.to_csv(folder / row["energy_simulation"], index=False)
            for key in ("weather", "district_load_target"):
                if row.get(key):
                    shutil.copy2(source / row[key], folder / row[key])
                    source_hashes[row[key]] = digest(source / row[key])
            model = row["dynamics"]["attributes"]["filename"]
            shutil.copy2(source / model, folder / model)
            source_hashes[model] = digest(source / model)
            row["pv"] = dict(type="citylearn.energy_model.PV", autosize=False,
                             attributes=dict(nominal_power=pv))
            row["electrical_storage"] = battery_spec(cap, rate, recipe)
            manifest.append(dict(building=name, role=role, pv_kw=pv, battery_kwh=cap,
                battery_kw=cap*rate, c_rate=rate, original_pv_kw=originals[name]["pv_kw"],
                original_battery_kwh=originals[name]["battery_kwh"],
                original_battery_kw=originals[name]["battery_kw"],
                potential_evil_owner=cohort_index < recipe["evil_house_count"]))
        (folder / "schema.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")
        pd.DataFrame(manifest).to_csv(folder / "buildings.csv", index=False)
        pd.DataFrame({"electricity_pricing":duck_prices(recipe, base.n_timesteps)}).to_csv(
            folder / "duck_prices.csv", index=False)
        shutil.copy2(upstream / "LICENSE", folder / "UPSTREAM_LICENSE")
        # Relative resource paths only: the directory can move to another machine.
        files = {p.name:digest(p) for p in folder.iterdir() if p.is_file() and p.name != "provenance.json"}
        (folder / "provenance.json").write_text(json.dumps(dict(upstream_commit=revision,
            dataset=recipe["dataset"], recipe=recipe, original_files=source_hashes,
            generated_files=files, retained_buildings=names,
            excluded_buildings=[n for n in original["buildings"] if n not in names]), indent=2), encoding="utf-8")
        print("Built", config_name, "in", folder, flush=True)
    return output


def verify(folder):
    folder = Path(folder)
    provenance = json.loads((folder / "provenance.json").read_text())
    for filename, expected in provenance["generated_files"].items():
        if digest(folder / filename) != expected:
            raise ValueError("Generated file changed: " + filename)
    return provenance
