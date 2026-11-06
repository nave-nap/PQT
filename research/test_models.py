"""Validate saved-model inference against offline features and predictions."""

from pathlib import Path
import json
import unittest

import numpy as np

from analyze_signals import load_features
from data_loader import load_session
from evaluate_models import predict_price
from model_data import session_model_rows
from models.inference import FairValuePredictor, observable_features

ROOT = Path(__file__).resolve().parents[1]


class ModelInferenceTest(unittest.TestCase):
    def test_history_features_and_saved_prediction_match_offline_pipeline(self) -> None:
        data_dir = ROOT / "data"
        artifact = ROOT / "models" / "fair_value_v1_selected.json"
        predictor = FairValuePredictor(artifact)
        self.assertEqual(predictor.bundle["train_sessions"],
                         [f"s{i:02d}" for i in range(1, 17)])
        self.assertEqual(predictor.bundle["selection_sessions"],
                         [f"s{i:02d}" for i in range(17, 21)])
        training_bundle = json.loads((ROOT / "models" / "fair_value_v1.json").read_text())
        self.assertEqual(predictor.bundle["models"], training_bundle["models"])
        top, trades = load_session(data_dir, "s01")
        batch = 500
        tops = {int(b): group for b, group in top.loc[top["batch"].between(batch - 240, batch)]
                .groupby("batch")}
        tape = {int(b): group for b, group in trades.loc[trades["batch"].between(batch - 240, batch)]
                .groupby("batch")}
        history = []
        for b in range(batch - 240, batch + 1):
            book = {row.symbol: {"bid_px": row.bid_px, "bid_qty": row.bid_qty,
                                 "ask_px": row.ask_px, "ask_qty": row.ask_qty}
                    for row in tops[b].itertuples()}
            records = []
            if b in tape:
                records = [{"symbol": row.symbol, "qty": row.qty,
                            "aggressor": row.aggressor}
                           for row in tape[b].itertuples()]
            history.append({"batch": b, "top": book, "trades": records})
        current = observable_features(history)
        offline, mids, _ = load_features(data_dir, "s01", ["HPPO", "BNKY", "CRRT", "BRRY"])
        for symbol in ("HPPO", "BNKY", "CRRT", "BRRY"):
            for feature in predictor.bundle["feature_sets"][symbol]:
                self.assertAlmostEqual(current[symbol][feature], offline[symbol][feature][batch], places=8)
        frames = session_model_rows(data_dir, "s01")
        for symbol in ("HPPO", "BNKY", "CRRT", "BRRY"):
            row = frames[(symbol, 60)].loc[lambda frame: frame["batch"].eq(batch)]
            self.assertEqual(len(row), 1)
            self.assertAlmostEqual(row.iloc[0]["future_mid"],
                                   mids[symbol][batch + 121])
            kind = predictor.bundle["selected_models"][symbol]["60"]
            from_history = predictor.predict(history, symbol, 60)
            vectorized = predict_price(row, predictor.bundle, symbol, 60, kind)[0]
            self.assertAlmostEqual(from_history["predicted_future_price_cents"], vectorized, places=8)
            self.assertTrue(np.isfinite(from_history["predicted_return_cents"]))
            self.assertEqual(from_history["horizon_seconds"], 60)
        # BRRY's empty current book blocks ETF-dependent models, but does not
        # block BNKY/CRRT models whose features do not require BRRY.
        history[-1]["top"]["BRRY"]["bid_px"] = None
        history[-1]["top"]["BRRY"]["bid_qty"] = None
        self.assertTrue(np.isfinite(predictor.predict(history, "BNKY", 60)[
            "predicted_future_price_cents"]))
        with self.assertRaises(ValueError):
            predictor.predict(history, "HPPO", 60)


if __name__ == "__main__":
    unittest.main()
