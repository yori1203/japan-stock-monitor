import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from v3_edinet_validation import TARGET_CODES, build_report, run


class ValidationReportTests(unittest.TestCase):
    def test_required_codes_are_in_validation_set(self):
        self.assertTrue({"8614", "2317", "4477", "3679", "2492"}.issubset(TARGET_CODES))
        self.assertGreaterEqual(len(TARGET_CODES), 10)
        self.assertLessEqual(len(TARGET_CODES), 20)

    def test_missing_secret_fails_without_exposing_value(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                run("unused.md")

    def test_report_has_required_summary_and_never_accepts_key(self):
        row = {"code":"8614","company_name":"東洋証券","edinet_code":"E00001",
               "document_name":"有価証券報告書","period":"2025-04-01 - 2026-03-31",
               "edinet_status":"ok","yahoo_status":"ok","score":100,"risks":(),"missing":[],
               "matched":6,"warnings":0,"period_mismatch":False,"unit_corrections":1,
               "fields":[{"name":"revenue","edinet":1000,"yahoo":1000,"status":"matched"}]}
        from datetime import datetime, timezone
        report = build_report([row], datetime.now(timezone.utc))
        for text in ("実行日時 JST", "EDINET取得成功数", "Yahoo照合成功数", "crosscheck_score", "risk_flags"):
            self.assertIn(text, report)
        self.assertNotIn("Subscription-Key", report)


if __name__ == "__main__": unittest.main()
