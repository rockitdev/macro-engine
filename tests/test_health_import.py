import tempfile
import unittest
from pathlib import Path

from macro_engine import coach, db, health_import


def payload(qty=80, date="2026-11-09 07:30:00 -0400", name="weight", units="kg"):
    return {"data": {"metrics": [{"name": name, "units": units,
                                "data": [{"qty": qty, "date": date}]}]}}


class HealthImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_units_local_date_and_separate_body_fat_batch(self):
        health_import.import_metrics(self.con, payload(176.3696, "2026-11-08 23:30:00 -0400", units="lb"))
        health_import.import_metrics(self.con, payload(22.5, "2026-11-08 23:30:00 -0400", "body_fat_percentage", "%"))
        row = self.con.execute("SELECT * FROM body_metrics").fetchone()
        self.assertEqual(row["date"], "2026-11-08")
        self.assertEqual(row["weight_kg"], 80)
        self.assertEqual(row["body_fat_pct"], 22.5)

    def test_retries_and_out_of_order_uploads(self):
        first = health_import.import_metrics(self.con, payload())
        self.assertEqual(first["updated_fields"], 1)
        self.assertEqual(health_import.import_metrics(self.con, payload())["skipped_fields"], 1)
        health_import.import_metrics(self.con, payload(81, "2026-11-09T12:00:00Z"))
        self.assertEqual(health_import.import_metrics(self.con, payload())["skipped_fields"], 1)
        row = self.con.execute("SELECT * FROM body_metrics").fetchone()
        self.assertEqual(row["weight_kg"], 81)
        self.assertEqual(self.con.execute("SELECT count(*) FROM body_metrics").fetchone()[0], 1)
        self.assertIsNotNone(health_import.status(self.con)["last_sync_at"])

    def test_unsorted_samples_select_latest(self):
        data = payload(81, "2026-11-09 08:30:00 -0400")
        data["data"]["metrics"][0]["data"].append({"qty": 80, "date": "2026-11-09 07:30:00 -0400"})
        health_import.import_metrics(self.con, data)
        self.assertEqual(self.con.execute("SELECT weight_kg FROM body_metrics").fetchone()[0], 81)

    def test_manual_entries_and_other_fields_are_preserved(self):
        coach.log_metrics(self.con, "2026-11-09", weight=79, waist=90, note="manual")
        result = health_import.import_metrics(self.con, payload())
        self.assertEqual(result["preserved_manual_fields"], 1)
        health_import.import_metrics(self.con, payload(22, name="body_fat_percentage", units="%"))
        row = self.con.execute("SELECT * FROM body_metrics").fetchone()
        self.assertEqual((row["weight_kg"], row["waist_cm"], row["note"], row["body_fat_pct"]),
                         (79, 90, "manual", 22))

    def test_manual_correction_and_deletion_survive_new_upload(self):
        health_import.import_metrics(self.con, payload())
        coach.log_metrics(self.con, "2026-11-09", weight=79)
        result = health_import.import_metrics(self.con, payload(81, "2026-11-09 08:30:00 -0400"))
        self.assertEqual(result["preserved_manual_fields"], 1)
        coach.delete_metrics(self.con, "2026-11-09")
        health_import.import_metrics(self.con, payload(81, "2026-11-09 08:30:00 -0400"))
        self.assertEqual(self.con.execute("SELECT count(*) FROM body_metrics").fetchone()[0], 0)

    def test_invalid_batches_do_not_partially_write(self):
        for invalid in [payload(True), payload(float("nan")), payload(0), payload(units="stone"),
                        payload(date="2026-11-09"), payload(date="garbage"),
                        payload(0.25, name="body_fat_percentage", units="%")]:
            with self.subTest(invalid=invalid):
                valid = payload()
                valid["data"]["metrics"].extend(invalid["data"]["metrics"])
                with self.assertRaises(ValueError):
                    health_import.import_metrics(self.con, valid)
                self.assertEqual(self.con.execute("SELECT count(*) FROM body_metrics").fetchone()[0], 0)
                self.assertIsNone(health_import.status(self.con))

    def test_malformed_shape_and_conflicting_sources(self):
        for invalid in [{}, {"data": []}, {"data": {"metrics": [None]}},
                        {"data": {"metrics": [{"name": "weight", "units": "kg", "data": [None]}]}}]:
            with self.assertRaises(ValueError):
                health_import.import_metrics(self.con, invalid)
        data = payload()
        data["data"]["metrics"].extend(payload(81)["data"]["metrics"])
        with self.assertRaisesRegex(ValueError, "conflicting samples"):
            health_import.import_metrics(self.con, data)

    def test_unrelated_metrics_ignored(self):
        result = health_import.import_metrics(self.con, payload(9000, name="step_count", units="count"))
        self.assertEqual(result["ignored_metrics"], 1)
        self.assertEqual(result["updated_fields"], 0)

    def test_shortcut_payload_reuses_units_dates_and_retry_protection(self):
        body = {"metric": "weight", "value": 176.3696, "unit": "lb",
                "date": "2026-11-08T23:30:00-04:00"}
        self.assertEqual(health_import.import_metrics(self.con, body)["updated_fields"], 1)
        self.assertEqual(health_import.import_metrics(self.con, body)["skipped_fields"], 1)
        body.update(metric="body_fat_percentage", value=22.5, unit="%")
        health_import.import_metrics(self.con, body)
        row = self.con.execute("SELECT * FROM body_metrics").fetchone()
        self.assertEqual((row["date"], row["weight_kg"], row["body_fat_pct"]), ("2026-11-08", 80, 22.5))

    def test_shortcut_rejects_missing_unknown_and_invalid_fields(self):
        body = {"metric": "weight", "value": 80, "unit": "kg", "date": "2026-11-09T07:00:00-04:00"}
        for invalid in [{"metric": "weight"}, dict(body, metric="typo"), dict(body, value="80 kg"),
                        dict(body, unit="stone"), dict(body, extra=True), dict(body, date="2026-11-09")]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                health_import.import_metrics(self.con, invalid)
        self.assertEqual(self.con.execute("SELECT count(*) FROM body_metrics").fetchone()[0], 0)

    def test_shortcut_iso8601_timezones_preserve_sample_calendar_date(self):
        for date in ["2026-11-08T23:30:00-04:00", "2026-11-08T23:30:00.123-04:00",
                     "2026-11-08T23:30:00Z", "2026-11-08T00:30:00+05:30"]:
            with self.subTest(date=date):
                samples, _ = health_import._samples({"metric": "weight", "value": 80,
                                                     "unit": "kg", "date": date})
                self.assertEqual(list(samples), [("2026-11-08", "weight_kg")])


if __name__ == "__main__":
    unittest.main()
