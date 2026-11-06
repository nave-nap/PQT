"""Condition frozen-baseline fill markout on prior depletion and prior OFI.

Run: .venv/bin/python research/attribute_liquidity_fills.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.run_liquidity_signals import load_features, OUT, TRAIN, VALIDATION, SYMBOLS  # noqa: E402


def main() -> None:
    original = pd.read_csv(ROOT / "results" / "orderflow_imbalance" / "followup" /
                           "baseline_fill_attribution.csv")
    thresholds = json.loads((OUT / "predictor.json").read_text())["symbols"]
    rows = []
    for name in TRAIN + VALIDATION:
        frames = load_features(name)
        fills = original[original.session == name].copy()
        for symbol in SYMBOLS:
            block = fills[fills.symbol == symbol].copy()
            if block.empty:
                continue
            features = frames[symbol]
            prior = (block.batch.to_numpy() - 1).clip(min=0)
            block["prior_net_dep_40"] = features.net_dep_40.iloc[prior].to_numpy()
            block["prior_net_replen_20"] = features.net_replen_20.iloc[prior].to_numpy()
            block["prior_cycle_diff_20"] = features.cycle_diff_20.iloc[prior].to_numpy()
            block["prior_ofi_norm40"] = features.ofi_norm40.iloc[prior].to_numpy()
            block["prior_spread_cents"] = features.spread.iloc[prior].to_numpy()
            edges = thresholds[symbol]["events"]["next_ask_up"]["table_edges"]
            sign = block.side.map({"BUY": -1, "SELL": 1})
            block["prior_adverse_depletion"] = sign * block.prior_net_dep_40
            block["depletion_category"] = "middle"
            block.loc[((block.side == "SELL") & (block.prior_net_dep_40 >= edges[3])) |
                      ((block.side == "BUY") & (block.prior_net_dep_40 <= edges[0])),
                      "depletion_category"] = "strong_adverse"
            block.loc[((block.side == "SELL") & (block.prior_net_dep_40 <= edges[0])) |
                      ((block.side == "BUY") & (block.prior_net_dep_40 >= edges[3])),
                      "depletion_category"] = "strong_favorable"
            block.loc[block.prior_net_dep_40.isna(), "depletion_category"] = "unavailable"
            rows.append(block)
        print(f"attributed {name}", flush=True)
    all_fills = pd.concat(rows, ignore_index=True)
    all_fills.to_csv(OUT / "baseline_fill_attribution.csv", index=False)
    groups = []
    for key, block in all_fills.groupby(["split", "session", "symbol", "depletion_category"]):
        qty = block.qty.sum()
        groups.append(dict(zip(["split", "session", "symbol", "depletion_category"], key)) | {
            "fills": len(block), "shares": qty,
            "net_markout_usd": block.net_markout_mills.sum() / 1000,
            "net_mills_per_share": block.net_markout_mills.sum() / qty,
            "negative_markout_share_fraction": block.loc[block.net_mills_per_share < 0, "qty"].sum() / qty,
            "mean_prior_spread_cents": (block.prior_spread_cents * block.qty).sum() / qty,
            "mean_prior_ofi_adverse": (block.prior_adverse_pressure * block.qty).sum() / qty,
        })
    pd.DataFrame(groups).to_csv(OUT / "baseline_fill_categories.csv", index=False)
    print(f"wrote {len(all_fills)} fill rows")


if __name__ == "__main__":
    main()
