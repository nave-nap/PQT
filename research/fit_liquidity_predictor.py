"""Train-only logistic and ridge comparisons for depletion beyond OFI.

Run: .venv/bin/python research/fit_liquidity_predictor.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.run_liquidity_signals import load_features, OUT, TRAIN, VALIDATION, SYMBOLS  # noqa: E402

BASE = ("ofi_norm40", "micro_dev", "imbalance", "momentum_10s")
FEATURE = "net_dep_40"
PREDICTORS = BASE + (FEATURE,)


def design(values: np.ndarray, mu: np.ndarray, sd: np.ndarray, n: int) -> np.ndarray:
    z = np.clip((values[:, :n] - mu[:n]) / sd[:n], -5, 5)
    return np.column_stack((np.ones(len(z)), z))


def logistic_fit(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    def objective(beta):
        z = x @ beta
        loss = float(np.logaddexp(0, z).sum() - y @ z + 10 * np.dot(beta[1:], beta[1:]))
        grad = x.T @ (expit(z) - y)
        grad[1:] += 20 * beta[1:]
        return loss, grad
    start = np.zeros(x.shape[1])
    start[0] = np.log(np.clip(y.mean(), .001, .999) / (1 - np.clip(y.mean(), .001, .999)))
    result = minimize(objective, start, jac=True, method="L-BFGS-B", options={"maxiter": 150})
    if not result.success:
        raise RuntimeError(result.message)
    return result.x


def ridge_fit(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    penalty = np.eye(x.shape[1]) * 10
    penalty[0, 0] = 0
    return np.linalg.solve(x.T @ x + penalty, x.T @ y)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frames = {}
    for name in TRAIN + VALIDATION:
        frames[name] = load_features(name)
        print(f"features {name}", flush=True)
    results = []
    calibration = []
    bundle = {"version": "liquidity-depletion-v1", "training_sessions": list(TRAIN),
              "feature_order": list(PREDICTORS), "clip_standardized": 5,
              "enabled_for_trading": False, "symbols": {}}
    for symbol in SYMBOLS:
        train = pd.concat([frames[name][symbol] for name in TRAIN], ignore_index=True)
        cols = list(PREDICTORS) + ["next_ask_up", "next_bid_down", "future_60s"]
        train = train[cols].dropna()
        raw = train[list(PREDICTORS)].to_numpy(dtype=float)
        mu, sd = raw.mean(axis=0), raw.std(axis=0)
        sd[sd == 0] = 1
        xb = design(raw, mu, sd, 4)
        xp = design(raw, mu, sd, 5)
        symbol_model = {"mean": mu.tolist(), "std": sd.tolist(), "events": {}}
        for label in ("next_ask_up", "next_bid_down"):
            target = train[label].to_numpy(dtype=float)
            beta_base = logistic_fit(xb, target)
            beta_plus = logistic_fit(xp, target)
            prior = float(target.mean())
            edges = np.quantile(train[FEATURE], [.2, .4, .6, .8])
            bins = np.searchsorted(edges, train[FEATURE], side="right")
            counts = np.bincount(bins, minlength=5)
            successes = np.bincount(bins, weights=target, minlength=5)
            table = ((successes + 20 * prior) / (counts + 20)).tolist()
            symbol_model["events"][label] = {"base_coefficients": beta_base.tolist(),
                                                "plus_coefficients": beta_plus.tolist(),
                                                "unconditional_probability": prior,
                                                "table_edges": edges.tolist(),
                                                "table_probabilities": table}
            for split, names in (("train", TRAIN), ("validation", VALIDATION)):
                for name in names:
                    block = frames[name][symbol][cols].dropna()
                    if not len(block):
                        continue
                    a = block[list(PREDICTORS)].to_numpy(dtype=float)
                    y = block[label].to_numpy(dtype=float)
                    pred_base = expit(design(a, mu, sd, 4) @ beta_base)
                    pred_plus = expit(design(a, mu, sd, 5) @ beta_plus)
                    pred_table = np.asarray(table)[np.searchsorted(edges, a[:, 4], side="right")]
                    pred_rule = np.where(a[:, 4] >= edges[3], table[4],
                                         np.where(a[:, 4] <= edges[0], table[0], prior))
                    for method, pred in (("unconditional", np.full(len(y), prior)),
                                         ("rule", pred_rule), ("table", pred_table),
                                         ("logistic_base", pred_base), ("logistic_plus", pred_plus)):
                        results.append({"split": split, "session": name, "symbol": symbol,
                                        "label": label, "method": method, "observations": len(y),
                                        "brier": float(np.mean((y-pred)**2)),
                                        "base_rate": float(y.mean()),
                                        "mean_prediction": float(pred.mean())})
                    if split == "validation":
                        for bin_no in range(10):
                            mask = (pred_plus >= bin_no/10) & (pred_plus < (bin_no+1)/10)
                            if mask.any():
                                calibration.append({"session": name, "symbol": symbol, "label": label,
                                                    "probability_bin": bin_no,
                                                    "observations": int(mask.sum()),
                                                    "predicted_probability": float(pred_plus[mask].mean()),
                                                    "observed_frequency": float(y[mask].mean())})
        target = train.future_60s.to_numpy(dtype=float)
        beta_base = ridge_fit(xb, target)
        beta_plus = ridge_fit(xp, target)
        symbol_model["return_60s"] = {"base_coefficients": beta_base.tolist(),
                                      "plus_coefficients": beta_plus.tolist(),
                                      "train_residual_rmse_cents": float(np.sqrt(np.mean((target-xp@beta_plus)**2)))}
        for split, names in (("train", TRAIN), ("validation", VALIDATION)):
            for name in names:
                block = frames[name][symbol][cols].dropna()
                a = block[list(PREDICTORS)].to_numpy(dtype=float)
                y = block.future_60s.to_numpy(dtype=float)
                for method, pred in (("zero_mid", np.zeros(len(y))),
                                     ("ridge_base", design(a, mu, sd, 4) @ beta_base),
                                     ("ridge_plus", design(a, mu, sd, 5) @ beta_plus)):
                    results.append({"split": split, "session": name, "symbol": symbol,
                                    "label": "future_60s", "method": method,
                                    "observations": len(y), "mse_cents2": float(np.mean((y-pred)**2))})
        bundle["symbols"][symbol] = symbol_model
    pd.DataFrame(results).to_csv(OUT / "model_comparison.csv", index=False)
    pd.DataFrame(calibration).to_csv(OUT / "probability_calibration.csv", index=False)
    (OUT / "predictor.json").write_text(json.dumps(bundle, indent=2) + "\n")
    print(f"wrote model study to {OUT}")


if __name__ == "__main__":
    main()
