"""Descriptive same-price replenishment event study, with future labels only offline.

Run: .venv/bin/python research/study_replenishment.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "liquidity_depletion"
TRAIN = tuple(f"s{i:02d}" for i in range(1, 17))
VALIDATION = tuple(f"s{i:02d}" for i in range(17, 21))


def main() -> None:
    rows = []
    event_rows = []
    for split, names in (("train", TRAIN), ("validation", VALIDATION)):
        for name in names:
            top = pd.read_csv(ROOT / "data" / f"{name}.top.csv")
            trades = pd.read_csv(ROOT / "data" / f"{name}.trades.csv")
            for symbol, block in top.groupby("symbol", sort=False):
                block = block.sort_values("batch").reset_index(drop=True)
                mid = (block.bid_px + block.ask_px) / 2
                for side in ("bid", "ask"):
                    px, qty = block[f"{side}_px"], block[f"{side}_qty"]
                    same = px.eq(px.shift(1)) & px.notna() & qty.notna() & qty.shift(1).notna()
                    replen = (qty - qty.shift(1)).where(same).clip(lower=0)
                    event = replen > 0
                    future_valid = block.batch <= int(block.batch.max()) - 120
                    persistence = event & future_valid
                    for k in range(1, 6):
                        persistence &= px.shift(-k).eq(px) & qty.shift(-k).ge(
                            qty - np.maximum(1, np.floor(replen / 2)))
                    event_trade = trades[(trades.symbol == symbol) &
                                         (trades.aggressor == ("SELL" if side == "bid" else "BUY"))]
                    price_col = f"{side}_px"
                    aligned = (event_trade.merge(block[["batch", price_col]], on="batch", how="inner")
                               .loc[lambda f: f.price.eq(f[price_col])]
                               .groupby("batch").qty.sum())
                    aligned_qty = block.batch.map(aligned).fillna(0).to_numpy()
                    direction = 1 if side == "bid" else -1
                    for index in np.flatnonzero(event.to_numpy() & future_valid.to_numpy()):
                        event_rows.append({"split": split, "session": name, "symbol": symbol,
                                           "side": side, "batch": int(block.batch.iloc[index]),
                                           "replen_shares": float(replen.iloc[index]),
                                           "aligned_aggressive_qty": float(aligned_qty[index]),
                                           "persistent_5_batches": bool(persistence.iloc[index]),
                                           "signed_future_10s_cents": direction * float(mid.iloc[index+20]-mid.iloc[index]),
                                           "signed_future_60s_cents": direction * float(mid.iloc[index+120]-mid.iloc[index]),
                                           "next_same_price": bool(px.iloc[index+1] == px.iloc[index])})
                    eligible = block.loc[event & future_valid]
                    if len(eligible):
                        indices = eligible.index.to_numpy()
                        rows.append({"split": split, "session": name, "symbol": symbol,
                                     "side": side, "events": len(indices),
                                     "mean_replen_shares": float(replen.iloc[indices].mean()),
                                     "persist_5_fraction": float(persistence.iloc[indices].mean()),
                                     "aggressive_trade_same_batch_fraction": float((aligned_qty[indices] > 0).mean()),
                                     "mean_signed_future_10s_cents": float((direction*(mid.shift(-20)-mid)).iloc[indices].mean()),
                                     "mean_signed_future_60s_cents": float((direction*(mid.shift(-120)-mid)).iloc[indices].mean())})
            print(f"replenishment {name}", flush=True)
    pd.DataFrame(rows).to_csv(OUT / "replenishment_event_summary.csv", index=False)
    pd.DataFrame(event_rows).to_csv(OUT / "replenishment_events.csv", index=False)
    print(f"wrote {len(event_rows)} event labels")


if __name__ == "__main__":
    main()
