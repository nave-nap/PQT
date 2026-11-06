"""Synthetic feature faults and conservative fill-value sensitivity.

Run: .venv/bin/python research/run_liquidity_stress.py
These are controlled probes, not realized exchange outcomes.
"""

from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from strategies.liquidity_depletion import LiquidityStream  # noqa: E402
from strategies.orderflow_imbalance import Book  # noqa: E402

OUT = ROOT / "results" / "liquidity_depletion"


class Trade:
    def __init__(self, symbol, aggressor, price, qty):
        self.symbol, self.aggressor, self.price, self.qty = symbol, aggressor, price, qty


def probe(scenario, observed, expectation, source="synthetic_stream"):
    return {"scenario": scenario, "source": source, "observed": str(observed),
            "expectation": str(expectation), "pass": bool(observed == expectation)}


def main():
    rows = []
    stream = LiquidityStream((2, 5))
    stream.update("X", 0, Book(100, 20, 102, 20))
    f = stream.update("X", 1, Book(100, 5, 102, 20))
    rows.append(probe("large_no_trade_depth_loss", f.values["bid_no_trade_depletion"], 1))
    stream.update("X", 2, Book(100, 5, 102, 5))
    stream.update("X", 3, Book(100, 5, 102, 20))
    for b in range(4, 9):
        f = stream.update("X", b, Book(100, 5, 102, 20))
    rows.append(probe("rapid_replenishment_persistence", f.values["ask_replenishment_persistence"], 1.0))
    f = stream.update("X", 9, Book(100, 5, 102, 20), (Trade("X", "BUY", 102, 10),))
    rows.append(probe("aggressive_trades_absorbed_without_reprice",
                      (f.values["ask_depletion"], f.values["ask_aligned_trade_qty"]), (0, 10)))
    f = stream.update("X", 10, Book(101, 100, 105, 100))
    rows.append(probe("sudden_price_jump_new_queue",
                      (f.values["bid_eligible"], f.values["ask_eligible"],
                       f.values["bid_depletion"], f.values["ask_depletion"]), (0, 0, 0, 0)))
    thin = LiquidityStream((2,))
    thin.update("BRRY", 0, Book(100, 4, 102, 2))
    f = thin.update("BRRY", 1, Book(100, 1, 102, 2))
    rows.append(probe("thin_brry_depth_guard", f.values["bid_relative_depletion"], .6))
    own = stream.update("X", 11, Book(101, 100, 105, 100), own_touch=True)
    rows.append(probe("own_orders_at_touch", own.quality, "own_touch"))
    rows.append(probe("signal_reverses_before_execution",
                      "requires next-batch quote recheck", "requires next-batch quote recheck",
                      "execution_semantics"))
    rows.append(probe("partial_ioc_fills", "IOC disabled: no orders submitted",
                      "IOC disabled: no orders submitted", "design_gate"))
    halt = stream.update("X", 12, Book(None, None, None, None, "HALTED"))
    wake = stream.update("X", 13, Book(100, 10, 102, 10))
    gap = stream.update("X", 15, Book(100, 10, 102, 10))
    rows.append(probe("halt_and_reconnect_reset", (halt.quality, wake.quality, gap.quality),
                      ("invalid", "warmup", "warmup")))
    simultaneous = LiquidityStream((2,))
    for s in ("HPPO", "BNKY", "BRRY"):
        simultaneous.update(s, 0, Book(100, 10, 102, 10))
    shocks = [simultaneous.update(s, 1, Book(101, 10, 103, 10)).values["bid_px_change"]
              for s in ("HPPO", "BNKY", "BRRY")]
    rows.append(probe("simultaneous_etf_basket_changes", shocks, [1, 1, 1]))
    f = thin.update("BRRY", 2, Book(100, 0, 102, 1))
    rows.append(probe("extended_low_liquidity_empty_side", f.quality, "invalid"))

    backtests = pd.read_csv(OUT / "strategy_comparison.csv")
    for split, block in backtests.groupby("split"):
        grouped = block.groupby("variant").agg(markout=("full_horizon_net_markout_usd", "sum"),
                                                shares=("full_horizon_shares", "sum"))
        for cents in (1, 2):
            baseline = grouped.loc["baseline"]
            baseline_stressed = baseline.markout - baseline.shares * cents / 100
            for variant, values in grouped.iterrows():
                stressed = values.markout - values.shares * cents / 100
                rows.append({"scenario": f"uniform_fill_value_haircut_{cents}c",
                             "source": "historical_replay_sensitivity", "split": split,
                             "variant": variant, "observed": round(stressed-baseline_stressed, 3),
                             "expectation": "USD delta vs identically stressed frozen baseline",
                             "pass": True})
    # Crowding/queue position cannot be reproduced from Level 1 snapshots.
    rows.append({"scenario": "crowded_best_price_queue", "source": "model_limit",
                 "observed": "true queue loss unobservable; see uniform fill-value haircuts",
                 "expectation": "no exchange-performance claim", "pass": True})
    result = pd.DataFrame(rows)
    result.to_csv(OUT / "stress_tests.csv", index=False)
    assert result["pass"].all()
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
