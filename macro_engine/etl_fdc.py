"""ETL for USDA FoodData Central bulk CSVs -> macros.db.

Usage: .venv/bin/python -m macro_engine.etl_fdc [--datasets foundation sr_legacy survey]

Downloads land in ~/.local/share/macro-engine/raw/ and are kept (re-runs skip
the download). Food ids are stable across re-runs (upsert on source+source_id),
so aliases and log history survive a reload. FTS is rebuilt at the end.
"""

import argparse
import csv
import sys
import urllib.request
import zipfile
from pathlib import Path

from . import db

BASE = "https://fdc.nal.usda.gov/fdc-datasets/"
DATASETS = {
    "foundation": {
        "zip": "FoodData_Central_foundation_food_csv_2026-04-30.zip",
        "source": "fdc_foundation",
        "data_types": {"foundation_food"},
    },
    "sr_legacy": {
        "zip": "FoodData_Central_sr_legacy_food_csv_2018-04.zip",
        "source": "fdc_sr_legacy",
        "data_types": {"sr_legacy_food"},
    },
    "survey": {
        "zip": "FoodData_Central_survey_food_csv_2024-10-31.zip",
        "source": "fdc_survey",
        "data_types": {"survey_fndds_food"},
    },
}

# nutrient_nbr -> our column. kcal preference: 208, else Atwater specific 958,
# else Atwater general 957 (Foundation foods often lack 208).
NBR_MAP = {
    "203": "protein_g",
    "204": "fat_g",
    "205": "carb_g",
    "291": "fiber_g",
    "269": "sugar_g",
    "606": "satfat_g",
    "307": "sodium_mg",
}
KCAL_NBRS = ("208", "958", "957")


def _norm_nbr(raw: str) -> str:
    raw = (raw or "").strip()
    try:
        f = float(raw)
        return str(int(f)) if f == int(f) else raw
    except ValueError:
        return raw


def _download(name: str, raw_dir: Path) -> Path:
    url = BASE + DATASETS[name]["zip"]
    dest = raw_dir / DATASETS[name]["zip"]
    if dest.exists() and dest.stat().st_size > 0:
        print(f"[{name}] using cached {dest.name}")
        return dest
    print(f"[{name}] downloading {url}")
    tmp = dest.with_suffix(".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(dest)
    return dest


def _extract(zip_path: Path, raw_dir: Path) -> Path:
    """Extract and return the directory containing food.csv."""
    out = raw_dir / zip_path.stem
    marker = out / ".extracted"
    if not marker.exists():
        out.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(out)
        marker.touch()
    hits = sorted(out.rglob("food.csv"))
    if not hits:
        raise FileNotFoundError(f"no food.csv inside {zip_path}")
    return hits[0].parent


def _read_csv(path: Path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        yield from csv.DictReader(f)


def load_dataset(con, name: str, raw_dir: Path) -> int:
    cfg = DATASETS[name]
    folder = _extract(_download(name, raw_dir), raw_dir)

    # Which nutrient ids do we care about, and what do they map to?
    nutrient_col = {}
    for row in _read_csv(folder / "nutrient.csv"):
        nbr = _norm_nbr(row.get("nutrient_nbr", ""))
        if nbr in NBR_MAP:
            nutrient_col[row["id"]] = NBR_MAP[nbr]
        elif nbr in KCAL_NBRS:
            nutrient_col[row["id"]] = f"kcal:{nbr}"

    wanted_ids = set()
    for row in _read_csv(folder / "food.csv"):
        if row.get("data_type") in cfg["data_types"]:
            wanted_ids.add(row["fdc_id"])

    # Accumulate per-food nutrient values (files are per-100g)
    nutrients: dict[str, dict] = {}
    for row in _read_csv(folder / "food_nutrient.csv"):
        fdc_id = row["fdc_id"]
        col = nutrient_col.get(row["nutrient_id"])
        if col is None or fdc_id not in wanted_ids:
            continue
        try:
            amount = float(row["amount"])
        except (ValueError, TypeError):
            continue
        nutrients.setdefault(fdc_id, {})[col] = amount

    units = {}
    mu = folder / "measure_unit.csv"
    if mu.exists():
        units = {r["id"]: r["name"] for r in _read_csv(mu)}

    portions: dict[str, list] = {}
    fp = folder / "food_portion.csv"
    if fp.exists():
        for row in _read_csv(fp):
            fdc_id = row["fdc_id"]
            if fdc_id not in wanted_ids:
                continue
            try:
                grams = float(row["gram_weight"])
            except (ValueError, TypeError):
                continue
            desc = (row.get("portion_description") or "").strip()
            if desc.lower() == "quantity not specified":
                continue
            if not desc:
                amount = (row.get("amount") or "").strip()
                unit = units.get(row.get("measure_unit_id", ""), "")
                unit = "" if unit == "undetermined" else unit
                modifier = (row.get("modifier") or "").strip()
                desc = " ".join(p for p in (amount, unit, modifier) if p)
            if desc:
                portions.setdefault(fdc_id, []).append((desc, grams))

    count = 0
    for row in _read_csv(folder / "food.csv"):
        if row.get("data_type") not in cfg["data_types"]:
            continue
        fdc_id = row["fdc_id"]
        n = nutrients.get(fdc_id, {})
        kcal = next((n[f"kcal:{nbr}"] for nbr in KCAL_NBRS if f"kcal:{nbr}" in n), None)
        if kcal is None and not any(k in n for k in ("protein_g", "fat_g", "carb_g")):
            continue  # nothing useful
        food_id = con.execute(
            """INSERT INTO foods (name, brand, source, source_id)
               VALUES (?, NULL, ?, ?)
               ON CONFLICT(source, source_id) DO UPDATE SET name = excluded.name
               RETURNING id""",
            (row["description"].strip(), cfg["source"], fdc_id),
        ).fetchone()[0]
        con.execute(
            """INSERT OR REPLACE INTO food_nutrients
               (food_id, kcal, protein_g, carb_g, fat_g, fiber_g, sugar_g,
                satfat_g, sodium_mg)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (food_id, kcal, n.get("protein_g"), n.get("carb_g"), n.get("fat_g"),
             n.get("fiber_g"), n.get("sugar_g"), n.get("satfat_g"),
             n.get("sodium_mg")),
        )
        con.execute("DELETE FROM portions WHERE food_id = ?", (food_id,))
        con.executemany(
            "INSERT INTO portions (food_id, label, grams) VALUES (?, ?, ?)",
            [(food_id, label, grams) for label, grams in portions.get(fdc_id, [])],
        )
        count += 1
    con.commit()
    print(f"[{name}] loaded {count} foods")
    return count


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datasets", nargs="+", default=list(DATASETS),
                    choices=list(DATASETS))
    ap.add_argument("--db", default=None)
    ap.add_argument("--raw-dir", default=None)
    args = ap.parse_args(argv)

    raw_dir = Path(args.raw_dir) if args.raw_dir else \
        db.default_db_path().parent / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    con = db.connect(args.db)
    csv.field_size_limit(sys.maxsize)

    total = sum(load_dataset(con, name, raw_dir) for name in args.datasets)
    db.rebuild_fts(con)
    print(f"done: {total} foods loaded, FTS rebuilt")


if __name__ == "__main__":
    main()
