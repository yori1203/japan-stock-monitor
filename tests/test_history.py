import csv
import tempfile
import unittest
from pathlib import Path

from history import FIELDS, append_unique, migrate_history


class HistoryTests(unittest.TestCase):
    def test_legacy_history_is_preserved_and_backed_up(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "signals.csv"
            legacy = "date,code,price,score,signal,confidence,rsi\n2026-08-25 11:39,6740,47.0,42,hold,low,53.8\n"
            path.write_text(legacy, encoding="utf-8")
            self.assertEqual(migrate_history(path), 1)
            self.assertEqual(path.with_suffix(".csv.v1.bak").read_text(encoding="utf-8"), legacy)
            with path.open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["code"], "6740")
            self.assertEqual(list(rows[0]), FIELDS)

    def test_duplicate_run_is_not_appended(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "signals.csv"
            row = {"date": "2026-09-03 08:30", "code": "6740", "price": 46,
                   "score": 30, "signal": "stop", "confidence": "中", "rsi": 35,
                   "session": "morning", "category": "portfolio", "run_id": "20260903-morning"}
            self.assertEqual(append_unique(path, [row]), 1)
            self.assertEqual(append_unique(path, [row]), 0)


if __name__ == "__main__":
    unittest.main()
