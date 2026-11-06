"""Audit PQT history and measure causal features against future midprice changes.

Run: .venv/bin/python research/stage1_explore.py --data data

Only s01-s16 are used for predictive research. s17-s20 and s21-s24 are
reserved for later validation and final evaluation, respectively.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

HORIZONS = (1, 5, 10, 30, 60)  # seconds
FEATURES = ("imbalance", "micro_offset", "flow_10s", "past_return_10s")
DISCOVERY = tuple(f"s{i:02d}" for i in range(1, 17))
VALIDATION = tuple(f"s{i:02d}" for i in range(17, 21))
FINAL_TEST = tuple(f"s{i:02d}" for i in range(21, 25))
SYMBOLS = ("HPPO", "BNKY", "CRRT", "BRRY")


def finite_pair(x: pd.Series, y: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    a, b = x.to_numpy(dtype=float), y.to_numpy(dtype=float)
    good = np.isfinite(a) & np.isfinite(b)
    return a[good], b[good]


def association(x: pd.Series, y: pd.Series) -> dict[str, float | int | None]:
    a, b = finite_pair(x, y)
    if len(a) < 100 or np.std(a) == 0 or np.std(b) == 0:
        return {"n": int(len(a)), "corr": None, "top_bottom_cents": None}
    lo, hi = np.quantile(a, [0.2, 0.8])
    low, high = b[a <= lo], b[a >= hi]
    return {
        "n": int(len(a)),
        "corr": float(np.corrcoef(a, b)[0, 1]),
        "top_bottom_cents": float(high.mean() - low.mean()),
    }


def summarize(rows: list[dict]) -> list[dict]:
    result = []
    for keys, block in pd.DataFrame(rows).groupby(["symbol", "feature", "horizon_s"], sort=True):
        valid = block.dropna(subset=["corr"])
        correlations = valid["corr"].to_numpy(dtype=float)
        result.append({
            "symbol": keys[0], "feature": keys[1], "horizon_s": int(keys[2]),
            "sessions": int(len(valid)),
            "mean_corr": float(correlations.mean()) if len(valid) else None,
            "positive_sessions": int((correlations > 0).sum()),
            "mean_top_bottom_cents": float(valid["top_bottom_cents"].mean()) if len(valid) else None,
        })
    return result


def load_and_audit(data_dir: Path, session: str, n_batches: int) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    top = pd.read_csv(data_dir / f"{session}.top.csv")
    trades = pd.read_csv(data_dir / f"{session}.trades.csv")
    required_top = {"batch", "symbol", "bid_px", "bid_qty", "ask_px", "ask_qty", "status"}
    required_trades = {"batch", "symbol", "price", "qty", "aggressor", "self_trade", "aux"}
    assert set(top.columns) == required_top, (session, "top columns")
    assert set(trades.columns) == required_trades, (session, "trade columns")
    assert len(top) == n_batches * len(SYMBOLS), (session, "top count")
    assert not top.duplicated(["batch", "symbol"]).any(), (session, "duplicate top")
    assert set(top["symbol"]) == set(SYMBOLS), (session, "symbols")
    assert set(top["batch"]) == set(range(n_batches)), (session, "batches")
    assert top["batch"].between(0, n_batches - 1).all()
    assert trades["batch"].between(0, n_batches - 1).all()
    assert set(trades["symbol"]).issubset(SYMBOLS)
    assert set(trades["aggressor"]).issubset({"BUY", "SELL"})
    assert (trades["qty"] > 0).all() and (trades["price"] > 0).all()
    assert top["bid_px"].isna().equals(top["bid_qty"].isna())
    assert top["ask_px"].isna().equals(top["ask_qty"].isna())
    assert (top["bid_qty"].dropna() > 0).all()
    assert (top["ask_qty"].dropna() > 0).all()
    two_sided = top["bid_px"].notna() & top["ask_px"].notna()
    assert (top.loc[two_sided, "bid_px"] < top.loc[two_sided, "ask_px"]).all(), (session, "crossed book")
    audit = {
        "session": session,
        "top_rows": int(len(top)), "trade_rows": int(len(trades)),
        "missing_bid": int(top["bid_px"].isna().sum()),
        "missing_ask": int(top["ask_px"].isna().sum()),
        "non_trading": int((top["status"] != "TRADING").sum()),
        "self_trades": int(trades["self_trade"].sum()),
    }
    return top, trades, audit


def features_for_session(top: pd.DataFrame, trades: pd.DataFrame, n_batches: int) -> pd.DataFrame:
    signed = np.where(trades["aggressor"].eq("BUY"), trades["qty"], -trades["qty"])
    tape = trades.assign(signed_qty=signed).groupby(["batch", "symbol"], sort=False).agg(
        signed_qty=("signed_qty", "sum"), trade_qty=("qty", "sum")
    )
    top = top.merge(tape, on=["batch", "symbol"], how="left", validate="one_to_one")
    top[["signed_qty", "trade_qty"]] = top[["signed_qty", "trade_qty"]].fillna(0)
    frames = []
    for symbol in SYMBOLS:
        frame = top.loc[top["symbol"].eq(symbol)].sort_values("batch").reset_index(drop=True).copy()
        assert frame["batch"].to_numpy().tolist() == list(range(n_batches))
        frame["mid"] = (frame["bid_px"] + frame["ask_px"]) / 2
        frame["spread"] = frame["ask_px"] - frame["bid_px"]
        total = frame["bid_qty"] + frame["ask_qty"]
        frame["imbalance"] = (frame["bid_qty"] - frame["ask_qty"]) / total
        micro = (frame["ask_px"] * frame["bid_qty"] + frame["bid_px"] * frame["ask_qty"]) / total
        frame["micro_offset"] = micro - frame["mid"]
        volume_10s = frame["trade_qty"].rolling(20, min_periods=1).sum()
        signed_10s = frame["signed_qty"].rolling(20, min_periods=1).sum()
        frame["flow_10s"] = (signed_10s / volume_10s).fillna(0)
        frame["past_return_10s"] = frame["mid"] - frame["mid"].shift(20)
        for h in HORIZONS:
            frame[f"future_{h}s"] = frame["mid"].shift(-2 * h) - frame["mid"]
        # Orders decided after batch b are processed in b+1. This is still a
        # price proxy: an actual fill can be at a bid, ask, or resting quote.
        frame["delayed_60s"] = frame["mid"].shift(-121) - frame["mid"].shift(-1)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def etf_associations(frame: pd.DataFrame, n_batches: int) -> list[dict]:
    mids = frame.pivot(index="batch", columns="symbol", values="mid")
    assert len(mids) == n_batches
    basket = mids["BNKY"] + 2 * mids["BRRY"]
    gap = mids["HPPO"] - basket
    # The rolling center uses observations through the decision batch only.
    residual = gap - gap.rolling(240, min_periods=60).mean()
    rows = []
    for h in HORIZONS:
        lag = 2 * h
        outcomes = {
            "HPPO_change": mids["HPPO"].shift(-lag) - mids["HPPO"],
            "basket_change": basket.shift(-lag) - basket,
            "gap_change": gap.shift(-lag) - gap,
        }
        for outcome, target in outcomes.items():
            rows.append({"feature": outcome, "horizon_s": h, **association(residual, target)})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("research/stage1_results.json"))
    args = parser.parse_args()
    manifest = json.loads((args.data / "manifest.json").read_text())
    assert manifest["format"] == "pqt.history/2"
    assert manifest["batch_ms"] == 500
    assert manifest["sessions"] == list(DISCOVERY + VALIDATION + FINAL_TEST)
    assert [s["symbol"] for s in manifest["symbols"]] == list(SYMBOLS)
    n_batches = manifest["n_batches"]
    audits, descriptive, signals, delayed_rows, etf_rows = [], [], [], [], []
    for session in manifest["sessions"]:
        top, trades, audit = load_and_audit(args.data, session, n_batches)
        audits.append(audit)
        frame = features_for_session(top, trades, n_batches)
        for symbol, sub in frame.groupby("symbol", sort=False):
            descriptive.append({
                "session": session, "symbol": symbol,
                "median_spread_cents": float(sub["spread"].median()),
                "p90_spread_cents": float(sub["spread"].quantile(0.9)),
                "median_bid_qty": float(sub["bid_qty"].median()),
                "median_ask_qty": float(sub["ask_qty"].median()),
                "traded_shares": int(sub["trade_qty"].sum()),
                "two_sided_fraction": float(sub["mid"].notna().mean()),
            })
            if session not in DISCOVERY:
                continue
            for feature in FEATURES:
                for h in HORIZONS:
                    signals.append({
                        "session": session, "symbol": symbol, "feature": feature,
                        "horizon_s": h, **association(sub[feature], sub[f"future_{h}s"]),
                    })
                delayed_rows.append({
                    "session": session, "symbol": symbol, "feature": feature,
                    "horizon_s": 60, **association(sub[feature], sub["delayed_60s"]),
                })
        if session in DISCOVERY:
            for row in etf_associations(frame, n_batches):
                etf_rows.append({"session": session, "symbol": "HPPO_BASKET", **row})
    output = {
        "protocol": "Stage 1 descriptive statistics use all 24 sessions. Predictive signal estimates use s01-s16 only; s17-s20 and s21-s24 have no inspected future labels here.",
        "horizons_seconds": HORIZONS,
        "audits": audits,
        "descriptive": descriptive,
        "signal_summary": summarize(signals),
        "delayed_60s_summary": summarize(delayed_rows),
        "etf_summary": summarize(etf_rows),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Wrote {args.output}")
    print(f"Audited {len(audits)} sessions, {sum(a['top_rows'] for a in audits)} top rows, "
          f"{sum(a['trade_rows'] for a in audits)} trades")
    print("Predictive sessions:", ", ".join(DISCOVERY))
    print("Validation reserved:", ", ".join(VALIDATION))
    print("Final test reserved:", ", ".join(FINAL_TEST))


if __name__ == "__main__":
    main()
