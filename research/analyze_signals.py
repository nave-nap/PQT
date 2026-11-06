"""Evaluate causal market-data features on session-level PQT splits.

Run: .venv/bin/python research/analyze_signals.py --data data --results results
Training: s01-s16. Validation: s17-s20. Final test: s21-s24 is not read here.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "pqt-mpl-cache"))
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import t as student_t  # noqa: E402

from data_loader import add_market_variables, aggregate_trade_flow, load_manifest, load_session

HORIZONS = (1, 5, 10, 30, 60)
TRAIN = tuple(f"s{i:02d}" for i in range(1, 17))
VALIDATION = tuple(f"s{i:02d}" for i in range(17, 21))
TEST = tuple(f"s{i:02d}" for i in range(21, 25))
TAKER_FEE_CENTS = 0.4


def lag_change(values: np.ndarray, batches: int) -> np.ndarray:
    """Current minus past, with missing values at the session start."""
    out = np.full(len(values), np.nan)
    out[batches:] = values[batches:] - values[:-batches]
    return out


def future_change(values: np.ndarray, batches: int, decision_delay: int = 0) -> np.ndarray:
    """Future minus current, or future minus delayed execution-batch mid."""
    out = np.full(len(values), np.nan)
    end = len(values) - batches - decision_delay
    if end > 0:
        out[:end] = values[batches + decision_delay:] - values[decision_delay:len(values) - batches]
    return out


def load_features(data_dir: Path, session: str, symbols: list[str]) -> tuple[dict, dict, dict]:
    top, trades = load_session(data_dir, session)
    market = add_market_variables(top)
    flow = aggregate_trade_flow(trades)
    market = market.merge(flow, on=["batch", "symbol"], how="left", validate="one_to_one")
    market[["trade_qty", "signed_qty"]] = market[["trade_qty", "signed_qty"]].fillna(0)
    n = int(market["batch"].max()) + 1
    frames = {symbol: market.loc[market["symbol"].eq(symbol)].sort_values("batch").reset_index(drop=True)
              for symbol in symbols}
    assert all(len(frame) == n and np.array_equal(frame["batch"], np.arange(n))
               for frame in frames.values())
    mids = {symbol: frames[symbol]["mid_px"].to_numpy(dtype=float, na_value=np.nan)
            for symbol in symbols}
    spreads = {symbol: frames[symbol]["spread"].to_numpy(dtype=float, na_value=np.nan)
               for symbol in symbols}
    features: dict[str, dict[str, np.ndarray]] = {}
    for symbol in symbols:
        frame = frames[symbol]
        mid = mids[symbol]
        local = {
            "book_imbalance": frame["imbalance"].to_numpy(dtype=float, na_value=np.nan),
            "micro_minus_mid": (frame["microprice"] - frame["mid_px"]).to_numpy(
                dtype=float, na_value=np.nan),
        }
        for seconds in (1, 5, 10, 30):
            local[f"momentum_{seconds}s"] = lag_change(mid, 2 * seconds)
            volume = frame["trade_qty"].rolling(2 * seconds, min_periods=2 * seconds).sum()
            signed = frame["signed_qty"].rolling(2 * seconds, min_periods=2 * seconds).sum()
            ratio = (signed / volume).where(volume > 0, 0).where(volume.notna())
            local[f"signed_flow_{seconds}s"] = ratio.to_numpy(dtype=float, na_value=np.nan)
        for other in symbols:
            if other != symbol:
                for seconds in (5, 10):
                    local[f"lead_{other}_{seconds}s"] = lag_change(mids[other], 2 * seconds)
        features[symbol] = local
    gap = mids["HPPO"] - mids["BNKY"] - 2 * mids["BRRY"]
    # A raw gap may include an unknown session basis. This alternative centers
    # it on observations available through the decision batch only.
    centered = gap - pd.Series(gap).rolling(240, min_periods=60).mean().to_numpy()
    for symbol in ("HPPO", "BNKY", "BRRY"):
        features[symbol]["etf_raw_gap"] = gap.copy()
        features[symbol]["etf_gap_deviation_120s"] = centered.copy()
    # Published exposure weight gives a prespecified CRRT/HPPO hedge proxy.
    features["CRRT"]["crrt_hedged_momentum_10s"] = (
        lag_change(mids["CRRT"], 20) + 0.29 * lag_change(mids["HPPO"], 20))
    return features, mids, spreads


def family(feature: str) -> str:
    if feature == "book_imbalance":
        return "book imbalance"
    if feature == "micro_minus_mid":
        return "microprice"
    if feature.startswith("momentum_"):
        return "own momentum"
    if feature.startswith("signed_flow_"):
        return "signed flow"
    if feature.startswith("etf_"):
        return "ETF gap"
    if feature.startswith("lead_"):
        return "cross-asset lead"
    return "CRRT relation"


def session_association(x: np.ndarray, y: np.ndarray, edges: np.ndarray,
                        median_spread: float) -> dict:
    mask = np.isfinite(x) & np.isfinite(y)
    a, b = x[mask], y[mask]
    result = {"observations": len(a), "correlation": np.nan,
              "raw_sign_accuracy": np.nan, "median_spread_cents": median_spread}
    for q in range(1, 6):
        result[f"q{q}_mean_cents"] = np.nan
        result[f"q{q}_count"] = 0
    if len(a) < 100 or np.std(a) == 0 or np.std(b) == 0:
        return result
    result["correlation"] = float(np.corrcoef(a, b)[0, 1])
    direction_mask = (a != 0) & (b != 0)
    if direction_mask.sum() >= 100:
        result["raw_sign_accuracy"] = float(
            np.mean(np.sign(a[direction_mask]) == np.sign(b[direction_mask])))
    quintile = np.searchsorted(edges, a, side="right") + 1
    for q in range(1, 6):
        selected = b[quintile == q]
        result[f"q{q}_count"] = len(selected)
        if len(selected):
            result[f"q{q}_mean_cents"] = float(selected.mean())
    return result


def mean_ci(values: pd.Series) -> tuple[float, float, float]:
    a = values.dropna().to_numpy(dtype=float)
    if not len(a):
        return np.nan, np.nan, np.nan
    avg = float(a.mean())
    if len(a) < 2:
        return avg, np.nan, np.nan
    half = float(student_t.ppf(0.975, len(a) - 1) * a.std(ddof=1) / np.sqrt(len(a)))
    return avg, avg - half, avg + half


def summarize(session_rows: pd.DataFrame) -> pd.DataFrame:
    rows = []
    group_columns = ["split", "symbol", "feature", "family", "horizon_s"]
    for key, block in session_rows.groupby(group_columns, sort=True):
        corr, low, high = mean_ci(block["correlation"])
        row = dict(zip(group_columns, key))
        row.update({
            "sessions": int(block["correlation"].notna().sum()),
            "observations": int(block["observations"].sum()),
            "mean_session_corr": corr, "corr_ci95_low": low, "corr_ci95_high": high,
            "positive_corr_sessions": int(block["correlation"].gt(0).sum()),
            "negative_corr_sessions": int(block["correlation"].lt(0).sum()),
            "median_spread_cents": float(block["median_spread_cents"].median()),
            "raw_sign_accuracy": float(block["raw_sign_accuracy"].mean()),
        })
        for q in range(1, 6):
            row[f"q{q}_mean_cents"] = float(block[f"q{q}_mean_cents"].mean())
            row[f"q{q}_sessions"] = int(block[f"q{q}_count"].gt(0).sum())
        row["q5_minus_q1_cents"] = row["q5_mean_cents"] - row["q1_mean_cents"]
        rows.append(row)
    summary = pd.DataFrame(rows)
    train_sign = summary.loc[summary["split"].eq("train"),
                             ["symbol", "feature", "horizon_s", "mean_session_corr"]].copy()
    train_sign["train_direction"] = np.sign(train_sign.pop("mean_session_corr"))
    summary = summary.merge(train_sign, on=["symbol", "feature", "horizon_s"],
                            how="left", validate="many_to_one")
    summary["same_direction_sessions"] = np.where(
        summary["train_direction"].ge(0), summary["positive_corr_sessions"],
        summary["negative_corr_sessions"])
    summary["directed_sign_accuracy"] = np.where(
        summary["train_direction"].ge(0), summary["raw_sign_accuracy"],
        1 - summary["raw_sign_accuracy"])
    summary["directed_balanced_move_cents"] = (
        summary["train_direction"] * summary["q5_minus_q1_cents"] / 2)
    summary["half_spread_plus_taker_fee_cents"] = (
        summary["median_spread_cents"] / 2 + TAKER_FEE_CENTS)
    summary["move_minus_simple_cost_hurdle_cents"] = (
        summary["directed_balanced_move_cents"] -
        summary["half_spread_plus_taker_fee_cents"])
    return summary


def relationship_rows(session: str, split: str, mids: dict[str, np.ndarray]) -> list[dict]:
    returns = {symbol: lag_change(mid, 20) for symbol, mid in mids.items()}
    rows = []
    for other in ("HPPO", "BNKY", "BRRY"):
        x, y = returns[other], returns["CRRT"]
        mask = np.isfinite(x) & np.isfinite(y)
        corr = np.corrcoef(x[mask], y[mask])[0, 1]
        beta = np.cov(x[mask], y[mask])[0, 1] / np.var(x[mask], ddof=1)
        rows.append({"session": session, "split": split, "crrt_vs": other,
                     "contemporaneous_10s_return_corr": float(corr),
                     "crrt_cents_per_other_cent": float(beta)})
    return rows


def plot_signal_results(results: Path, summary: pd.DataFrame, ranking: pd.DataFrame) -> None:
    selected = ranking.head(12).iloc[::-1]
    fig, ax = plt.subplots(figsize=(11, 7))
    ypos = np.arange(len(selected))
    labels = [f"{r.symbol}: {r.feature}" for r in selected.itertuples()]
    ax.barh(ypos, selected["train_mean_corr"], color="#3366cc", alpha=0.7, label="train")
    ax.scatter(selected["validation_mean_corr"], ypos, color="#d95f02", marker="D",
               s=32, label="validation")
    ax.set_yticks(ypos, labels)
    ax.axvline(0, color="black", linewidth=0.7)
    ax.set(xlabel="Mean correlation across sessions (60 s target)",
           title="Top training correlations and their validation results")
    ax.legend()
    fig.tight_layout()
    fig.savefig(results / "eda" / "signal_correlations_60s.png", dpi=170)
    plt.close(fig)

    fig, axs = plt.subplots(2, 2, figsize=(11, 8), sharex=True)
    chosen = ranking.head(4)
    for ax, record in zip(axs.flat, chosen.itertuples()):
        pair = summary.loc[summary["symbol"].eq(record.symbol) &
                           summary["feature"].eq(record.feature) &
                           summary["horizon_s"].eq(60)]
        for split, color in (("train", "#3366cc"), ("validation", "#d95f02")):
            row = pair.loc[pair["split"].eq(split)].iloc[0]
            values = [row[f"q{q}_mean_cents"] for q in range(1, 6)]
            ax.plot(range(1, 6), values, marker="o", label=split, color=color)
        ax.axhline(0, color="gray", linewidth=0.7)
        ax.set(title=f"{record.symbol}: {record.feature}",
               ylabel="Mean future mid change (cents)")
        ax.grid(alpha=0.2)
    axs[0, 0].legend()
    for ax in axs[-1]:
        ax.set_xlabel("Training-distribution feature quintile")
    fig.suptitle("60-second conditional price changes, equally weighted by session")
    fig.tight_layout()
    fig.savefig(results / "eda" / "signal_quantile_profiles_60s.png", dpi=170)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--results", type=Path, default=Path("results"))
    args = parser.parse_args()
    args.results.mkdir(parents=True, exist_ok=True)
    (args.results / "eda").mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(args.data)
    assert manifest["batch_ms"] == 500
    assert manifest["sessions"] == list(TRAIN + VALIDATION + TEST)
    symbols = [entry["symbol"] for entry in manifest["symbols"]]
    thresholds: dict[tuple[str, str], list[np.ndarray]] = {}
    for session in TRAIN:
        features, _, _ = load_features(args.data, session, symbols)
        for symbol, items in features.items():
            for name, values in items.items():
                thresholds.setdefault((symbol, name), []).append(values[np.isfinite(values)])
    edges = {key: np.quantile(np.concatenate(chunks), [0.2, 0.4, 0.6, 0.8])
             for key, chunks in thresholds.items()}
    rows, delayed_rows, relationships = [], [], []
    for session in TRAIN + VALIDATION:
        split = "train" if session in TRAIN else "validation"
        features, mids, spreads = load_features(args.data, session, symbols)
        relationships.extend(relationship_rows(session, split, mids))
        for symbol in symbols:
            for name, values in features[symbol].items():
                for horizon in HORIZONS:
                    target = future_change(mids[symbol], 2 * horizon)
                    result = session_association(values, target, edges[(symbol, name)],
                                                 float(np.nanmedian(spreads[symbol])))
                    rows.append({"session": session, "split": split, "symbol": symbol,
                                 "feature": name, "family": family(name),
                                 "horizon_s": horizon, **result})
                    delayed_target = future_change(mids[symbol], 2 * horizon,
                                                   decision_delay=1)
                    delayed_result = session_association(values, delayed_target,
                                                         edges[(symbol, name)],
                                                         float(np.nanmedian(spreads[symbol])))
                    delayed_rows.append({"session": session, "split": split, "symbol": symbol,
                                         "feature": name, "family": family(name),
                                         "horizon_s": horizon, **delayed_result})
    session_stats = pd.DataFrame(rows)
    session_stats.to_csv(args.results / "eda" / "signal_session_metrics.csv",
                         index=False, float_format="%.6f")
    pd.DataFrame(relationships).to_csv(args.results / "eda" / "crrt_relationships.csv",
                                       index=False, float_format="%.6f")
    summary = summarize(session_stats)
    summary.to_csv(args.results / "signal_analysis.csv", index=False, float_format="%.6f")
    delayed_summary = summarize(pd.DataFrame(delayed_rows))
    delayed_summary.to_csv(args.results / "eda" / "delayed_signal_analysis.csv",
                           index=False, float_format="%.6f")
    train = summary.loc[summary["split"].eq("train") & summary["horizon_s"].eq(60)].copy()
    validation = summary.loc[summary["split"].eq("validation") & summary["horizon_s"].eq(60)].copy()
    ranking = train.merge(validation, on=["symbol", "feature", "family", "horizon_s"],
                          suffixes=("_train", "_validation"), validate="one_to_one")
    ranking = ranking.rename(columns={
        "mean_session_corr_train": "train_mean_corr",
        "mean_session_corr_validation": "validation_mean_corr",
    })
    ranking["train_abs_corr"] = ranking["train_mean_corr"].abs()
    ranking = ranking.sort_values(["train_abs_corr", "symbol", "feature"],
                                  ascending=[False, True, True]).reset_index(drop=True)
    ranking.insert(0, "training_rank", np.arange(1, len(ranking) + 1))
    ranking.to_csv(args.results / "eda" / "signal_ranking_60s.csv",
                   index=False, float_format="%.6f")
    plot_signal_results(args.results, summary, ranking)
    print(f"Evaluated {len(summary)} split-level feature/horizon rows across 20 sessions")
    print("Final test sessions s21-s24 were not read for predictive analysis")
    print("Top 10 training-ranked 60 s signals:")
    print(ranking[["training_rank", "symbol", "feature", "train_mean_corr",
                   "validation_mean_corr", "same_direction_sessions_train",
                   "same_direction_sessions_validation"]].head(10).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
