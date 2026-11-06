"""Conservative, read-only HPPO IOC opportunity screen; no orders are sent.

Run: .venv/bin/python research/assess_liquidity_taker.py
Samples once per 10 seconds to reduce overlapping labels. Future prices are
used only to evaluate the decision, never to form the independent value signal.
"""

from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from models.inference import FairValuePredictor  # noqa: E402
from research.run_liquidity_signals import load_features  # noqa: E402

OUT = ROOT / "results" / "liquidity_depletion"
PREDICTOR = FairValuePredictor(ROOT / "models" / "fair_value_v1_selected.json")
POLICY = __import__("json").loads((OUT / "policy.json").read_text())
HPPO = POLICY["symbols"]["HPPO"]


def main() -> None:
    rows = []
    for i in range(1, 21):
        name = f"s{i:02d}"
        split = "train" if i <= 16 else "validation"
        frames = load_features(name)
        hppo, brry = frames["HPPO"], frames["BRRY"]
        trades = pd.read_csv(ROOT / "data" / f"{name}.trades.csv")
        tape = trades.loc[trades.symbol.eq("HPPO")].copy()
        tape["signed"] = np.where(tape.aggressor.eq("BUY"), tape.qty, -tape.qty)
        flow = tape.groupby("batch")[["signed", "qty"]].sum().reindex(hppo.batch, fill_value=0)
        signed = flow.signed.rolling(60, min_periods=1).sum().to_numpy()
        volume = flow.qty.rolling(60, min_periods=1).sum().to_numpy()
        flow30 = np.divide(signed, volume, out=np.zeros_like(signed, dtype=float), where=volume>0)
        lead = brry.mid.diff(20).to_numpy()
        for b in range(240, len(hppo)-121, 20):
            row = hppo.iloc[b]
            dep = row.net_dep_40
            if not np.isfinite(dep) or not np.isfinite(row.mid):
                continue
            for side, extreme in (("BUY", dep >= HPPO["high_threshold"]),
                                  ("SELL", dep <= HPPO["low_threshold"])):
                if not extreme:
                    continue
                values = {"mid": row.mid, "momentum_10s": row.momentum_10s,
                          "signed_flow_30s": flow30[b], "book_imbalance": row.imbalance,
                          "micro_minus_mid": row.micro_dev,
                          "lead_BRRY_10s": lead[b]}
                if not all(np.isfinite(v) for v in values.values()):
                    continue
                prediction = PREDICTOR.predict_from_features("HPPO", 60, values)
                current_touch = row.ask_px if side == "BUY" else row.bid_px
                nxt = hppo.iloc[b+1]
                next_touch = nxt.ask_px if side == "BUY" else nxt.bid_px
                available = bool(np.isfinite(next_touch) and
                                 (next_touch <= current_touch if side == "BUY" else
                                  next_touch >= current_touch))
                direction = 1 if side == "BUY" else -1
                edge = direction * (prediction["predicted_future_price_cents"] - current_touch)
                uncertainty = prediction["uncertainty_cents"]
                # Fee is 0.4 cents/share in the frozen rules. One extra cent
                # allows for changed execution price beyond the observed quote.
                threshold = .4 + 1.0 + uncertainty
                future_mid = hppo.mid.iloc[b+121]
                realized = (direction * (future_mid - next_touch) - .4
                            if available and np.isfinite(future_mid) else np.nan)
                rows.append({"split": split, "session": name, "batch": b, "side": side,
                             "depletion": dep, "predicted_edge_cents": edge,
                             "uncertainty_cents": uncertainty, "required_edge_cents": threshold,
                             "passes_conservative_gate": edge > threshold,
                             "next_batch_price_available": available,
                             "realized_60s_net_cents_if_available": realized})
        print(f"taker screen {name}", flush=True)
    result = pd.DataFrame(rows)
    result.to_csv(OUT / "taker_opportunity_screen.csv", index=False)
    summary = result.groupby(["split", "side"], as_index=False).agg(
        screened=("batch", "size"), passes=("passes_conservative_gate", "sum"),
        next_price_available=("next_batch_price_available", "mean"),
        mean_predicted_edge_cents=("predicted_edge_cents", "mean"),
        mean_realized_net_if_available=("realized_60s_net_cents_if_available", "mean"))
    summary.to_csv(OUT / "taker_screen_summary.csv", index=False)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
