import unittest

import pandas as pd

from backtest import simulate, summarize


class BacktestTests(unittest.TestCase):
    def test_summary_empty(self):
        result = summarize("0000", [])
        self.assertEqual(result["trades"], 0)
        self.assertEqual(result["total_return"], 0)

    def test_simulation_does_not_overlap(self):
        index = pd.date_range("2025-01-01", periods=150, freq="B")
        close = [100 + i for i in range(150)]
        frame = pd.DataFrame({"Open": close, "Close": close, "Volume": [2000] * 150}, index=index)
        trades = simulate(frame, hold_days=10)
        for previous, current in zip(trades, trades[1:]):
            self.assertGreater(pd.Timestamp(current.entry_date), pd.Timestamp(previous.exit_date))


if __name__ == "__main__":
    unittest.main()
