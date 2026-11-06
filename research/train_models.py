"""Fit causal fair-value models on s01-s16 only and save portable coefficients.

Run: .venv/bin/python research/train_models.py --data data --output models/fair_value_v1.json
"""

from __future__ import annotations

import argparse
from importlib.metadata import version
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from model_data import CROSS_FEATURE, FEATURES, HORIZONS, SYMBOLS, TRAIN, session_model_rows

ALPHAS = (1.0, 10.0, 100.0, 1000.0)
FIT_EVERY_BATCHES = 20  # 10 seconds; reduce repeated overlapping training labels


def pipeline(kind: str, alpha: float | None = None):
    estimator = Ridge(alpha=alpha) if kind == "ridge" else LinearRegression()
    return make_pipeline(StandardScaler(), estimator)


def serialize_fit(model, feature_names: tuple[str, ...], x: np.ndarray,
                  y: np.ndarray, alpha: float | None = None) -> dict:
    scaler, regressor = model.steps[0][1], model.steps[1][1]
    prediction = model.predict(x)
    return {
        "features": list(feature_names),
        "scaler_mean": [float(v) for v in scaler.mean_],
        "scaler_scale": [float(v) for v in scaler.scale_],
        "intercept_cents": float(regressor.intercept_),
        "coefficients": [float(v) for v in regressor.coef_],
        "train_residual_rmse_cents": float(np.sqrt(np.mean((prediction - y) ** 2))),
        "ridge_alpha": alpha,
    }


def choose_ridge_alpha(x: np.ndarray, y: np.ndarray, groups: np.ndarray) -> tuple[float, dict]:
    folds = GroupKFold(n_splits=4)
    scores = {}
    for alpha in ALPHAS:
        errors = []
        for train_index, holdout_index in folds.split(x, y, groups):
            model = pipeline("ridge", alpha)
            model.fit(x[train_index], y[train_index])
            errors.append(mean_absolute_error(y[holdout_index], model.predict(x[holdout_index])))
        scores[str(alpha)] = float(np.mean(errors))
    best = min(ALPHAS, key=lambda alpha: (scores[str(alpha)], alpha))
    return best, scores


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("models/fair_value_v1.json"))
    args = parser.parse_args()
    parts: dict[tuple[str, int], list[pd.DataFrame]] = {
        (symbol, horizon): [] for symbol in SYMBOLS for horizon in HORIZONS}
    for session in TRAIN:
        rows = session_model_rows(args.data, session)
        for key, frame in rows.items():
            parts[key].append(frame.loc[frame["batch"].mod(FIT_EVERY_BATCHES).eq(0)])
    bundle = {
        "model_id": "pqt-fair-value-v1", "pqt_client_version": version("pqt-client"),
        "python_fit_version": __import__("sys").version.split()[0],
        "train_sessions": list(TRAIN), "fit_every_batches": FIT_EVERY_BATCHES,
        "target": "mid[b + 1 + 2*h] - mid[b], cents; h in seconds",
        "feature_sets": {key: list(value) for key, value in FEATURES.items()},
        "cross_features": CROSS_FEATURE,
        "regime_thresholds": {},
        "models": {},
    }
    for symbol in SYMBOLS:
        bundle["models"][symbol] = {}
        regime_frame = pd.concat(parts[(symbol, 60)], ignore_index=True)
        bundle["regime_thresholds"][symbol] = {
            "spread_median_cents": float(regime_frame["spread"].median()),
            "rv10_median_cents": float(regime_frame["rv10"].median()),
        }
        for horizon in HORIZONS:
            frame = pd.concat(parts[(symbol, horizon)], ignore_index=True)
            assert set(frame["session"]) == set(TRAIN)
            y = frame["target_return"].to_numpy(dtype=float)
            groups = frame["session"].to_numpy()
            all_features = FEATURES[symbol]
            x = frame[list(all_features)].to_numpy(dtype=float)
            cross_feature = (CROSS_FEATURE[symbol],)
            cross_x = frame[list(cross_feature)].to_numpy(dtype=float)
            cross_model = pipeline("linear")
            cross_model.fit(cross_x, y)
            linear_model = pipeline("linear")
            linear_model.fit(x, y)
            alpha, cv_scores = choose_ridge_alpha(x, y, groups)
            ridge_model = pipeline("ridge", alpha)
            ridge_model.fit(x, y)
            bundle["models"][symbol][str(horizon)] = {
                "training_rows": len(frame),
                "cross_asset": serialize_fit(cross_model, cross_feature, cross_x, y),
                "linear": serialize_fit(linear_model, all_features, x, y),
                "ridge": serialize_fit(ridge_model, all_features, x, y, alpha),
                "ridge_train_session_cv_mae_cents": cv_scores,
            }
            print(f"{symbol} {horizon}s: {len(frame)} training rows, ridge alpha {alpha}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
