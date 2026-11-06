"""Pure-Python, training-fitted depletion predictor; disabled for trading.

Probabilities refer to the next batch's best ask rising or best bid falling.
They are empirical/model estimates, not hidden-value probabilities.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Mapping


def _sigmoid(value: float) -> float:
    if value >= 0:
        e = math.exp(-value)
        return 1 / (1 + e)
    e = math.exp(value)
    return e / (1 + e)


class LiquidityPredictor:
    def __init__(self, path: str | Path):
        self.bundle = json.loads(Path(path).read_text(encoding="utf-8"))
        if self.bundle["version"] != "liquidity-depletion-v1":
            raise ValueError("unsupported depletion predictor version")
        self.order = tuple(self.bundle["feature_order"])

    def predict(self, symbol: str, features: Mapping[str, float | None],
                *, quality: str = "valid") -> dict:
        if symbol not in self.bundle["symbols"]:
            raise KeyError(symbol)
        required = [features.get(name) for name in self.order]
        if quality != "valid" or any(v is None or not math.isfinite(float(v)) for v in required):
            return {"quality": quality if quality != "valid" else "missing_feature",
                    "symbol": symbol, "p_ask_up_next_batch": None,
                    "p_bid_down_next_batch": None, "predicted_return_60s_cents": None,
                    "uncertainty_60s_cents": None, "signal_strength": None,
                    "calibrated": False}
        model = self.bundle["symbols"][symbol]
        x = [1.0] + [max(-5., min(5., (float(value) - mean) / sd))
                     for value, mean, sd in zip(required, model["mean"], model["std"])]
        ask = model["events"]["next_ask_up"]["plus_coefficients"]
        bid = model["events"]["next_bid_down"]["plus_coefficients"]
        ret = model["return_60s"]
        return {"quality": "valid", "symbol": symbol,
                "p_ask_up_next_batch": _sigmoid(sum(a*b for a, b in zip(x, ask))),
                "p_bid_down_next_batch": _sigmoid(sum(a*b for a, b in zip(x, bid))),
                "predicted_return_60s_cents": sum(a*b for a, b in zip(x, ret["plus_coefficients"])),
                "uncertainty_60s_cents": ret["train_residual_rmse_cents"],
                "signal_strength": x[-1], "calibrated": False}
