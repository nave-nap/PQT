"""Replay frozen balanced and switchable depletion quote variants on s01-s20.

Run: .venv/bin/python research/run_liquidity_backtests.py --workers 4
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
import sys

import pandas as pd
from pqt_client import backtest_v2 as bt
from pqt_client.v2 import backtest_strategy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PQT_PRESET"] = "balanced"
from research.run_backtests import Telemetry, summarize  # noqa: E402
from strategies.competition_strategy import CompetitionStrategy  # noqa: E402
from strategies.liquidity_experimental import (  # noqa: E402
    DepletionProtection, DepletionAsymmetric, ReplenishmentAware, DepletionPlusOFI,
)

OUT = ROOT / "results" / "liquidity_depletion"
TRAIN = tuple(f"s{i:02d}" for i in range(1, 17))
VALIDATION = tuple(f"s{i:02d}" for i in range(17, 21))


class LifetimeTelemetry(Telemetry):
    def on_start(self):
        self.placements = {}
        self.lifetimes = []
        super().on_start()

    def on_ack(self, ack):
        if str(getattr(ack.order_type, "value", ack.order_type)) == "LIMIT":
            self.placements[ack.order_id] = ack.batch
        super().on_ack(ack)

    def on_cancelled(self, cancelled):
        start = self.placements.pop(cancelled.order_id, None)
        if start is not None:
            self.lifetimes.append(cancelled.batch - start)
        super().on_cancelled(cancelled)

    def on_fill(self, fill):
        if fill.remaining == 0:
            start = self.placements.pop(fill.order_id, None)
            if start is not None:
                self.lifetimes.append(fill.batch - start)
        super().on_fill(fill)


class Baseline(LifetimeTelemetry, CompetitionStrategy):
    pass


class Protection(LifetimeTelemetry, DepletionProtection):
    pass


class Asymmetric(LifetimeTelemetry, DepletionAsymmetric):
    pass


class Replenishment(LifetimeTelemetry, ReplenishmentAware):
    pass


class Combined(LifetimeTelemetry, DepletionPlusOFI):
    pass


class ProtectionStrict(Protection):
    STRICT_OWN_TOUCH = True


class ReplenishmentStrict(Replenishment):
    STRICT_OWN_TOUCH = True


class CombinedStrict(Combined):
    STRICT_OWN_TOUCH = True


CLASSES = {"baseline": Baseline, "protection": Protection, "asymmetric": Asymmetric,
           "replenishment": Replenishment, "depletion_plus_ofi": Combined,
           "protection_strict": ProtectionStrict,
           "replenishment_strict": ReplenishmentStrict,
           "depletion_plus_ofi_strict": CombinedStrict}


def run(job: tuple[str, str]) -> dict:
    variant, name = job
    logging.getLogger("pqt.strategy").setLevel(logging.ERROR)
    logging.getLogger("pqt.client").setLevel(logging.ERROR)
    session = bt.load_session(ROOT / "data", name)
    strategy = CLASSES[variant]()
    result = backtest_strategy(session, strategy)
    record = summarize(session, variant, strategy, result)
    record["avg_quote_lifetime_s"] = (sum(strategy.lifetimes) / len(strategy.lifetimes) / 2
                                       if strategy.lifetimes else None)
    record["cancellations"] = strategy.messages["cancel"]
    record["replacements"] = strategy.messages["replace"]
    record.update({f"ld_{key}": value for key, value in getattr(strategy, "ld_stats", {}).items()})
    mids = {s.symbol: [(t[s.symbol].bid_px + t[s.symbol].ask_px) / 2
                       if t[s.symbol].bid_px is not None and t[s.symbol].ask_px is not None else None
                       for t in session.tops] for s in session.symbols}
    full = {s.symbol: {"markout_mills": 0., "shares": 0,
                       "adverse_maker_shares": 0, "profitable_maker_shares": 0}
            for s in session.symbols}
    for fill in strategy.fill_events:
        b, symbol, qty = fill["batch"], fill["symbol"], fill["qty"]
        if b + 120 >= session.n_batches or mids[symbol][b + 120] is None:
            continue
        sign = 1 if fill["side"] == "BUY" else -1
        net = sign * (mids[symbol][b + 120] - fill["price"]) * 10 * qty - (
            fill["fee_mills"] + fill["penalty_mills"])
        item = full[symbol]
        item["markout_mills"] += net
        item["shares"] += qty
        if fill["liquidity"] == "MAKER":
            item["adverse_maker_shares" if net < 0 else "profitable_maker_shares"] += qty
    record["full_horizon_net_markout_usd"] = sum(v["markout_mills"] for v in full.values()) / 1000
    record["full_horizon_shares"] = sum(v["shares"] for v in full.values())
    record["full_horizon_net_mills_per_share"] = (1000*record["full_horizon_net_markout_usd"] /
                                                  record["full_horizon_shares"]
                                                  if record["full_horizon_shares"] else None)
    record["adverse_maker_shares"] = sum(v["adverse_maker_shares"] for v in full.values())
    record["profitable_maker_shares"] = sum(v["profitable_maker_shares"] for v in full.values())
    record["full_by_symbol"] = full
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--smoke-session", choices=TRAIN)
    parser.add_argument("--validation-only", action="store_true")
    parser.add_argument("--strict-own-touch", action="store_true",
                        help="Replay key variants with live-style own-touch abstention")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    sessions = (args.smoke_session,) if args.smoke_session else (
        VALIDATION if args.validation_only else TRAIN + VALIDATION)
    variants = (("baseline", "protection_strict", "replenishment_strict",
                 "depletion_plus_ofi_strict") if args.strict_own_touch else
                ("baseline", "protection", "asymmetric", "replenishment",
                 "depletion_plus_ofi"))
    jobs = [(variant, name) for name in sessions for variant in variants]
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run, job): job for job in jobs}
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            print(f"{row['session']} {row['variant']}: "
                  f"60s=${row['full_horizon_net_markout_usd']:.2f} "
                  f"fills={row['fills']} rejects={row['reject_count']}", flush=True)
    rows.sort(key=lambda x: (x["session"], x["variant"]))
    prefix = ("smoke_" if args.smoke_session else "") + ("strict_" if args.strict_own_touch else "") + ("validation_" if args.validation_only else "")
    (OUT / f"{prefix}backtest_raw.json").write_text(json.dumps(rows, indent=2) + "\n")
    fields = ("session", "split", "variant", "estimated_pnl_usd", "net_markout_60_usd",
              "full_horizon_net_markout_usd", "full_horizon_shares",
              "full_horizon_net_mills_per_share", "fills", "maker_fills", "taker_fills",
              "shares", "maker_shares", "taker_shares", "net_fees_usd", "cancellations",
              "replacements", "messages", "avg_quote_lifetime_s", "order_to_trade_ratio",
              "max_abs_position", "max_abs_worst_position", "max_abs_exposure",
              "max_abs_worst_exposure", "max_open_orders", "max_messages_batch", "reject_count",
              "adverse_maker_shares", "profitable_maker_shares", "ld_valid", "ld_own_touch",
              "ld_active_side_batches", "ld_quote_interventions")
    pd.DataFrame([{key: row.get(key) for key in fields} for row in rows]).to_csv(
        OUT / f"{prefix}strategy_comparison.csv", index=False)
    symbols = []
    for row in rows:
        for symbol, item in row["full_by_symbol"].items():
            symbols.append({"session": row["session"], "split": row["split"],
                            "variant": row["variant"], "symbol": symbol,
                            "full_horizon_net_markout_usd": item["markout_mills"] / 1000,
                            "full_horizon_shares": item["shares"],
                            "adverse_maker_shares": item["adverse_maker_shares"],
                            "profitable_maker_shares": item["profitable_maker_shares"],
                            "estimated_pnl_usd": row["by_symbol"][symbol]["estimated_pnl_usd"]})
    pd.DataFrame(symbols).to_csv(OUT / f"{prefix}strategy_by_symbol.csv", index=False)
    (OUT / f"{prefix}backtest_metadata.json").write_text(json.dumps({
        "sessions": list(sessions), "variants": list(variants),
        "competition_preset": "balanced", "sdk_client_version": version("pqt-client"),
        "rules": asdict(bt.Rules()), "full_horizon": "exclude last 120 fill batches",
        "policy_file": "policy.json", "predictor_file": "predictor.json",
        "command": " ".join(sys.argv),
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
