"""Portable, causal inference for a PQT fair-value model bundle.

Input is a sequence of observed batches through the current decision batch:
    {"batch": 240,
     "top": {"HPPO": {"bid_px": ..., "bid_qty": ..., "ask_px": ..., "ask_qty": ...}, ...},
     "trades": [{"symbol": "HPPO", "qty": 10, "aggressor": "BUY"}, ...]}

No future book, trade, or target is accepted by the feature builder. Keep at
least 241 contiguous batches; the latest 240 form the longest rolling window.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


def _field(obj: Any, name: str) -> Any:
    return obj[name] if isinstance(obj, Mapping) else getattr(obj, name)


def _side(value: Any) -> str:
    return str(getattr(value, "value", value)).upper()


def _book_values(top: Any) -> tuple[float, float, float] | None:
    bid, ask = _field(top, "bid_px"), _field(top, "ask_px")
    bid_qty, ask_qty = _field(top, "bid_qty"), _field(top, "ask_qty")
    try:
        if any(not math.isfinite(float(value)) for value in (bid, ask, bid_qty, ask_qty)):
            return None
    except (TypeError, ValueError):
        return None
    if bid_qty <= 0 or ask_qty <= 0 or bid >= ask:
        return None
    mid = (bid + ask) / 2
    micro = (ask * bid_qty + bid * ask_qty) / (bid_qty + ask_qty)
    imbalance = (bid_qty - ask_qty) / (bid_qty + ask_qty)
    return float(mid), float(micro), float(imbalance)


def _mid(record: Mapping, symbol: str) -> float | None:
    values = _book_values(record["top"][symbol])
    return None if values is None else values[0]


def _change(history: Sequence[Mapping], symbol: str, batches: int) -> float | None:
    current, past = _mid(history[-1], symbol), _mid(history[-1 - batches], symbol)
    return None if current is None or past is None else current - past


def observable_features(history: Sequence[Mapping],
                        requested_symbols: Sequence[str] | None = None) -> dict[str, dict[str, float]]:
    """Build Stage 3 inputs from past/current observations only.

    Raises ValueError during warmup, after skipped batches, or when a required
    book side is absent. A live adapter can then skip trading that batch.
    """
    if len(history) < 241:
        raise ValueError("at least 241 observed contiguous batches are required")
    recent = history[-241:]
    if any(_field(recent[i + 1], "batch") != _field(recent[i], "batch") + 1
           for i in range(len(recent) - 1)):
        raise ValueError("history has a skipped or repeated batch")
    symbols = tuple(requested_symbols or ("HPPO", "BNKY", "CRRT", "BRRY"))
    result = {}
    for symbol in symbols:
        current = _book_values(recent[-1]["top"][symbol])
        if current is None:
            raise ValueError(f"{symbol} has an empty current book side")
        mid, micro, imbalance = current
        signed = volume = 0
        for record in recent[-60:]:
            for trade in record.get("trades", ()):
                if _field(trade, "symbol") != symbol:
                    continue
                qty = int(_field(trade, "qty"))
                aggressor = _side(_field(trade, "aggressor"))
                if aggressor not in ("BUY", "SELL"):
                    raise ValueError("unknown aggressor side")
                signed += qty if aggressor == "BUY" else -qty
                volume += qty
        values = {
            "mid": mid, "microprice": micro,
            "book_imbalance": imbalance,
            "micro_minus_mid": micro - mid,
            "signed_flow_30s": signed / volume if volume else 0.0,
        }
        if symbol in ("HPPO", "BNKY"):
            values["momentum_10s"] = _change(recent, symbol, 20)
        if symbol == "CRRT":
            values["momentum_30s"] = _change(recent, symbol, 60)
        if symbol in ("BNKY", "CRRT", "BRRY"):
            values["lead_HPPO_10s"] = _change(recent, "HPPO", 20)
        if symbol == "HPPO":
            values["lead_BRRY_10s"] = _change(recent, "BRRY", 20)
        if symbol == "BRRY":
            h, b, r = (_mid(recent[-1], name) for name in ("HPPO", "BNKY", "BRRY"))
            if None in (h, b, r):
                raise ValueError("missing current ETF basket side")
            gap = h - b - 2 * r
            past_gaps = []
            for record in recent[-240:]:
                past_h, past_b, past_r = (_mid(record, name)
                                          for name in ("HPPO", "BNKY", "BRRY"))
                if None not in (past_h, past_b, past_r):
                    past_gaps.append(past_h - past_b - 2 * past_r)
            if len(past_gaps) < 60:
                raise ValueError("insufficient two-sided ETF basket history")
            values["etf_raw_gap"] = gap
            values["etf_gap_deviation_120s"] = gap - sum(past_gaps) / len(past_gaps)
        if any(value is None for value in values.values()):
            raise ValueError(f"{symbol} has a missing required past book side")
        result[symbol] = values
    return result


class FairValuePredictor:
    def __init__(self, path: str | Path = "models/fair_value_v1_selected.json") -> None:
        self.bundle = json.loads(Path(path).read_text(encoding="utf-8"))
        if self.bundle["model_id"] != "pqt-fair-value-v1":
            raise ValueError("unknown model bundle version")

    def predict_from_features(self, symbol: str, horizon_seconds: int,
                              features: Mapping[str, float],
                              model_kind: str = "selected") -> dict:
        if symbol not in self.bundle["models"] or str(horizon_seconds) not in self.bundle["models"][symbol]:
            raise ValueError("unsupported symbol or horizon")
        if model_kind == "selected":
            model_kind = self.bundle["selected_models"][symbol][str(horizon_seconds)]
        mid = float(features["mid"])
        if model_kind == "mid":
            predicted_price, uncertainty = mid, None
        elif model_kind == "microprice":
            predicted_price, uncertainty = float(features["microprice"]), None
        else:
            model = self.bundle["models"][symbol][str(horizon_seconds)][model_kind]
            scaled = [(float(features[name]) - mean) / scale
                      for name, mean, scale in zip(model["features"], model["scaler_mean"],
                                                   model["scaler_scale"])]
            predicted_return = model["intercept_cents"] + sum(
                coef * value for coef, value in zip(model["coefficients"], scaled))
            predicted_price = mid + predicted_return
            uncertainty = model["train_residual_rmse_cents"]
        if not math.isfinite(predicted_price):
            raise ValueError("nonfinite prediction")
        return {
            "predicted_future_price_cents": predicted_price,
            "predicted_return_cents": predicted_price - mid,
            "horizon_seconds": horizon_seconds,
            "uncertainty_cents": uncertainty,
            "model_id": f"{self.bundle['model_id']}/{symbol}/{horizon_seconds}s/{model_kind}",
        }

    def predict(self, history: Sequence[Mapping], symbol: str,
                horizon_seconds: int = 60, model_kind: str = "selected") -> dict:
        values = observable_features(history, (symbol,))[symbol]
        return self.predict_from_features(symbol, horizon_seconds, values, model_kind)
