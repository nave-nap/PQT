"""Validate every PQT history file against DATA.md and its manifest.

Run: .venv/bin/python research/validate_data.py --data data --output results/baseline/data_validation.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from data_loader import TOP_COLUMNS, TRADE_COLUMNS, add_market_variables, load_manifest, load_session


def check(condition: bool, message: str) -> None:
    if not bool(condition):
        raise ValueError(message)


def validate_manifest(manifest: dict) -> tuple[list[str], int, int]:
    check(manifest.get("format") == "pqt.history/2", "wrong history format")
    batch_ms, n_batches = manifest.get("batch_ms"), manifest.get("n_batches")
    check(isinstance(batch_ms, int) and batch_ms > 0, "invalid batch_ms")
    check(isinstance(n_batches, int) and n_batches > 0, "invalid n_batches")
    sessions = manifest.get("sessions")
    check(sessions == [f"s{i:02d}" for i in range(1, 25)], "expected s01 through s24")
    symbols = manifest.get("symbols")
    check(isinstance(symbols, list) and len(symbols) == 4, "expected four symbol definitions")
    names = [entry["symbol"] for entry in symbols]
    check(names == ["HPPO", "BNKY", "CRRT", "BRRY"], "unexpected symbol names/order")
    for entry in symbols:
        for key in ("tick_size", "position_limit", "max_order_qty"):
            check(isinstance(entry.get(key), int) and entry[key] > 0,
                  f"{entry['symbol']}: invalid {key}")
        check(isinstance(entry.get("exposure_weight_milli"), int),
              f"{entry['symbol']}: invalid exposure weight")
    return names, batch_ms, n_batches


def validate_session(data_dir: Path, session: str, manifest: dict) -> dict:
    names, batch_ms, n_batches = validate_manifest(manifest)
    top, trades = load_session(data_dir, session)
    check(tuple(top.columns) == TOP_COLUMNS, f"{session}: top columns")
    check(tuple(trades.columns) == TRADE_COLUMNS, f"{session}: trade columns")
    check(len(top) == n_batches * len(names), f"{session}: top row count")
    check(np.array_equal(top["batch"].to_numpy(), np.repeat(np.arange(n_batches), len(names))),
          f"{session}: top batch order or missing batches")
    check(np.array_equal(top["symbol"].to_numpy(), np.tile(names, n_batches)),
          f"{session}: top symbol order")
    check(top["status"].notna().all(), f"{session}: missing status")
    check(top["status"].eq("TRADING").all(), f"{session}: unexpected historical status")
    check(top["bid_px"].notna().eq(top["bid_qty"].notna()).all(),
          f"{session}: bid price/size missing mismatch")
    check(top["ask_px"].notna().eq(top["ask_qty"].notna()).all(),
          f"{session}: ask price/size missing mismatch")
    for side in ("bid", "ask"):
        check(top[f"{side}_px"].dropna().gt(0).all(), f"{session}: nonpositive {side} price")
        check(top[f"{side}_qty"].dropna().gt(0).all(), f"{session}: nonpositive {side} size")
    both = top["bid_px"].notna() & top["ask_px"].notna()
    check(top.loc[both, "bid_px"].lt(top.loc[both, "ask_px"]).all(),
          f"{session}: crossed or locked top")
    check(trades[["symbol", "aggressor", "aux"]].notna().all().all(),
          f"{session}: missing trade field")
    check(trades["batch"].between(0, n_batches - 1).all(), f"{session}: trade batch range")
    check(trades["symbol"].isin(names).all(), f"{session}: trade symbol")
    check(trades["aggressor"].isin(["BUY", "SELL"]).all(), f"{session}: trade direction")
    check(trades["price"].gt(0).all() and trades["qty"].gt(0).all(),
          f"{session}: nonpositive trade price/size")
    check(trades["aux"].eq("{}").all(), f"{session}: unexpected historical aux")
    # Trades are ordered by batch, symbol, then execution order within each group.
    rank = {symbol: i for i, symbol in enumerate(names)}
    sequence = trades["batch"].to_numpy() * len(names) + trades["symbol"].map(rank).to_numpy()
    check(np.all(np.diff(sequence) >= 0), f"{session}: trade batch/symbol order")
    for entry in manifest["symbols"]:
        symbol, tick = entry["symbol"], entry["tick_size"]
        rows = top.loc[top["symbol"].eq(symbol)]
        for column in ("bid_px", "ask_px"):
            check(rows[column].dropna().mod(tick).eq(0).all(),
                  f"{session}: {symbol} {column} tick")
        check(trades.loc[trades["symbol"].eq(symbol), "price"].mod(tick).eq(0).all(),
              f"{session}: {symbol} trade tick")
    enriched = add_market_variables(top)
    check(enriched.loc[~both, ["mid_px", "spread", "microprice"]].isna().all().all(),
          f"{session}: missing side represented as a number")
    return {
        "session": session,
        "top_rows": len(top), "trade_rows": len(trades),
        "missing_bid_rows": int(top["bid_px"].isna().sum()),
        "missing_ask_rows": int(top["ask_px"].isna().sum()),
        "two_sided_rows": int(both.sum()),
        "buy_aggressor_trades": int(trades["aggressor"].eq("BUY").sum()),
        "sell_aggressor_trades": int(trades["aggressor"].eq("SELL").sum()),
        "self_trade_rows": int(trades["self_trade"].sum()),
        "last_batch": int(top["batch"].iloc[-1]),
        "duration_seconds": n_batches * batch_ms / 1000,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("results/baseline/data_validation.json"))
    args = parser.parse_args()
    manifest = load_manifest(args.data)
    names, batch_ms, n_batches = validate_manifest(manifest)
    sessions = [validate_session(args.data, name, manifest) for name in manifest["sessions"]]
    summary = {
        "status": "PASS", "format": manifest["format"], "batch_ms": batch_ms,
        "n_batches": n_batches, "symbols": names, "sessions_validated": len(sessions),
        "total_top_rows": sum(row["top_rows"] for row in sessions),
        "total_trade_rows": sum(row["trade_rows"] for row in sessions),
        "total_missing_bid_rows": sum(row["missing_bid_rows"] for row in sessions),
        "total_missing_ask_rows": sum(row["missing_ask_rows"] for row in sessions),
        "sessions": sessions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"PASS: {len(sessions)} sessions, {summary['total_top_rows']:,} top rows, "
          f"{summary['total_trade_rows']:,} trades; wrote {args.output}")


if __name__ == "__main__":
    main()
