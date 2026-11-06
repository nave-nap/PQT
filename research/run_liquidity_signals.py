"""Causal depletion/replenishment signal study on s01-s20 only.

Run: .venv/bin/python research/run_liquidity_signals.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "pqt-mpl-cache"))
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import t as student_t  # noqa: E402
from pqt_client import backtest_v2 as bt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from strategies.liquidity_depletion import LiquidityStream  # noqa: E402
from strategies.orderflow_imbalance import Book, OFIStream  # noqa: E402

OUT = ROOT / "results" / "liquidity_depletion"
TRAIN = tuple(f"s{i:02d}" for i in range(1, 17))
VALIDATION = tuple(f"s{i:02d}" for i in range(17, 21))
SYMBOLS = ("HPPO", "BNKY", "BRRY", "CRRT")
HORIZONS = (1, 5, 10, 30, 60)
SIGNALS = tuple([f"net_dep_{w}" for w in (2, 5, 10, 20, 40)] +
                ["raw_dep_5", "raw_dep_40"] +
                [f"net_replen_{w}" for w in (5, 20, 40)] +
                ["no_trade_diff_20", "cycle_diff_20", "streak_diff",
                 "trade_at_touch_diff", "persistence_diff"])


def load_features(name: str) -> dict[str, pd.DataFrame]:
    session = bt.load_session(ROOT / "data", name)
    liquidity = LiquidityStream()
    ofi = OFIStream()
    rows = {s: [] for s in SYMBOLS}
    for batch, tops in enumerate(session.tops):
        trades = session.trades[batch]
        for symbol in SYMBOLS:
            top = tops[symbol]
            book = Book(top.bid_px, top.bid_qty, top.ask_px, top.ask_qty, top.status)
            f = liquidity.update(symbol, batch, book, trades)
            o = ofi.update(symbol, batch, book)
            v = f.values
            mid = ((top.bid_px + top.ask_px) / 2 if top.bid_px is not None
                   and top.ask_px is not None else np.nan)
            depth = ((top.bid_qty + top.ask_qty) if top.bid_qty is not None
                     and top.ask_qty is not None else np.nan)
            row = {"batch": batch, "mid": mid, "bid_px": top.bid_px, "ask_px": top.ask_px,
                   "spread": top.ask_px - top.bid_px if top.bid_px is not None
                   and top.ask_px is not None else np.nan,
                   "imbalance": ((top.bid_qty - top.ask_qty) / depth if depth and depth > 0
                                 else np.nan),
                   "micro_dev": ((top.ask_px - top.bid_px) *
                                 (top.bid_qty - top.ask_qty) / (2 * depth) if depth and depth > 0
                                 else np.nan),
                   "ofi_norm40": o.normalized[40],
                   "bid_eligible": v.get("bid_eligible"), "ask_eligible": v.get("ask_eligible"),
                   "bid_depletion": v.get("bid_depletion"),
                   "ask_depletion": v.get("ask_depletion"),
                   "bid_replenishment": v.get("bid_replenishment"),
                   "ask_replenishment": v.get("ask_replenishment"),
                   "bid_replenishment_speed_s": v.get("bid_replenishment_speed_s"),
                   "ask_replenishment_speed_s": v.get("ask_replenishment_speed_s"),
                   "bid_replenishment_persistence": v.get("bid_replenishment_persistence"),
                   "ask_replenishment_persistence": v.get("ask_replenishment_persistence"),
                   "bid_aligned_trade_qty": v.get("bid_aligned_trade_qty"),
                   "ask_aligned_trade_qty": v.get("ask_aligned_trade_qty")}
            for w in (2, 5, 10, 20, 40):
                row[f"net_dep_{w}"] = v.get(f"net_depletion_pressure_{w}")
            for w in (5, 40):
                ask_shares, bid_shares = v.get(f"ask_dep_shares_{w}"), v.get(f"bid_dep_shares_{w}")
                row[f"raw_dep_{w}"] = (ask_shares - bid_shares
                                         if ask_shares is not None and bid_shares is not None else None)
            for w in (5, 20, 40):
                row[f"net_replen_{w}"] = v.get(f"net_replenishment_pressure_{w}")
            row["no_trade_diff_20"] = ((v["ask_no_trade_dep_fraction_20"] or 0) -
                                       (v["bid_no_trade_dep_fraction_20"] or 0)) if v else None
            row["cycle_diff_20"] = ((v["bid_cycles_20"] or 0) -
                                    (v["ask_cycles_20"] or 0)) if v else None
            row["streak_diff"] = ((v["ask_decrease_streak"] or 0) -
                                  (v["bid_decrease_streak"] or 0)) if v else None
            row["trade_at_touch_diff"] = (min(1, (v["ask_aligned_trade_qty"] or 0) /
                                               max(5, top.ask_qty or 0)) -
                                          min(1, (v["bid_aligned_trade_qty"] or 0) /
                                               max(5, top.bid_qty or 0))) if v else None
            ap, bp = v.get("ask_replenishment_persistence"), v.get("bid_replenishment_persistence")
            row["persistence_diff"] = (bp - ap if bp is not None and ap is not None else None)
            rows[symbol].append(row)
    frames = {}
    for symbol, data in rows.items():
        frame = pd.DataFrame(data)
        frame["momentum_10s"] = frame.mid.diff(20)
        frame["next_ask_up"] = frame.ask_px.shift(-1) > frame.ask_px
        frame["next_bid_down"] = frame.bid_px.shift(-1) < frame.bid_px
        frame["next_either_move"] = (frame.ask_px.shift(-1) != frame.ask_px) | (
            frame.bid_px.shift(-1) != frame.bid_px)
        frame["next_mid_up"] = frame.mid.shift(-1) > frame.mid
        frame["next_mid_down"] = frame.mid.shift(-1) < frame.mid
        frame["future_vol_10s"] = frame.mid.diff().shift(-1).pow(2).rolling(20).sum().shift(-19).pow(.5)
        for horizon in HORIZONS:
            frame[f"future_{horizon}s"] = frame.mid.shift(-2*horizon) - frame.mid
        frames[symbol] = frame
    return frames


def corr(x: np.ndarray, y: np.ndarray) -> float:
    return float(np.corrcoef(x, y)[0, 1]) if len(x) >= 30 and np.std(x) and np.std(y) else np.nan


def ci(values: pd.Series) -> tuple[float, float, float]:
    a = values.dropna().to_numpy(dtype=float)
    if not len(a):
        return np.nan, np.nan, np.nan
    mean = float(np.mean(a))
    if len(a) < 2:
        return mean, np.nan, np.nan
    half = student_t.ppf(.975, len(a)-1) * np.std(a, ddof=1) / np.sqrt(len(a))
    return mean, float(mean-half), float(mean+half)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frames = {}
    for name in TRAIN + VALIDATION:
        frames[name] = load_features(name)
        print(f"features {name}", flush=True)
    rows = []
    event_rows = []
    for symbol in SYMBOLS:
        for feature in SIGNALS + ("ofi_norm40",):
            train_x = np.concatenate([frames[name][symbol][feature].to_numpy(dtype=float)
                                      for name in TRAIN])
            train_x = train_x[np.isfinite(train_x)]
            edges = np.quantile(train_x, (.2, .4, .6, .8)) if len(train_x) else np.zeros(4)
            for split, names in (("train", TRAIN), ("validation", VALIDATION)):
                for name in names:
                    frame = frames[name][symbol]
                    x = frame[feature].to_numpy(dtype=float)
                    for horizon in HORIZONS:
                        y = frame[f"future_{horizon}s"].to_numpy(dtype=float)
                        mask = np.isfinite(x) & np.isfinite(y)
                        a, b = x[mask], y[mask]
                        q = np.searchsorted(edges, a, side="right") + 1
                        moving = (a != 0) & (b != 0)
                        rows.append({"split": split, "session": name, "symbol": symbol,
                                     "feature": feature, "horizon_s": horizon,
                                     "observations": len(a), "correlation": corr(a,b),
                                     "sign_accuracy_nonzero": (float(np.mean(np.sign(a[moving]) == np.sign(b[moving])))
                                                               if moving.any() else np.nan),
                                     "q1_mean_cents": float(np.mean(b[q==1])) if (q==1).any() else np.nan,
                                     "q5_mean_cents": float(np.mean(b[q==5])) if (q==5).any() else np.nan,
                                     "q1_median_cents": float(np.median(b[q==1])) if (q==1).any() else np.nan,
                                     "q5_median_cents": float(np.median(b[q==5])) if (q==5).any() else np.nan,
                                     "mean_abs_future_cents": float(np.mean(abs(b))) if len(b) else np.nan})
                    valid = np.isfinite(x)
                    q = np.searchsorted(edges, x[valid], side="right") + 1
                    for quintile in (1, 5):
                        selected = frame.loc[valid].iloc[np.flatnonzero(q==quintile)]
                        if len(selected) == 0:
                            continue
                        event_rows.append({"split": split, "session": name, "symbol": symbol,
                                           "feature": feature, "quintile": quintile,
                                           "observations": len(selected),
                                           "p_next_ask_up": float(selected.next_ask_up.mean()),
                                           "p_next_bid_down": float(selected.next_bid_down.mean()),
                                           "p_next_either_move": float(selected.next_either_move.mean()),
                                           "p_next_mid_up": float(selected.next_mid_up.mean()),
                                           "p_next_mid_down": float(selected.next_mid_down.mean()),
                                           "mean_future_vol_10s": float(selected.future_vol_10s.mean()),
                                           "hypothetical_ask_markout_60_cents": float((selected.ask_px -
                                               (selected.mid + selected["future_60s"]) + .2).mean()),
                                           "hypothetical_bid_markout_60_cents": float(((selected.mid +
                                               selected["future_60s"]) - selected.bid_px + .2).mean())})
    detail = pd.DataFrame(rows)
    detail.to_csv(OUT / "session_signal_metrics.csv", index=False)
    pd.DataFrame(event_rows).to_csv(OUT / "event_analysis.csv", index=False)
    summary = []
    for key, block in detail.groupby(["split", "symbol", "feature", "horizon_s"]):
        mean, low, high = ci(block.correlation)
        summary.append(dict(zip(["split", "symbol", "feature", "horizon_s"], key)) | {
            "sessions": len(block), "observations": int(block.observations.sum()),
            "mean_session_corr": mean, "corr_ci95_low": low, "corr_ci95_high": high,
            "positive_sessions": int((block.correlation > 0).sum()),
            "sign_accuracy_nonzero": float(block.sign_accuracy_nonzero.mean()),
            "q5_minus_q1_cents": float((block.q5_mean_cents - block.q1_mean_cents).mean()),
            "q5_minus_q1_median_cents": float((block.q5_median_cents - block.q1_median_cents).mean()),
        })
    pd.DataFrame(summary).to_csv(OUT / "signal_analysis.csv", index=False)
    replen = []
    for split, names in (("train", TRAIN), ("validation", VALIDATION)):
        for name in names:
            for symbol in SYMBOLS:
                frame = frames[name][symbol]
                for side in ("bid", "ask"):
                    event = frame[frame[f"{side}_replenishment"] > 0]
                    depleted = frame[frame[f"{side}_depletion"] > 0]
                    replen.append({"split": split, "session": name, "symbol": symbol, "side": side,
                                   "replen_events": len(event), "depletion_events": len(depleted),
                                   "mean_replen_shares": event[f"{side}_replenishment"].mean(),
                                   "mean_recovery_s": event[f"{side}_replenishment_speed_s"].mean(),
                                   "mean_causal_persistence": frame[f"{side}_replenishment_persistence"].mean(),
                                   "same_price_fraction": frame[f"{side}_eligible"].mean(),
                                   "trade_aligned_depletion_fraction": float((depleted[f"{side}_aligned_trade_qty"] > 0).mean())
                                   if len(depleted) else np.nan})
    pd.DataFrame(replen).to_csv(OUT / "replenishment_summary.csv", index=False)
    # Out-of-session incremental regression: baseline includes OFI and simple book signals.
    comparisons = []
    for symbol in SYMBOLS:
        for horizon in (10, 60):
            for feature in SIGNALS:
                cols = ["ofi_norm40", "micro_dev", "imbalance", "momentum_10s",
                        feature, f"future_{horizon}s"]
                blocks = {name: frames[name][symbol][cols].dropna().to_numpy(dtype=float)
                          for name in TRAIN + VALIDATION}
                training = np.concatenate([blocks[name] for name in TRAIN])
                if len(training) < 200:
                    continue
                mu = training[:, :5].mean(axis=0)
                sd = training[:, :5].std(axis=0)
                sd[sd == 0] = 1
                x = (training[:, :5] - mu) / sd
                y = training[:, 5]
                base = np.column_stack((np.ones(len(x)), x[:, :4]))
                beta_base = np.linalg.lstsq(base, y, rcond=None)[0]
                beta_plus = np.linalg.lstsq(np.column_stack((base, x[:, 4])), y, rcond=None)[0]
                for split, names in (("train", TRAIN), ("validation", VALIDATION)):
                    for name in names:
                        a = blocks[name]
                        if len(a) < 100:
                            continue
                        z = (a[:, :5] - mu) / sd
                        p0 = np.column_stack((np.ones(len(z)), z[:, :4])) @ beta_base
                        p1 = np.column_stack((np.ones(len(z)), z)) @ beta_plus
                        target = a[:, 5]
                        comparisons.append({"split": split, "session": name, "symbol": symbol,
                                            "feature": feature, "horizon_s": horizon,
                                            "observations": len(a),
                                            "base_mse": float(np.mean((target-p0)**2)),
                                            "plus_mse": float(np.mean((target-p1)**2)),
                                            "delta_mse": float(np.mean((target-p0)**2)-np.mean((target-p1)**2)),
                                            "feature_coefficient_cents_per_train_sd": float(beta_plus[-1])})
    pd.DataFrame(comparisons).to_csv(OUT / "incremental_vs_ofi.csv", index=False)
    # Save a training-only, fixed ranking candidate, not a validation-selected policy.
    train = pd.DataFrame(summary)
    train = train[(train.split == "train") & (train.horizon_s == 60) &
                  (train.feature != "ofi_norm40")]
    train = train.assign(abs_corr=train.mean_session_corr.abs())
    ranked = train.sort_values("abs_corr", ascending=False)
    ranked.to_csv(OUT / "training_signal_ranking.csv", index=False)
    print(f"wrote liquidity signal analysis to {OUT}")


if __name__ == "__main__":
    main()
