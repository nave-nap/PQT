"""Common, session-bounded model rows for training and validation.

Every feature is observable at the end of batch b. The label is the public
midprice at b + 1 + 2*h, so an order decided at b first has a chance to execute
at b + 1, then h seconds elapse. The official hidden-value score is unavailable.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from analyze_signals import load_features
from data_loader import add_market_variables, load_session

HORIZONS = (10, 30, 60)
TRAIN = tuple(f"s{i:02d}" for i in range(1, 17))
VALIDATION = tuple(f"s{i:02d}" for i in range(17, 21))
FINAL_TEST = tuple(f"s{i:02d}" for i in range(21, 25))
SYMBOLS = ("HPPO", "BNKY", "CRRT", "BRRY")

# Chosen before validation from Stage 2 evidence. Do not expand this list in
# response to validation results; that would turn the four sessions into training.
FEATURES = {
    "HPPO": ("momentum_10s", "signed_flow_30s", "book_imbalance",
             "micro_minus_mid", "lead_BRRY_10s"),
    "BNKY": ("book_imbalance", "micro_minus_mid", "momentum_10s",
             "signed_flow_30s", "lead_HPPO_10s"),
    "CRRT": ("lead_HPPO_10s", "momentum_30s", "book_imbalance",
             "micro_minus_mid", "signed_flow_30s"),
    "BRRY": ("etf_raw_gap", "etf_gap_deviation_120s", "lead_HPPO_10s",
             "signed_flow_30s", "book_imbalance"),
}
CROSS_FEATURE = {
    "HPPO": "lead_BRRY_10s",
    "BNKY": "lead_HPPO_10s",
    "CRRT": "lead_HPPO_10s",
    "BRRY": "etf_raw_gap",
}
WARMUP_BATCHES = 240


def shifted(values: np.ndarray, batches: int) -> np.ndarray:
    result = np.full(len(values), np.nan)
    result[:-batches] = values[batches:]
    return result


def session_model_rows(data_dir: Path, session: str,
                       symbols: tuple[str, ...] = SYMBOLS) -> dict[tuple[str, int], pd.DataFrame]:
    """Return identical eligible rows for every candidate model of each target."""
    feature_arrays, mids, _ = load_features(data_dir, session, list(symbols))
    top, _ = load_session(data_dir, session)
    market = add_market_variables(top)
    n = len(mids[symbols[0]])
    rows = {}
    for symbol in symbols:
        current = market.loc[market["symbol"].eq(symbol)].sort_values("batch")
        assert np.array_equal(current["batch"].to_numpy(), np.arange(n))
        mid = mids[symbol]
        micro = current["microprice"].to_numpy(dtype=float, na_value=np.nan)
        bid = current["bid_px"].to_numpy(dtype=float, na_value=np.nan)
        ask = current["ask_px"].to_numpy(dtype=float, na_value=np.nan)
        bid_qty = current["bid_qty"].to_numpy(dtype=float, na_value=np.nan)
        ask_qty = current["ask_qty"].to_numpy(dtype=float, na_value=np.nan)
        rv10 = np.sqrt(pd.Series(mid).diff().pow(2).rolling(20, min_periods=10).sum()).to_numpy()
        base = pd.DataFrame({
            "session": session, "batch": np.arange(n), "mid": mid, "microprice": micro,
            "bid": bid, "ask": ask, "bid_qty": bid_qty, "ask_qty": ask_qty,
            "spread": ask - bid, "rv10": rv10,
            "next_bid": shifted(bid, 1), "next_ask": shifted(ask, 1),
            "next_bid_qty": shifted(bid_qty, 1), "next_ask_qty": shifted(ask_qty, 1),
        })
        for feature in FEATURES[symbol]:
            base[feature] = feature_arrays[symbol][feature]
        for horizon in HORIZONS:
            frame = base.copy()
            frame["future_mid"] = shifted(mid, 1 + 2 * horizon)
            frame["target_return"] = frame["future_mid"] - frame["mid"]
            eligible = ["mid", "microprice", "spread", "rv10", "future_mid"] + list(FEATURES[symbol])
            frame = frame.loc[frame["batch"].ge(WARMUP_BATCHES)].replace(
                [np.inf, -np.inf], np.nan).dropna(subset=eligible)
            rows[(symbol, horizon)] = frame.reset_index(drop=True)
    return rows
