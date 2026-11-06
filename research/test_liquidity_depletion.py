"""Hand-computed Level 1 depletion, replenishment, and stream parity tests."""

from collections import namedtuple
import json
from pathlib import Path
import tempfile
import unittest
import pandas as pd

from strategies.liquidity_depletion import LiquidityStream, offline_features
from strategies.liquidity_predictor import LiquidityPredictor
from strategies.orderflow_imbalance import Book

Trade = namedtuple("Trade", "symbol aggressor price qty")


class LiquidityTest(unittest.TestCase):
    def test_same_price_depletion_and_trade_alignment(self):
        stream = LiquidityStream((2,))
        stream.update("X", 0, Book(100, 10, 102, 20))
        trades = (Trade("X", "SELL", 100, 4), Trade("X", "BUY", 102, 5))
        result = stream.update("X", 1, Book(100, 6, 102, 15), trades)
        v = result.values
        self.assertEqual((v["bid_depletion"], v["ask_depletion"]), (4, 5))
        self.assertAlmostEqual(v["bid_relative_depletion"], .4)
        self.assertAlmostEqual(v["ask_relative_depletion"], .25)
        self.assertEqual(v["bid_depletion_per_second"], 8)
        self.assertEqual(v["bid_aligned_trade_qty"], 4)
        self.assertEqual(v["ask_aligned_trade_qty"], 5)
        self.assertEqual(v["bid_no_trade_depletion"], 0)
        self.assertEqual(v["ask_decrease_streak"], 1)
        next_value = stream.update("X", 2, Book(101, 9, 102, 20)).values
        self.assertEqual(next_value["bid_eligible"], 0)
        self.assertEqual(next_value["bid_depletion"], 0)
        self.assertEqual(next_value["bid_decrease_streak"], 0)
        self.assertEqual(next_value["ask_replenishment"], 5)
        self.assertEqual(next_value["ask_cycle"], 1)
        self.assertEqual(next_value["ask_replenishment_speed_s"], .5)
        self.assertAlmostEqual(next_value["ask_dep_intensity_2"], .125)

    def test_persistence_requires_five_later_batches(self):
        stream = LiquidityStream((2,))
        stream.update("X", 0, Book(100, 10, 102, 20))
        stream.update("X", 1, Book(100, 10, 102, 10))
        stream.update("X", 2, Book(100, 10, 102, 20))
        for b in range(3, 7):
            self.assertIsNone(stream.update("X", b, Book(100, 10, 102, 20))
                              .values["ask_replenishment_persistence"])
        self.assertEqual(stream.update("X", 7, Book(100, 10, 102, 20))
                         .values["ask_replenishment_persistence"], 1)

    def test_no_trade_drop_and_resets(self):
        stream = LiquidityStream((2,))
        stream.update("X", 0, Book(100, 10, 102, 20))
        self.assertEqual(stream.update("X", 1, Book(100, 5, 102, 20))
                         .values["bid_no_trade_depletion"], 1)
        self.assertEqual(stream.update("X", 2, Book(100, 1, 102, 20))
                         .values["bid_decrease_streak"], 2)
        self.assertEqual(stream.update("X", 4, Book(100, 5, 102, 20)).quality, "warmup")
        self.assertEqual(stream.update("X", 5, Book(100, 5, 102, 20), own_touch=True).quality,
                         "own_touch")
        self.assertEqual(stream.update("X", 6, Book(100, 5, 102, 20)).quality, "warmup")
        self.assertEqual(stream.update("X", 7, Book(100, 0, 102, 20)).quality, "invalid")
        self.assertEqual(stream.update("X", 8, Book(100, 5, 102, 20, "HALTED")).quality, "invalid")

    def test_offline_streaming_parity(self):
        rows = [(s, b, Book(100, 10+b, 102, 20-b), ())
                for b in range(8) for s in ("X", "Y")]
        offline = offline_features(rows, windows=(2, 5))
        stream = LiquidityStream((2, 5))
        live = [stream.update(*row) for row in rows]
        self.assertEqual(offline, live)

    def test_archived_batch_arithmetic_matches_independent_calculation(self):
        path = Path(__file__).resolve().parents[1] / "data" / "s01.top.csv"
        block = pd.read_csv(path).query("symbol == 'HPPO'").head(100).reset_index(drop=True)
        stream = LiquidityStream((2, 5))
        for index, row in block.iterrows():
            book = Book(int(row.bid_px), int(row.bid_qty), int(row.ask_px), int(row.ask_qty))
            result = stream.update("HPPO", int(row.batch), book)
            if index == 0:
                self.assertEqual(result.quality, "warmup")
                continue
            previous = block.iloc[index-1]
            for side in ("bid", "ask"):
                same = row[f"{side}_px"] == previous[f"{side}_px"]
                expected_dep = max(int(previous[f"{side}_qty"] - row[f"{side}_qty"]), 0) if same else 0
                expected_repl = max(int(row[f"{side}_qty"] - previous[f"{side}_qty"]), 0) if same else 0
                self.assertEqual(result.values[f"{side}_depletion"], expected_dep)
                self.assertEqual(result.values[f"{side}_replenishment"], expected_repl)

    def test_predictor_quality_and_finite_output(self):
        bundle = {"version": "liquidity-depletion-v1",
                  "feature_order": ["ofi_norm40", "micro_dev", "imbalance",
                                    "momentum_10s", "net_dep_40"],
                  "symbols": {"X": {"mean": [0]*5, "std": [1]*5,
                                     "events": {"next_ask_up": {"plus_coefficients": [0]*6},
                                                "next_bid_down": {"plus_coefficients": [0]*6}},
                                     "return_60s": {"plus_coefficients": [0, 0, 0, 0, 0, 2],
                                                    "train_residual_rmse_cents": 3}}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.json"
            path.write_text(json.dumps(bundle))
            predictor = LiquidityPredictor(path)
            values = dict(zip(bundle["feature_order"], [0, 0, 0, 0, .25]))
            result = predictor.predict("X", values)
            self.assertEqual(result["quality"], "valid")
            self.assertEqual(result["p_ask_up_next_batch"], .5)
            self.assertEqual(result["predicted_return_60s_cents"], .5)
            self.assertEqual(result["uncertainty_60s_cents"], 3)
            self.assertEqual(predictor.predict("X", values, quality="own_touch")["quality"],
                             "own_touch")
            self.assertEqual(predictor.predict("X", {})["quality"], "missing_feature")


if __name__ == "__main__":
    unittest.main()
