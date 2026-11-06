"""Time four-symbol inference from a real observed history window.

Run: .venv/bin/python research/benchmark_inference.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data_loader import load_session
from models.inference import FairValuePredictor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--model", type=Path, default=Path("models/fair_value_v1_selected.json"))
    parser.add_argument("--output", type=Path,
                        default=Path("results/model_diagnostics/inference_benchmark.json"))
    parser.add_argument("--repeats", type=int, default=200)
    args = parser.parse_args()
    top, trades = load_session(args.data, "s01")
    top = top.loc[top["batch"].between(260, 500)]
    trades = trades.loc[trades["batch"].between(260, 500)]
    books = {int(batch): {row.symbol: {"bid_px": row.bid_px, "bid_qty": row.bid_qty,
                                            "ask_px": row.ask_px, "ask_qty": row.ask_qty}
                          for row in group.itertuples()}
             for batch, group in top.groupby("batch")}
    tape = {int(batch): [{"symbol": row.symbol, "qty": row.qty,
                          "aggressor": row.aggressor} for row in group.itertuples()]
            for batch, group in trades.groupby("batch")}
    history = [{"batch": batch, "top": books[batch], "trades": tape.get(batch, [])}
               for batch in range(260, 501)]
    predictor = FairValuePredictor(args.model)
    symbols = ("HPPO", "BNKY", "CRRT", "BRRY")
    samples = []
    for _ in range(args.repeats):
        start = time.perf_counter()
        for symbol in symbols:
            predictor.predict(history, symbol, 60)
        samples.append((time.perf_counter() - start) * 1000)
    result = {
        "session": "s01", "decision_batch": 500, "history_batches": len(history),
        "symbols_per_call": len(symbols), "repeats": args.repeats,
        "median_four_symbol_ms": statistics.median(samples),
        "p95_four_symbol_ms": sorted(samples)[int(0.95 * (len(samples) - 1))],
        "max_four_symbol_ms": max(samples), "exchange_batch_ms": 500,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
