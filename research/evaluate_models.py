"""Compare saved fair-value models on s17-s20 without touching final tests.

Run: .venv/bin/python research/evaluate_models.py --data data --model models/fair_value_v1.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "pqt-mpl-cache"))
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from model_data import HORIZONS, SYMBOLS, VALIDATION, session_model_rows

MODEL_KINDS = ("mid", "microprice", "cross_asset", "linear", "ridge")
TAKER_FEE_CENTS = 0.4
EXTRA_EDGE_CENTS = 1.0
MIN_DEPTH_SHARES = 10
PROXY_EVERY_BATCHES = 20


def predict_price(frame: pd.DataFrame, bundle: dict, symbol: str,
                  horizon: int, kind: str) -> np.ndarray:
    mid = frame["mid"].to_numpy(dtype=float)
    if kind == "mid":
        return mid
    if kind == "microprice":
        return frame["microprice"].to_numpy(dtype=float)
    spec = bundle["models"][symbol][str(horizon)][kind]
    x = frame[spec["features"]].to_numpy(dtype=float)
    mean, scale = np.array(spec["scaler_mean"]), np.array(spec["scaler_scale"])
    coefficients = np.array(spec["coefficients"])
    predicted_return = spec["intercept_cents"] + ((x - mean) / scale) @ coefficients
    return mid + predicted_return


def session_metrics(frame: pd.DataFrame, prediction: np.ndarray) -> dict:
    actual = frame["future_mid"].to_numpy(dtype=float)
    mid = frame["mid"].to_numpy(dtype=float)
    residual = prediction - actual
    pred_return, actual_return = prediction - mid, actual - mid
    directional = (pred_return != 0) & (actual_return != 0)
    sign_accuracy = (float(np.mean(np.sign(pred_return[directional]) ==
                                   np.sign(actual_return[directional])))
                     if directional.sum() >= 100 else np.nan)
    calibration_slope = (float(np.cov(pred_return, actual_return)[0, 1] /
                               np.var(pred_return, ddof=1))
                         if np.std(pred_return) > 1e-9 else np.nan)
    return {
        "eligible_rows": len(frame), "mae_cents": float(np.mean(np.abs(residual))),
        "rmse_cents": float(np.sqrt(np.mean(residual ** 2))),
        "bias_cents": float(np.mean(residual)),
        "direction_accuracy": sign_accuracy,
        "calibration_slope": calibration_slope,
    }


def decision_proxy(frame: pd.DataFrame, prediction: np.ndarray) -> dict:
    """Illustrative one-share taker markout with a 10-second decision cadence.

    Decision uses only current quotes and prediction. A hypothetical 10-share
    IOC fill requires next-batch depth and a non-adverse next touch; one cent
    impact is paid within the current touch +1 / touch -1 limit.
    """
    eligible = frame["batch"].mod(PROXY_EVERY_BATCHES).eq(0).to_numpy()
    p = prediction[eligible]
    sample = frame.loc[eligible]
    ask, bid = sample["ask"].to_numpy(), sample["bid"].to_numpy()
    buy = p - ask > TAKER_FEE_CENTS + EXTRA_EDGE_CENTS
    sell = bid - p > TAKER_FEE_CENTS + EXTRA_EDGE_CENTS
    next_ask, next_bid = sample["next_ask"].to_numpy(), sample["next_bid"].to_numpy()
    buy_fill = (buy & np.isfinite(next_ask) & (next_ask <= ask) &
                (sample["ask_qty"].to_numpy() >= MIN_DEPTH_SHARES) &
                (sample["next_ask_qty"].to_numpy() >= MIN_DEPTH_SHARES))
    sell_fill = (sell & np.isfinite(next_bid) & (next_bid >= bid) &
                 (sample["bid_qty"].to_numpy() >= MIN_DEPTH_SHARES) &
                 (sample["next_bid_qty"].to_numpy() >= MIN_DEPTH_SHARES))
    future = sample["future_mid"].to_numpy()
    markouts = np.concatenate([
        future[buy_fill] - (next_ask[buy_fill] + 1) - TAKER_FEE_CENTS,
        (next_bid[sell_fill] - 1) - future[sell_fill] - TAKER_FEE_CENTS,
    ])
    return {"decision_opportunities": len(sample), "signal_attempts": int((buy | sell).sum()),
            "proxy_fills": len(markouts),
            "proxy_mean_markout_cents": float(markouts.mean()) if len(markouts) else np.nan}


def calibration_bins(frame: pd.DataFrame, prediction: np.ndarray, session: str,
                     symbol: str, horizon: int, kind: str) -> pd.DataFrame:
    pred_return = prediction - frame["mid"].to_numpy()
    actual_return = frame["target_return"].to_numpy()
    # Diagnostic buckets only. These validation quantiles are never used to fit
    # or tune a model, a threshold, or a live decision rule.
    edges = np.unique(np.quantile(pred_return, [0.2, 0.4, 0.6, 0.8]))
    group = np.searchsorted(edges, pred_return, side="right") + 1
    rows = []
    for bin_number in range(1, len(edges) + 2):
        mask = group == bin_number
        if not mask.any():
            continue
        rows.append({"session": session, "symbol": symbol, "horizon_s": horizon,
                     "model": kind, "bin": bin_number, "rows": int(mask.sum()),
                     "mean_predicted_return_cents": float(pred_return[mask].mean()),
                     "mean_realized_return_cents": float(actual_return[mask].mean())})
    return pd.DataFrame(rows)


def regime_metrics(frame: pd.DataFrame, prediction: np.ndarray, thresholds: dict,
                   session: str, symbol: str, horizon: int, kind: str) -> list[dict]:
    error = np.abs(prediction - frame["future_mid"].to_numpy())
    rows = []
    for name, column, threshold in (
        ("spread", "spread", thresholds["spread_median_cents"]),
        ("volatility", "rv10", thresholds["rv10_median_cents"]),
    ):
        values = frame[column].to_numpy()
        for level, mask in (("low", values <= threshold), ("high", values > threshold)):
            rows.append({"session": session, "symbol": symbol, "horizon_s": horizon,
                         "model": kind, "regime": name, "level": level,
                         "training_threshold": threshold, "rows": int(mask.sum()),
                         "mae_cents": float(error[mask].mean()) if mask.any() else np.nan})
    return rows


def summarize(session_rows: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (symbol, horizon, kind), block in session_rows.groupby(
            ["symbol", "horizon_s", "model"], sort=True):
        rows.append({
            "symbol": symbol, "horizon_s": horizon, "model": kind,
            "sessions": len(block), "eligible_rows": int(block["eligible_rows"].sum()),
            "mae_cents": float(block["mae_cents"].mean()),
            "rmse_cents": float(block["rmse_cents"].mean()),
            "bias_cents": float(block["bias_cents"].mean()),
            "direction_accuracy": float(block["direction_accuracy"].mean()),
            "calibration_slope": float(block["calibration_slope"].mean()),
            "signal_attempts": int(block["signal_attempts"].sum()),
            "proxy_fills": int(block["proxy_fills"].sum()),
            "proxy_mean_session_markout_cents": float(block["proxy_mean_markout_cents"].mean()),
        })
    summary = pd.DataFrame(rows)
    for baseline in ("mid", "microprice"):
        key = summary.loc[summary["model"].eq(baseline),
                          ["symbol", "horizon_s", "mae_cents", "rmse_cents"]].rename(columns={
                              "mae_cents": f"{baseline}_mae_cents",
                              "rmse_cents": f"{baseline}_rmse_cents"})
        summary = summary.merge(key, on=["symbol", "horizon_s"],
                                how="left", validate="many_to_one")
        summary[f"mae_improvement_vs_{baseline}_pct"] = (
            100 * (summary[f"{baseline}_mae_cents"] - summary["mae_cents"])
            / summary[f"{baseline}_mae_cents"])
    return summary


def choose_models(summary: pd.DataFrame, sessions: pd.DataFrame) -> dict:
    """Prefer a baseline unless a learned model clears a material stability bar."""
    selected = {}
    for symbol in SYMBOLS:
        selected[symbol] = {}
        for horizon in HORIZONS:
            rows = summary.loc[summary["symbol"].eq(symbol) &
                               summary["horizon_s"].eq(horizon)].set_index("model")
            baseline = min(("mid", "microprice"), key=lambda kind: rows.loc[kind, "mae_cents"])
            baseline_mae = rows.loc[baseline, "mae_cents"]
            learned = min(("cross_asset", "linear", "ridge"),
                          key=lambda kind: rows.loc[kind, "mae_cents"])
            learned_mae = rows.loc[learned, "mae_cents"]
            gain = 100 * (baseline_mae - learned_mae) / baseline_mae
            pair = sessions.loc[sessions["symbol"].eq(symbol) &
                                sessions["horizon_s"].eq(horizon)]
            base_session = pair.loc[pair["model"].eq(baseline)].set_index("session")["mae_cents"]
            best_session = pair.loc[pair["model"].eq(learned)].set_index("session")["mae_cents"]
            wins = int((best_session < base_session).sum())
            if gain >= 0.5 and wins >= 3:
                # Within 0.2% of the best learned MAE, use the simpler model.
                tolerance = learned_mae * 0.002
                for kind in ("cross_asset", "linear", "ridge"):
                    if rows.loc[kind, "mae_cents"] <= learned_mae + tolerance:
                        learned = kind
                        break
                selected[symbol][str(horizon)] = learned
            else:
                selected[symbol][str(horizon)] = baseline
    return selected


def render_plots(output: Path, summary: pd.DataFrame, bins: pd.DataFrame,
                 selected: dict) -> None:
    fig, axs = plt.subplots(2, 2, figsize=(12, 8), sharex=True)
    for ax, symbol in zip(axs.flat, SYMBOLS):
        block = summary.loc[summary["symbol"].eq(symbol)]
        for kind in MODEL_KINDS:
            part = block.loc[block["model"].eq(kind)]
            ax.plot(part["horizon_s"], part["mae_improvement_vs_mid_pct"],
                    marker="o", label=kind)
        ax.axhline(0, color="gray", linewidth=0.7)
        ax.set(title=symbol, ylabel="MAE improvement vs mid (%)")
        ax.grid(alpha=0.2)
    axs[0, 0].legend(fontsize=8)
    for ax in axs[-1]:
        ax.set_xlabel("Prediction horizon (seconds)")
    fig.suptitle("Validation MAE; same eligible observations for every model per symbol/horizon")
    fig.tight_layout()
    fig.savefig(output / "mae_comparison.png", dpi=160)
    plt.close(fig)

    fig, axs = plt.subplots(2, 2, figsize=(10, 8))
    for ax, symbol in zip(axs.flat, SYMBOLS):
        kind = selected[symbol]["60"]
        part = bins.loc[bins["symbol"].eq(symbol) & bins["horizon_s"].eq(60) &
                        bins["model"].eq(kind)]
        grouped = part.groupby("bin", as_index=False).agg(
            predicted=("mean_predicted_return_cents", "mean"),
            actual=("mean_realized_return_cents", "mean"))
        ax.scatter(grouped["predicted"], grouped["actual"], s=40)
        for row in grouped.itertuples():
            ax.annotate(str(row.bin), (row.predicted, row.actual), xytext=(3, 3),
                        textcoords="offset points", fontsize=8)
        lo = min(grouped[["predicted", "actual"]].min())
        hi = max(grouped[["predicted", "actual"]].max())
        ax.plot([lo, hi], [lo, hi], color="gray", linewidth=0.8)
        ax.set(title=f"{symbol}: {kind}", xlabel="Predicted 60 s return (cents)",
               ylabel="Realized 60 s mid return (cents)")
        ax.grid(alpha=0.2)
    fig.suptitle("Selected-model validation calibration; numbers are prediction bins")
    fig.tight_layout()
    fig.savefig(output / "selected_calibration_60s.png", dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--model", type=Path, default=Path("models/fair_value_v1.json"))
    parser.add_argument("--results", type=Path, default=Path("results"))
    parser.add_argument("--selected-output", type=Path,
                        default=Path("models/fair_value_v1_selected.json"))
    args = parser.parse_args()
    bundle = json.loads(args.model.read_text(encoding="utf-8"))
    assert bundle["train_sessions"] == [f"s{i:02d}" for i in range(1, 17)]
    output = args.results / "model_diagnostics"
    output.mkdir(parents=True, exist_ok=True)
    records, bins, regimes = [], [], []
    for session in VALIDATION:
        frames = session_model_rows(args.data, session)
        for (symbol, horizon), frame in frames.items():
            for kind in MODEL_KINDS:
                prediction = predict_price(frame, bundle, symbol, horizon, kind)
                metric = session_metrics(frame, prediction)
                proxy = decision_proxy(frame, prediction)
                records.append({"session": session, "symbol": symbol,
                                "horizon_s": horizon, "model": kind, **metric, **proxy})
                bins.append(calibration_bins(frame, prediction, session, symbol, horizon, kind))
                regimes.extend(regime_metrics(frame, prediction,
                                              bundle["regime_thresholds"][symbol],
                                              session, symbol, horizon, kind))
    session_results = pd.DataFrame(records)
    session_results.to_csv(output / "model_session_metrics.csv", index=False,
                           float_format="%.6f")
    all_bins = pd.concat(bins, ignore_index=True)
    all_bins.to_csv(output / "model_edge_bins.csv", index=False, float_format="%.6f")
    regime_result = pd.DataFrame(regimes)
    regime_result.to_csv(output / "model_regimes.csv", index=False, float_format="%.6f")
    summary = summarize(session_results)
    summary.to_csv(args.results / "model_comparison.csv", index=False,
                   float_format="%.6f")
    selected = choose_models(summary, session_results)
    bundle["selected_models"] = selected
    bundle["selection_rule"] = (
        "On s17-s20, learned MAE >=0.5% below best mid/micro baseline and better "
        "in >=3/4 sessions; choose simplest model within 0.2% of best learned MAE."
    )
    bundle["selection_sessions"] = list(VALIDATION)
    args.selected_output.parent.mkdir(parents=True, exist_ok=True)
    args.selected_output.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    render_plots(output, summary, all_bins, selected)
    print(f"Saved {len(summary)} model comparisons and selected bundle {args.selected_output}")
    print("Selected per symbol/horizon:", selected)
    print("Final test sessions s21-s24 were not read")


if __name__ == "__main__":
    main()
