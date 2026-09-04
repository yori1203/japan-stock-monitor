import unittest

import pandas as pd

from strategy import add_indicators, evaluate_frame, evaluate_rows


class StrategyTests(unittest.TestCase):
    def test_indicators_and_decisions_share_frame_path(self):
        index = pd.date_range("2025-01-01", periods=100, freq="B")
        frame = pd.DataFrame({
            "Open": [100 + i * 0.5 for i in range(100)],
            "Close": [100 + i * 0.5 for i in range(100)],
            "Volume": [1000 + (i % 5) * 100 for i in range(100)],
        }, index=index)
        enriched, decisions = evaluate_frame(frame)
        direct = evaluate_rows(enriched.iloc[-1], enriched.iloc[-2])
        self.assertEqual(decisions[-1], direct)
        self.assertGreaterEqual(direct.score, 0)
        self.assertLessEqual(direct.score, 100)

    def test_overbought_exit_precedes_buy(self):
        row = pd.Series({"Close": 120, "MA5": 118, "MA25": 110, "MA75": 100,
                         "RSI": 80, "MACD": 5, "MACD_SIGNAL": 4,
                         "Volume": 2000, "VOL_MA20": 1000})
        decision = evaluate_rows(row)
        self.assertEqual(decision.signal_key, "take_profit")

    def test_missing_columns_are_rejected(self):
        with self.assertRaises(ValueError):
            add_indicators(pd.DataFrame({"Close": [1]}))


if __name__ == "__main__":
    unittest.main()
