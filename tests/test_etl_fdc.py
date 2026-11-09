"""Tests for parse/transform helpers and load_dataset() in macro_engine/etl_fdc.py.

We do NOT attempt a live USDA download.  Instead:
 - Pure helpers (_norm_nbr, _read_csv, _extract) are tested with tiny fixtures.
 - load_dataset() is tested by pre-building a minimal fake zip at the path
   that _download() would cache to — seeing the file exists, _download() skips
   the HTTP call and returns the local path.
"""

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_engine import db, etl_fdc


# ─────────────────────────────────────────────────────────────────────────────
#  _norm_nbr
# ─────────────────────────────────────────────────────────────────────────────

class TestNormNbr(unittest.TestCase):
    """_norm_nbr normalises a raw nutrient-number string."""

    def test_integer_string_unchanged(self):
        self.assertEqual(etl_fdc._norm_nbr("203"), "203")

    def test_float_with_zero_fraction_becomes_int(self):
        self.assertEqual(etl_fdc._norm_nbr("203.0"), "203")

    def test_float_with_nonzero_fraction_unchanged(self):
        self.assertEqual(etl_fdc._norm_nbr("203.5"), "203.5")

    def test_empty_string(self):
        self.assertEqual(etl_fdc._norm_nbr(""), "")

    def test_none_returns_empty(self):
        self.assertEqual(etl_fdc._norm_nbr(None), "")

    def test_non_numeric_passthrough(self):
        self.assertEqual(etl_fdc._norm_nbr("abc"), "abc")

    def test_whitespace_stripped(self):
        # "  203  " → float 203.0 → int → "203"
        self.assertEqual(etl_fdc._norm_nbr("  203  "), "203")

    def test_large_integer_float(self):
        self.assertEqual(etl_fdc._norm_nbr("100000.0"), "100000")

    def test_zero(self):
        self.assertEqual(etl_fdc._norm_nbr("0"), "0")

    def test_zero_float(self):
        self.assertEqual(etl_fdc._norm_nbr("0.0"), "0")


# ─────────────────────────────────────────────────────────────────────────────
#  _read_csv
# ─────────────────────────────────────────────────────────────────────────────

class TestReadCsv(unittest.TestCase):
    """_read_csv yields DictReader rows from a UTF-8-sig CSV path."""

    def _make_csv(self, content: str) -> Path:
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False,
            encoding="utf-8-sig", newline="")
        f.write(content)
        f.close()
        return Path(f.name)

    def test_reads_header_and_rows(self):
        p = self._make_csv("fdc_id,description\n1001,Bananas raw\n1002,Eggs\n")
        try:
            rows = list(etl_fdc._read_csv(p))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["fdc_id"], "1001")
            self.assertEqual(rows[1]["description"], "Eggs")
        finally:
            os.unlink(p)

    def test_empty_csv_yields_no_rows(self):
        p = self._make_csv("fdc_id,description\n")
        try:
            rows = list(etl_fdc._read_csv(p))
            self.assertEqual(rows, [])
        finally:
            os.unlink(p)

    def test_strips_utf8_bom(self):
        p = self._make_csv("id,name\n1,Apple\n")
        try:
            rows = list(etl_fdc._read_csv(p))
            self.assertIn("id", rows[0])
            self.assertNotIn("﻿id", rows[0])
        finally:
            os.unlink(p)

    def test_multiple_columns(self):
        p = self._make_csv(
            "fdc_id,data_type,description\n"
            "100,foundation_food,Apple\n"
            "200,sr_legacy_food,Banana\n")
        try:
            rows = list(etl_fdc._read_csv(p))
            self.assertEqual(rows[0]["data_type"], "foundation_food")
            self.assertEqual(rows[1]["data_type"], "sr_legacy_food")
        finally:
            os.unlink(p)


# ─────────────────────────────────────────────────────────────────────────────
#  _extract
# ─────────────────────────────────────────────────────────────────────────────

