"""Typed loading and basic market variables for the PQT historical data pack.

Prices are integer cents, quantities are shares, and batch b is the market
snapshot published at the end of (b + 1) * batch_ms milliseconds. There are no
wall-clock timestamps. Missing top-of-book sides remain pandas NA, not zero.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

TOP_COLUMNS = ("batch", "symbol", "bid_px", "bid_qty", "ask_px", "ask_qty", "status")
TRADE_COLUMNS = ("batch", "symbol", "price", "qty", "aggressor", "self_trade", "aux")


def load_manifest(data_dir: str | Path) -> dict:
    """Load the data-pack manifest without silently substituting defaults."""
    with (Path(data_dir) / "manifest.json").open(encoding="utf-8") as file:
        return json.load(file)


def load_session(data_dir: str | Path, session: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read one session's top and trade CSVs with nullable price/size columns."""
    root = Path(data_dir)
    top = pd.read_csv(
        root / f"{session}.top.csv",
        dtype={"batch": "int64", "symbol": "string", "bid_px": "Int64", "bid_qty": "Int64",
               "ask_px": "Int64", "ask_qty": "Int64", "status": "string"},
    )
    trades = pd.read_csv(
        root / f"{session}.trades.csv",
        dtype={"batch": "int64", "symbol": "string", "price": "int64", "qty": "int64",
               "aggressor": "string", "self_trade": "string", "aux": "string"},
    )
    # Preserve the wire encoding check for the validator; analysis gets booleans.
    invalid = ~trades["self_trade"].isin(["true", "false"])
    if invalid.any():
        raise ValueError(f"{session}: invalid self_trade encoding in {int(invalid.sum())} rows")
    trades["self_trade"] = trades["self_trade"].eq("true")
    return top, trades


def add_market_variables(top: pd.DataFrame) -> pd.DataFrame:
    """Add public top-of-book mid, spread, imbalance, and microprice.

    These variables are available only when both sides have a price and size.
    The caller must preserve session and batch order before making lagged features.
    """
    result = top.copy()
    bid, ask = result["bid_px"].astype("Float64"), result["ask_px"].astype("Float64")
    bid_qty = result["bid_qty"].astype("Float64")
    ask_qty = result["ask_qty"].astype("Float64")
    total_qty = bid_qty + ask_qty
    result["mid_px"] = (bid + ask) / 2
    result["spread"] = ask - bid
    result["imbalance"] = ((bid_qty - ask_qty) / total_qty).where(total_qty > 0)
    result["microprice"] = ((ask * bid_qty + bid * ask_qty) / total_qty).where(total_qty > 0)
    return result


def aggregate_trade_flow(trades: pd.DataFrame) -> pd.DataFrame:
    """Aggregate signed shares by observed batch and symbol.

    BUY means the *arriving* order bought and receives a positive sign.
    SELL means the arriving order sold and receives a negative sign.
    """
    if not trades["aggressor"].isin(["BUY", "SELL"]).all():
        raise ValueError("aggressor must be BUY or SELL")
    signed = trades["qty"].where(trades["aggressor"].eq("BUY"), -trades["qty"])
    return (trades.assign(signed_qty=signed)
            .groupby(["batch", "symbol"], sort=False, as_index=False)
            .agg(trade_qty=("qty", "sum"), signed_qty=("signed_qty", "sum")))


def batch_end_seconds(batch: int | pd.Series, batch_ms: int) -> float | pd.Series:
    """Elapsed seconds at the end of batch b: (b + 1) * batch_ms / 1000."""
    return (batch + 1) * batch_ms / 1000
