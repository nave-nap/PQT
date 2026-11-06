"""Timing checks for Stage 1 market-data features and labels."""

import unittest

import numpy as np
import pandas as pd

from stage1_explore import SYMBOLS, features_for_session


class TimingTest(unittest.TestCase):
    def test_future_changes_do_not_change_present_features(self) -> None:
        n = 130
        rows = []
        for symbol in SYMBOLS:
            for batch in range(n):
                rows.append((batch, symbol, 100 + batch, 10, 102 + batch, 20, "TRADING"))
        top = pd.DataFrame(rows, columns=[
            "batch", "symbol", "bid_px", "bid_qty", "ask_px", "ask_qty", "status"
        ])
        trades = pd.DataFrame([
            (98, "BNKY", 199, 5, "BUY"),
            (101, "BNKY", 202, 50, "SELL"),
        ], columns=["batch", "symbol", "price", "qty", "aggressor"])
        original = features_for_session(top, trades, n)
        changed_top = top.copy()
        changed_top.loc[changed_top.batch >= 100, ["bid_px", "ask_px"]] += 1000
        changed_trades = trades.copy()
        changed_trades.loc[changed_trades.batch >= 100, "qty"] = 5000
        changed = features_for_session(changed_top, changed_trades, n)
        observed = ["mid", "spread", "imbalance", "micro_offset", "flow_10s", "past_return_10s"]
        pd.testing.assert_frame_equal(
            original.loc[original.batch.eq(99), observed].reset_index(drop=True),
            changed.loc[changed.batch.eq(99), observed].reset_index(drop=True),
        )
        bnky = original.loc[original.symbol.eq("BNKY")].set_index("batch")
        self.assertAlmostEqual(bnky.loc[98, "flow_10s"], 1.0)
        self.assertAlmostEqual(bnky.loc[99, "future_1s"], 2.0)
        self.assertTrue(np.isnan(bnky.loc[129, "future_1s"]))
        self.assertAlmostEqual(bnky.loc[0, "delayed_60s"], 120.0)
        self.assertTrue(np.isnan(bnky.loc[9, "delayed_60s"]))


if __name__ == "__main__":
    unittest.main()
