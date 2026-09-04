import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

from monitor import configured_stocks, create_report, discover, load_config, report_session


class MonitorTests(unittest.TestCase):
    def test_current_config_shape_is_supported(self):
        config = {"portfolio": [{"code": "6740", "shares": 100, "priority": "high"}],
                  "watchlist": [{"code": "6177", "priority": "high"},
                                {"code": "4583", "priority": "normal"}]}
        stocks = configured_stocks(config)
        self.assertEqual([item["code"] for item in stocks], ["6740", "6177", "4583"])
        self.assertEqual(stocks[0]["shares"], 100)

    def test_repository_config_preserves_portfolio_and_watchlist(self):
        stocks = configured_stocks(load_config("config.json"))
        portfolio = [(item["code"], item["shares"], item["priority"])
                     for item in stocks if item["category"] == "portfolio"]
        watchlist = [(item["code"], item["priority"])
                     for item in stocks if item["category"] == "watchlist"]
        self.assertEqual(portfolio, [
            ("6740", 100, "high"),
            ("6573", 100, "high"),
            ("4596", 100, "high"),
            ("4597", 300, "high"),
        ])
        self.assertEqual(watchlist, [
            ("6177", "high"),
            ("2134", "normal"),
            ("6721", "normal"),
            ("2410", "normal"),
            ("4583", "normal"),
        ])

    def test_session_uses_jst(self):
        jst = ZoneInfo("Asia/Tokyo")
        self.assertEqual(report_session(datetime(2026, 9, 3, 9, 30, tzinfo=jst)), "morning")
        self.assertEqual(report_session(datetime(2026, 9, 3, 12, 30, tzinfo=jst)), "noon")
        self.assertEqual(report_session(datetime(2026, 9, 3, 16, 0, tzinfo=jst)), "evening")

    def test_explicit_noon_session_is_supported(self):
        jst = ZoneInfo("Asia/Tokyo")
        self.assertEqual(report_session(datetime(2026, 9, 3, 9, 0, tzinfo=jst), "noon"), "noon")

    @patch("monitor.analyse")
    def test_discovery_excludes_configured_and_selects_top_scores(self, analyse_mock):
        def result(code, category, priority):
            return {"code": code, "category": category, "priority": priority,
                    "score": {"1111": 60, "2222": 90}[code], "volume_ratio": 1.0}
        analyse_mock.side_effect = result
        config = {"auto_discovery": {"enabled": True, "top_candidates": 1,
                                     "candidate_codes": ["0000", "1111", "2222"]}}
        found, errors = discover(config, {"0000"})
        self.assertEqual([item["code"] for item in found], ["2222"])
        self.assertEqual(errors, [])

    def test_report_marks_daily_data_and_jst_times(self):
        jst = ZoneInfo("Asia/Tokyo")
        timestamp = datetime(2026, 9, 4, 8, 30, tzinfo=jst)
        item = {"code": "6740", "category": "portfolio", "signal": "監視",
                "data_as_of": "2026-09-03", "fetched_at": timestamp, "price": 46,
                "score": 50, "confidence": "低", "rsi": 40, "ma5": 45,
                "ma25": 44, "ma75": 43, "volume_ratio": 1.0, "reasons": ["test"]}
        with TemporaryDirectory() as folder:
            output = Path(folder) / "report.md"
            create_report([item], [], [], generated_at=timestamp, session="morning", output=output)
            text = output.read_text(encoding="utf-8")
        self.assertIn("日足（リアルタイム価格ではありません）", text)
        self.assertIn("データ基準日：2026-09-03", text)
        self.assertIn("データ取得日時（JST）：2026-09-04 08:30:00", text)


if __name__ == "__main__":
    unittest.main()