class TestExtract(unittest.TestCase):
    """_extract unpacks a zip and returns the directory containing food.csv."""

    def _make_zip(self, raw_dir: Path, name: str,
                  contents: dict) -> Path:
        zip_path = raw_dir / f"{name}.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            for member, text in contents.items():
                z.writestr(member, text)
        return zip_path

    def test_returns_directory_with_food_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            zip_path = self._make_zip(
                raw, "testdata",
                {"FoodData/food.csv": "fdc_id,description\n1,Apple\n"})
            result = etl_fdc._extract(zip_path, raw)
            self.assertTrue((result / "food.csv").exists())

    def test_raises_if_no_food_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            zip_path = self._make_zip(raw, "nocsv", {"other.txt": "nothing\n"})
            with self.assertRaises(FileNotFoundError):
                etl_fdc._extract(zip_path, raw)

    def test_nested_food_csv_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            zip_path = self._make_zip(
                raw, "nested",
                {"a/b/c/food.csv": "fdc_id,description\n2,Banana\n"})
            result = etl_fdc._extract(zip_path, raw)
            self.assertTrue((result / "food.csv").exists())
            rows = list(etl_fdc._read_csv(result / "food.csv"))
            self.assertEqual(rows[0]["description"], "Banana")

    def test_cached_extraction_skips_unzip(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            zip_path = raw / "cached.zip"
            with zipfile.ZipFile(zip_path, "w"):
                pass  # empty zip

            # Simulate a previously extracted directory
            out_dir = raw / "cached"
            out_dir.mkdir()
            (out_dir / ".extracted").touch()
            (out_dir / "food.csv").write_text("fdc_id,description\n99,Cached\n")

            result = etl_fdc._extract(zip_path, raw)
            self.assertEqual(result, out_dir)

    def test_returns_parent_of_food_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            zip_path = self._make_zip(
                raw, "flat",
                {"food.csv": "fdc_id,description\n3,Cherry\n"})
            result = etl_fdc._extract(zip_path, raw)
            self.assertTrue(result.is_dir())
            self.assertTrue((result / "food.csv").is_file())


# ─────────────────────────────────────────────────────────────────────────────
#  NBR_MAP / KCAL_NBRS / DATASETS constants
# ─────────────────────────────────────────────────────────────────────────────

class TestNbrMapConstants(unittest.TestCase):

    def test_nbrmap_contains_protein(self):
        self.assertIn("203", etl_fdc.NBR_MAP)
        self.assertEqual(etl_fdc.NBR_MAP["203"], "protein_g")

    def test_kcal_nbrs_preference_order(self):
        self.assertEqual(etl_fdc.KCAL_NBRS[0], "208")

    def test_datasets_dict_has_expected_keys(self):
        self.assertIn("foundation", etl_fdc.DATASETS)
        self.assertIn("sr_legacy", etl_fdc.DATASETS)
        self.assertIn("survey", etl_fdc.DATASETS)


# ─────────────────────────────────────────────────────────────────────────────
#  load_dataset() — end-to-end with tiny fake USDA CSVs
#
#  Strategy: create a valid zip at the path _download() would cache to.
#  _download() sees the file and skips the HTTP call; _extract() unpacks it;
#  load_dataset() processes the fake rows.
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadDataset(unittest.TestCase):

    # Cache-path name for the foundation zip (must match DATASETS)
    FOUNDATION_ZIP = etl_fdc.DATASETS["foundation"]["zip"]
    SR_LEGACY_ZIP  = etl_fdc.DATASETS["sr_legacy"]["zip"]
    SURVEY_ZIP     = etl_fdc.DATASETS["survey"]["zip"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw_dir = Path(self.tmp.name) / "raw"
        self.raw_dir.mkdir()
        self.con = db.connect(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    # ── helpers ──────────────────────────────────────────────────────────── #

    def _place_zip(self, zip_name: str, members: dict) -> None:
        """Write a valid zip at raw_dir/zip_name with members={name: content}."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for member, content in members.items():
                z.writestr(member, content)
        (self.raw_dir / zip_name).write_bytes(buf.getvalue())

    def _foundation_zip(self, food_csv: str, nutrient_csv: str,
                        food_nutrient_csv: str,
                        measure_unit_csv: str = None,
                        food_portion_csv: str = None) -> None:
        members = {
            "food.csv": food_csv,
            "nutrient.csv": nutrient_csv,
            "food_nutrient.csv": food_nutrient_csv,
        }
        if measure_unit_csv is not None:
            members["measure_unit.csv"] = measure_unit_csv
        if food_portion_csv is not None:
            members["food_portion.csv"] = food_portion_csv
        self._place_zip(self.FOUNDATION_ZIP, members)

    # ── tests ────────────────────────────────────────────────────────────── #

    def test_happy_path_inserts_food_with_macros(self):
        self._foundation_zip(
            food_csv="fdc_id,data_type,description\n1001,foundation_food,Apple\n",
            nutrient_csv="id,nutrient_nbr\n1,208\n2,203\n3,204\n4,205\n",
            food_nutrient_csv=(
                "fdc_id,nutrient_id,amount\n"
                "1001,1,52\n1001,2,0.3\n1001,3,0.2\n1001,4,14\n"),
        )
        count = etl_fdc.load_dataset(self.con, "foundation", self.raw_dir)
        self.assertEqual(count, 1)
        row = self.con.execute(
            "SELECT * FROM foods WHERE source='fdc_foundation'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "Apple")
        nutr = self.con.execute(
            "SELECT * FROM food_nutrients WHERE food_id=?", (row["id"],)).fetchone()
        self.assertAlmostEqual(nutr["kcal"], 52)
        self.assertAlmostEqual(nutr["protein_g"], 0.3, places=5)

    def test_skips_food_with_no_macros(self):
        """A row with no nutrient entries and no kcal must be skipped."""
        self._foundation_zip(
            food_csv="fdc_id,data_type,description\n1002,foundation_food,NoMacros\n",
            nutrient_csv="id,nutrient_nbr\n1,208\n",
            food_nutrient_csv="fdc_id,nutrient_id,amount\n",
        )
        count = etl_fdc.load_dataset(self.con, "foundation", self.raw_dir)
        self.assertEqual(count, 0)

    def test_skips_wrong_data_type(self):
        """A foundation load must ignore rows whose data_type != foundation_food."""
        self._foundation_zip(
            food_csv="fdc_id,data_type,description\n1003,sr_legacy_food,SRL\n",
            nutrient_csv="id,nutrient_nbr\n1,208\n",
            food_nutrient_csv="fdc_id,nutrient_id,amount\n1003,1,100\n",
        )
        count = etl_fdc.load_dataset(self.con, "foundation", self.raw_dir)
        self.assertEqual(count, 0)

    def test_portions_inserted(self):
        """A food_portion.csv row with gram_weight creates a portions row."""
        self._foundation_zip(
            food_csv="fdc_id,data_type,description\n1004,foundation_food,Banana\n",
            nutrient_csv="id,nutrient_nbr\n1,208\n",
            food_nutrient_csv="fdc_id,nutrient_id,amount\n1004,1,89\n",
            measure_unit_csv="id,name\n1,gram\n2,medium\n",
            food_portion_csv=(
                "id,fdc_id,seq_num,amount,measure_unit_id,portion_description,"
                "modifier,gram_weight,data_points\n"
                "10,1004,1,1,2,,medium,118,5\n"),
        )
        etl_fdc.load_dataset(self.con, "foundation", self.raw_dir)
        fid = self.con.execute(
            "SELECT id FROM foods WHERE source_id='1004'").fetchone()[0]
        portions = self.con.execute(
            "SELECT * FROM portions WHERE food_id=?", (fid,)).fetchall()
        self.assertEqual(len(portions), 1)
        self.assertAlmostEqual(portions[0]["grams"], 118)

    def test_portion_description_built_from_parts(self):
        """When portion_description is blank, desc is assembled from amount+unit+modifier."""
        self._foundation_zip(
            food_csv="fdc_id,data_type,description\n1010,foundation_food,Rice\n",
            nutrient_csv="id,nutrient_nbr\n1,208\n",
            food_nutrient_csv="fdc_id,nutrient_id,amount\n1010,1,130\n",
            measure_unit_csv="id,name\n3,cup\n",
            food_portion_csv=(
                "id,fdc_id,seq_num,amount,measure_unit_id,portion_description,"
                "modifier,gram_weight,data_points\n"
                "30,1010,1,1,3,,cooked,186,0\n"),
        )
        etl_fdc.load_dataset(self.con, "foundation", self.raw_dir)
        fid = self.con.execute(
            "SELECT id FROM foods WHERE source_id='1010'").fetchone()[0]
        portions = self.con.execute(
            "SELECT label FROM portions WHERE food_id=?", (fid,)).fetchall()
        # label must contain unit and/or modifier
        self.assertEqual(len(portions), 1)
        self.assertIn("cooked", portions[0]["label"])

    def test_quantity_not_specified_portion_skipped(self):
        self._foundation_zip(
            food_csv="fdc_id,data_type,description\n1005,foundation_food,Cherry\n",
            nutrient_csv="id,nutrient_nbr\n1,208\n",
            food_nutrient_csv="fdc_id,nutrient_id,amount\n1005,1,63\n",
            measure_unit_csv="id,name\n1,gram\n",
            food_portion_csv=(
                "id,fdc_id,seq_num,amount,measure_unit_id,portion_description,"
                "modifier,gram_weight,data_points\n"
                "20,1005,1,1,1,Quantity not specified,,100,0\n"),
        )
        etl_fdc.load_dataset(self.con, "foundation", self.raw_dir)
        fid = self.con.execute(
            "SELECT id FROM foods WHERE source_id='1005'").fetchone()[0]
        portions = self.con.execute(
            "SELECT * FROM portions WHERE food_id=?", (fid,)).fetchall()
        self.assertEqual(len(portions), 0)

    def test_undetermined_unit_omitted_from_label(self):
        """A measure_unit of 'undetermined' must not appear in the portion label."""
        self._foundation_zip(
            food_csv="fdc_id,data_type,description\n1011,foundation_food,Oats\n",
            nutrient_csv="id,nutrient_nbr\n1,208\n",
            food_nutrient_csv="fdc_id,nutrient_id,amount\n1011,1,389\n",
            measure_unit_csv="id,name\n9,undetermined\n",
            food_portion_csv=(
                "id,fdc_id,seq_num,amount,measure_unit_id,portion_description,"
                "modifier,gram_weight,data_points\n"
                "40,1011,1,1,9,,dry,40,0\n"),
        )
        etl_fdc.load_dataset(self.con, "foundation", self.raw_dir)
        fid = self.con.execute(
            "SELECT id FROM foods WHERE source_id='1011'").fetchone()[0]
        portions = self.con.execute(
            "SELECT label FROM portions WHERE food_id=?", (fid,)).fetchall()
        self.assertEqual(len(portions), 1)
        self.assertNotIn("undetermined", portions[0]["label"])

    def test_rerun_preserves_food_id(self):
        """Upsert on source+source_id must not change food_id on re-run."""
        self._foundation_zip(
            food_csv="fdc_id,data_type,description\n1006,foundation_food,Grape\n",
            nutrient_csv="id,nutrient_nbr\n1,208\n",
            food_nutrient_csv="fdc_id,nutrient_id,amount\n1006,1,69\n",
        )
        etl_fdc.load_dataset(self.con, "foundation", self.raw_dir)
        fid1 = self.con.execute(
            "SELECT id FROM foods WHERE source_id='1006'").fetchone()[0]

        etl_fdc.load_dataset(self.con, "foundation", self.raw_dir)
        fid2 = self.con.execute(
            "SELECT id FROM foods WHERE source_id='1006'").fetchone()[0]
        self.assertEqual(fid1, fid2)

    def test_kcal_fallback_to_atwater_958(self):
        """When nutrient 208 absent, fall back to Atwater specific (958)."""
        self._foundation_zip(
            food_csv="fdc_id,data_type,description\n1007,foundation_food,Tofu\n",
            nutrient_csv="id,nutrient_nbr\n1,958\n2,203\n",
            food_nutrient_csv=(
                "fdc_id,nutrient_id,amount\n"
                "1007,1,76\n1007,2,8\n"),
        )
        etl_fdc.load_dataset(self.con, "foundation", self.raw_dir)
        nutr = self.con.execute(
            "SELECT fn.* FROM food_nutrients fn "
            "JOIN foods f ON f.id=fn.food_id WHERE f.source_id='1007'").fetchone()
        self.assertAlmostEqual(nutr["kcal"], 76)

    def test_invalid_amount_skipped_gracefully(self):
        """A non-numeric amount in food_nutrient.csv must not crash the ETL."""
        self._foundation_zip(
            food_csv="fdc_id,data_type,description\n1008,foundation_food,Widget\n",
            nutrient_csv="id,nutrient_nbr\n1,208\n2,203\n",
            food_nutrient_csv=(
                "fdc_id,nutrient_id,amount\n"
                "1008,1,abc\n"    # bad — skip
                "1008,2,5.5\n"), # good — include
        )
        # protein exists, no kcal; food still has protein so it's not skipped
        count = etl_fdc.load_dataset(self.con, "foundation", self.raw_dir)
        self.assertEqual(count, 1)
        nutr = self.con.execute(
            "SELECT fn.* FROM food_nutrients fn "
            "JOIN foods f ON f.id=fn.food_id WHERE f.source_id='1008'").fetchone()
        self.assertIsNone(nutr["kcal"])
        self.assertAlmostEqual(nutr["protein_g"], 5.5)

    def test_survey_dataset_uses_nbr_keying(self):
        """FNDDS survey CSVs key by nutrient_nbr directly in nutrient_id column."""
        members = {
            "food.csv": (
                "fdc_id,data_type,description\n"
                "2001,survey_fndds_food,Brown Rice\n"),
            "nutrient.csv": "id,nutrient_nbr\n1,208\n2,203\n",
            # FNDDS: nutrient_id column holds the nutrient number, not the id
            "food_nutrient.csv": (
                "fdc_id,nutrient_id,amount\n"
                "2001,208,216\n2001,203,4.5\n"),
        }
        self._place_zip(self.SURVEY_ZIP, members)
        count = etl_fdc.load_dataset(self.con, "survey", self.raw_dir)
        self.assertEqual(count, 1)
        nutr = self.con.execute(
            "SELECT fn.* FROM food_nutrients fn "
            "JOIN foods f ON f.id=fn.food_id WHERE f.source_id='2001'").fetchone()
        self.assertAlmostEqual(nutr["kcal"], 216)
        self.assertAlmostEqual(nutr["protein_g"], 4.5)

    def test_sr_legacy_dataset(self):
        """load_dataset() also works for sr_legacy data_type."""
        members = {
            "food.csv": (
                "fdc_id,data_type,description\n"
                "3001,sr_legacy_food,Chicken breast\n"),
            "nutrient.csv": "id,nutrient_nbr\n1,208\n2,203\n",
            "food_nutrient.csv": (
                "fdc_id,nutrient_id,amount\n"
                "3001,1,165\n3001,2,31\n"),
        }
        self._place_zip(self.SR_LEGACY_ZIP, members)
        count = etl_fdc.load_dataset(self.con, "sr_legacy", self.raw_dir)
        self.assertEqual(count, 1)
        row = self.con.execute(
            "SELECT * FROM foods WHERE source_id='3001'").fetchone()
        self.assertEqual(row["name"], "Chicken breast")
        self.assertEqual(row["source"], "fdc_sr_legacy")

    def test_portion_without_measure_unit_file(self):
        """load_dataset() with no measure_unit.csv must not crash."""
        self._foundation_zip(
            food_csv="fdc_id,data_type,description\n1009,foundation_food,Walnut\n",
            nutrient_csv="id,nutrient_nbr\n1,208\n",
            food_nutrient_csv="fdc_id,nutrient_id,amount\n1009,1,654\n",
            # No measure_unit_csv, no food_portion_csv
        )
        count = etl_fdc.load_dataset(self.con, "foundation", self.raw_dir)
        self.assertEqual(count, 1)

    def test_returns_count_of_loaded_foods(self):
        """Return value equals the number of foods inserted/updated."""
        self._foundation_zip(
            food_csv=(
                "fdc_id,data_type,description\n"
                "4001,foundation_food,FoodA\n"
                "4002,foundation_food,FoodB\n"
                "4003,foundation_food,FoodC\n"),
            nutrient_csv="id,nutrient_nbr\n1,208\n",
            food_nutrient_csv=(
                "fdc_id,nutrient_id,amount\n"
                "4001,1,100\n4002,1,200\n4003,1,300\n"),
        )
        count = etl_fdc.load_dataset(self.con, "foundation", self.raw_dir)
        self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
