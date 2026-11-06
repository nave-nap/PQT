"""Freeze a small depletion policy from training statistics only.

Run: .venv/bin/python research/build_liquidity_policy.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "liquidity_depletion"


def main() -> None:
    predictor = json.loads((OUT / "predictor.json").read_text())
    signal = pd.read_csv(OUT / "session_signal_metrics.csv")
    ofi = json.loads((ROOT / "results" / "orderflow_imbalance" /
                      "training_thresholds.json").read_text())["thresholds"]
    symbols = {}
    for symbol, model in predictor["symbols"].items():
        edges = model["events"]["next_ask_up"]["table_edges"]
        block = signal[(signal.split == "train") & (signal.symbol == symbol) &
                       (signal.feature == "net_dep_40") & (signal.horizon_s == 60)]
        symbols[symbol] = {"low_threshold": edges[0], "high_threshold": edges[3],
                           "train_low_return_cents": float(block.q1_mean_cents.mean()),
                           "train_high_return_cents": float(block.q5_mean_cents.mean()),
                           "ofi_abs_threshold": ofi[symbol]}
    policy = {"version": "liquidity-policy-v1", "training_sessions": [f"s{i:02d}" for i in range(1,17)],
              "enabled_symbols": ["HPPO"],
              "reason": "HPPO had negative training fill markout under strong adverse depletion; BRRY did not",
              "window_batches": 40, "hysteresis_exit_ratio": .65,
              "min_hold_batches": 6, "replenishment_recovery_ratio": .5,
              "max_widen_cents": 6, "symbols": symbols,
              "trading_enabled_by_default": False}
    (OUT / "policy.json").write_text(json.dumps(policy, indent=2) + "\n")
    print(f"wrote {OUT / 'policy.json'}")


if __name__ == "__main__":
    main()
