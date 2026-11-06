"""Reproducible PQT v0.2.3 replay with fill/risk telemetry and sealed holdout phases.

Usage:
  .venv/bin/python research/run_backtests.py --phase tune --workers 4
  .venv/bin/python research/run_backtests.py --phase trainval --workers 4
  .venv/bin/python research/run_backtests.py --phase holdout --workers 4

The official SDK backtest_strategy/run_backtest and default Rules are never changed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from importlib.metadata import version
import json
import logging
import math
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pqt_client import backtest_v2 as bt  # noqa: E402
from pqt_client.v2 import backtest_strategy  # noqa: E402
from strategies.market_maker import MarketMaker  # noqa: E402
from strategies.our_strategy import OurStrategy  # noqa: E402

TRAIN = tuple(f"s{i:02d}" for i in range(1, 17))
VALIDATION = tuple(f"s{i:02d}" for i in range(17, 21))
HOLDOUT = tuple(f"s{i:02d}" for i in range(21, 25))
TUNE_SESSIONS = ("s02", "s06", "s10", "s14")
VARIANTS = ("official", "microprice", "cross_asset", "predictive",
            "predictive_basket", "combined")
TUNE_CASES = {
    "base": {},
    "width_4": {"params": {"half_width": 4}},
    "size_10": {"params": {"quote_size": 10}},
    "skew_6": {"params": {"inventory_skew": 6}},
    "edge_2": {"params": {"min_quote_edge": 2}},
    "vol_width_08": {"config": {"volatility_width_weight": 0.8}},
    "arb_margin_12": {"params": {"arb_margin": 12}},
}


def _side(side: object) -> str:
    return str(getattr(side, "value", side)).upper()


class Telemetry:
    """Observe the same SDK callbacks/actions a live strategy uses; never alter replay rules."""

    def on_start(self):
        self.fill_events = []
        self.conversion_events = []
        self.reject_events = []
        self.messages = {k: 0 for k in ("new", "cancel", "replace", "cancel_all", "convert")}
        self.max_abs_position = {s: 0 for s in self.symbols}
        self.max_abs_worst_position = {s: 0 for s in self.symbols}
        self.max_abs_exposure_milli = 0
        self.max_abs_worst_exposure_milli = 0
        self.max_open_orders = 0
        self.max_open_per_symbol = {s: 0 for s in self.symbols}
        self.messages_by_batch = {}
        super().on_start()

    def on_batch(self, state):
        super().on_batch(state)
        for symbol in self.symbols:
            self.max_abs_position[symbol] = max(self.max_abs_position[symbol],
                                                abs(state.position(symbol)))
            usage = state.limit_usage.symbols.get(symbol)
            if usage:
                self.max_abs_worst_position[symbol] = max(
                    self.max_abs_worst_position[symbol], abs(usage.long_worst),
                    abs(usage.short_worst))
        self.max_abs_exposure_milli = max(self.max_abs_exposure_milli, abs(state.exposure))
        self.max_abs_worst_exposure_milli = max(
            self.max_abs_worst_exposure_milli, abs(state.limit_usage.exposure_low),
            abs(state.limit_usage.exposure_high))
        self.max_open_orders = max(self.max_open_orders, len(state.open_orders))
        for symbol in self.symbols:
            self.max_open_per_symbol[symbol] = max(
                self.max_open_per_symbol[symbol], len(state.orders(symbol)))

    def _count(self, kind):
        self.messages[kind] += 1
        batch = self.state.batch if self.state is not None else -1
        self.messages_by_batch[batch] = self.messages_by_batch.get(batch, 0) + 1

    def on_fill(self, fill):
        self.fill_events.append({
            "batch": fill.batch, "order_id": fill.order_id, "symbol": fill.symbol,
            "side": _side(fill.side), "price": fill.price, "qty": fill.qty,
            "liquidity": _side(fill.liquidity), "fee_mills": fill.fee_mills,
            "penalty_mills": fill.penalty_mills, "self_trade": fill.self_trade,
        })
        super().on_fill(fill)

    def on_converted(self, converted):
        self.conversion_events.append({"batch": converted.batch,
                                       "kind": _side(converted.kind),
                                       "qty": converted.qty,
                                       "fee_mills": converted.fee_mills})
        super().on_converted(converted)

    def on_reject(self, reject):
        self.reject_events.append({"batch": reject.batch, "code": _side(reject.code),
                                   "detail": reject.detail})
        super().on_reject(reject)

    def buy(self, *args, **kwargs):
        self._count("new")
        return super().buy(*args, **kwargs)

    def sell(self, *args, **kwargs):
        self._count("new")
        return super().sell(*args, **kwargs)

    def cancel(self, *args, **kwargs):
        self._count("cancel")
        return super().cancel(*args, **kwargs)

    def replace(self, *args, **kwargs):
        self._count("replace")
        return super().replace(*args, **kwargs)

    def cancel_all(self, *args, **kwargs):
        self._count("cancel_all")
        return super().cancel_all(*args, **kwargs)

    def create(self, *args, **kwargs):
        self._count("convert")
        return super().create(*args, **kwargs)

    def redeem(self, *args, **kwargs):
        self._count("convert")
        return super().redeem(*args, **kwargs)


class MeasuredOfficial(Telemetry, MarketMaker):
    pass


class MeasuredOur(Telemetry, OurStrategy):
    pass


def strategy_for(variant: str, overrides: dict):
    if variant == "official":
        return MeasuredOfficial()
    modes = {"microprice": "microprice", "cross_asset": "cross_asset",
             "predictive": "selected", "predictive_basket": "selected",
             "combined": "selected"}
    if variant not in modes:
        raise ValueError(f"unknown variant {variant}")
    cls = type(f"Measured_{variant}", (MeasuredOur,), {})
    cls.FAIR_MODE = modes[variant]
    cls.ETF_ENABLED = variant == "combined"
    cls.CONFIG_OVERRIDES = dict(overrides.get("config", {}))
    if variant not in ("predictive_basket", "combined"):
        cls.CONFIG_OVERRIDES["basket_fair_weight"] = 0.0
    defaults = overrides.get("params", {})
    cls.MODEL_PARAMS = tuple(replace(p, default=defaults.get(p.name, p.default))
                             for p in OurStrategy.MODEL_PARAMS)
    return cls()


def _mids(session: bt.Session) -> dict[str, list[float | None]]:
    result = {s.symbol: [] for s in session.symbols}
    last = {s.symbol: None for s in session.symbols}
    for tops in session.tops:
        for symbol, t in tops.items():
            if t.bid_px is not None and t.ask_px is not None:
                last[symbol] = (t.bid_px + t.ask_px) / 2
            result[symbol].append(last[symbol])
    return result


def _stress_keep(variant: str, session: str, fill: dict) -> bool:
    """Deterministic 75% maker-fill retention, independent of future outcomes."""
    key = f"{variant}|{session}|{fill['order_id']}|{fill['batch']}|{fill['symbol']}"
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:8], "big") % 4 != 0


def summarize(session: bt.Session, variant: str, strategy: Telemetry,
              result: bt.BacktestResult) -> dict:
    mids = _mids(session)
    marks = bt.Marks()
    for tops, trades in zip(session.tops, session.trades):
        marks.end_of_batch(tops, trades)
    symbols = tuple(s.symbol for s in session.symbols)
    by_symbol = {s: {"fills": 0, "shares": 0, "maker_fills": 0, "taker_fills": 0,
                     "maker_shares": 0, "taker_shares": 0, "fees_mills": 0,
                     "gross_markout_60_cent_shares": 0.0,
                     "net_markout_60_mills": 0.0, "spread_capture_cent_shares": 0.0,
                     "spread_capture_shares": 0, "stress_markout_mills": 0.0,
                     "stress_shares": 0, "cash_mills": 0.0} for s in symbols}
    for f in strategy.fill_events:
        s, qty, price, batch = f["symbol"], f["qty"], f["price"], f["batch"]
        sign = 1 if f["side"] == "BUY" else -1
        item = by_symbol[s]
        item["fills"] += 1
        item["shares"] += qty
        maker = f["liquidity"] == "MAKER"
        item["maker_fills" if maker else "taker_fills"] += 1
        item["maker_shares" if maker else "taker_shares"] += qty
        fee = f["fee_mills"] + f["penalty_mills"]
        item["fees_mills"] += fee
        item["cash_mills"] -= sign * price * qty * 10 + fee
        current_mid = mids[s][batch]
        if maker and current_mid is not None:
            item["spread_capture_cent_shares"] += sign * (current_mid - price) * qty
            item["spread_capture_shares"] += qty
        future = mids[s][min(session.n_batches - 1, batch + 120)]
        if future is not None:
            gross = sign * (future - price) * qty
            item["gross_markout_60_cent_shares"] += gross
            item["net_markout_60_mills"] += gross * 10 - fee
            if not maker or _stress_keep(variant, session.name, f):
                # One-cent adverse price movement per retained share, plus lost maker fills.
                item["stress_markout_mills"] += gross * 10 - fee - 10 * qty
                item["stress_shares"] += qty
    # Conversion transfers are internal. Allocate their value at observable basket mids so the
    # per-symbol cash accounts add back to the SDK total, with fee charged to HPPO.
    for c in strategy.conversion_events:
        b = c["batch"]
        n = c["qty"] * (1 if c["kind"] == "CREATE" else -1)
        bnky = mids["BNKY"][b] or 0
        brry = mids["BRRY"][b] or 0
        by_symbol["HPPO"]["cash_mills"] -= n * (bnky + 2 * brry) * 10 + c["fee_mills"]
        by_symbol["HPPO"]["fees_mills"] += c["fee_mills"]
        by_symbol["HPPO"]["net_markout_60_mills"] -= c["fee_mills"]
        by_symbol["HPPO"]["stress_markout_mills"] -= c["fee_mills"]
        by_symbol["BNKY"]["cash_mills"] += n * bnky * 10
        by_symbol["BRRY"]["cash_mills"] += n * 2 * brry * 10
    for s in symbols:
        item = by_symbol[s]
        item["estimated_pnl_usd"] = round(
            (item["cash_mills"] + result.positions.get(s, 0) * marks.mark(s)) / 1000, 4)
        item["net_markout_60_usd"] = round(item["net_markout_60_mills"] / 1000, 4)
        item["stress_markout_usd"] = round(item["stress_markout_mills"] / 1000, 4)
        item["avg_spread_capture_cents"] = (
            round(item["spread_capture_cent_shares"] / item["spread_capture_shares"], 4)
            if item["spread_capture_shares"] else None)
    messages = sum(strategy.messages.values())
    shares = sum(v["shares"] for v in by_symbol.values())
    maker = sum(v["maker_fills"] for v in by_symbol.values())
    taker = sum(v["taker_fills"] for v in by_symbol.values())
    spread_n = sum(v["spread_capture_cent_shares"] for v in by_symbol.values())
    spread_d = sum(v["spread_capture_shares"] for v in by_symbol.values())
    attribution = sum(v["estimated_pnl_usd"] for v in by_symbol.values())
    if abs(attribution - result.pnl_est_mills / 1000) > 0.001:
        raise ValueError(f"per-symbol PnL does not reconcile: {attribution} vs {result.pnl_est_mills / 1000}")
    if maker + taker != result.fills or shares != result.shares_traded:
        raise ValueError("fill telemetry does not reconcile to SDK totals")
    return {
        "variant": variant, "session": session.name,
        "split": "train" if session.name in TRAIN else "validation" if session.name in VALIDATION else "holdout",
        "batches": result.batches, "estimated_pnl_usd": result.pnl_est_mills / 1000,
        "realized_pnl_usd": result.realized_pnl_mills / 1000,
        "net_fees_usd": result.fees_mills / 1000,
        "fills": result.fills, "shares": result.shares_traded,
        "maker_fills": maker, "taker_fills": taker,
        "maker_shares": sum(v["maker_shares"] for v in by_symbol.values()),
        "taker_shares": sum(v["taker_shares"] for v in by_symbol.values()),
        "avg_spread_capture_cents": round(spread_n / spread_d, 4) if spread_d else None,
        "net_markout_60_usd": round(sum(v["net_markout_60_mills"] for v in by_symbol.values()) / 1000, 4),
        "stress_markout_usd": round(sum(v["stress_markout_mills"] for v in by_symbol.values()) / 1000, 4),
        "stress_shares": sum(v["stress_shares"] for v in by_symbol.values()),
        "max_abs_position": strategy.max_abs_position,
        "max_abs_worst_position": strategy.max_abs_worst_position,
        "max_abs_exposure": strategy.max_abs_exposure_milli / 1000,
        "max_abs_worst_exposure": strategy.max_abs_worst_exposure_milli / 1000,
        "max_open_orders": strategy.max_open_orders,
        "max_open_per_symbol": strategy.max_open_per_symbol,
        "max_messages_batch": max(strategy.messages_by_batch.values(), default=0),
        "rejects": dict(result.rejects), "callback_rejects": strategy.reject_events,
        "reject_count": max(sum(result.rejects.values()), len(strategy.reject_events)),
        "messages": messages, "message_types": strategy.messages,
        "order_to_trade_ratio": round(messages / result.fills, 4) if result.fills else None,
        "conversions": len(strategy.conversion_events),
        "pnl_attribution_usd": round(attribution, 4),
        "by_symbol": by_symbol,
    }


def _run_one(job):
    variant, session_name, overrides, phase = job
    logging.getLogger("pqt.strategy").setLevel(logging.ERROR)
    logging.getLogger("pqt.client").setLevel(logging.ERROR)
    session = bt.load_session(ROOT / "data", session_name)
    strategy = strategy_for(variant, overrides)
    result = backtest_strategy(session, strategy)
    record = summarize(session, variant, strategy, result)
    record["phase"] = phase
    return record


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("tune", "trainval", "holdout"), required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--smoke-session", choices=TRAIN, help="single training session, no output merge")
    args = parser.parse_args()
    if args.phase == "holdout":
        if args.smoke_session:
            parser.error("--smoke-session is only for tune or trainval")
        frozen = ROOT / "results" / "strategy_selected.json"
        if not frozen.exists():
            raise SystemExit("freeze results/strategy_selected.json before opening holdout")
        selection = json.loads(frozen.read_text(encoding="utf-8"))
        if not selection.get("frozen"):
            raise SystemExit("selection is not marked frozen")
        variants = tuple(dict.fromkeys((selection["selected_variant"], "official")))
        sessions = HOLDOUT
        overrides = selection["overrides"]
        jobs = [(v, s, overrides, "holdout") for v in variants for s in sessions]
    elif args.phase == "tune":
        sessions = (args.smoke_session,) if args.smoke_session else TUNE_SESSIONS
        jobs = [("combined", s, cfg, name) for name, cfg in TUNE_CASES.items()
                for s in sessions]
    else:
        selection_path = ROOT / "results" / "tuning_choice.json"
        if not selection_path.exists():
            raise SystemExit("select tuning configuration before trainval replay")
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        overrides = selection["overrides"]
        sessions = (args.smoke_session,) if args.smoke_session else TRAIN + VALIDATION
        jobs = [(v, s, overrides, "trainval") for v in VARIANTS for s in sessions]
    outdir = ROOT / "results" / "backtests"
    if args.smoke_session:
        outdir = outdir / "smoke"
    rawdir = outdir / "raw" / args.phase
    rawdir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "phase": args.phase, "sessions": list(sessions),
        "variants": sorted({job[0] for job in jobs}),
        "pqt_client_version": version("pqt-client"),
        "pqt_protocol_version": version("pqt-protocol"),
        "python_version": sys.version.split()[0],
        "repository_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "sdk_rules": asdict(bt.Rules()),
        "command": " ".join(sys.argv),
        "selection_overrides": overrides if args.phase != "tune" else TUNE_CASES,
        "selection_sha256": (
            hashlib.sha256((ROOT / "results" / "strategy_selected.json").read_bytes()).hexdigest()
            if args.phase == "holdout" else None),
    }
    (outdir / f"{args.phase}_metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    completed = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_run_one, job): job for job in jobs}
        for future in as_completed(futures):
            variant, session_name, _, case = futures[future]
            record = future.result()
            file = rawdir / f"{case}_{variant}_{session_name}.json"
            file.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            completed.append(record)
            print(f"{args.phase} {case} {variant} {session_name}: "
                  f"PnL=${record['estimated_pnl_usd']:.2f} "
                  f"markout=${record['net_markout_60_usd']:.2f} "
                  f"fills={record['fills']} rejects={record['reject_count']}", flush=True)
    completed.sort(key=lambda r: (r["phase"], r["variant"], r["session"]))
    if args.phase == "tune":
        fields = ["phase", "session", "split", "estimated_pnl_usd", "net_markout_60_usd",
                  "stress_markout_usd", "fills", "shares", "reject_count",
                  "messages", "order_to_trade_ratio", "max_abs_worst_exposure"]
        _write_csv(outdir / "tuning.csv", [{k: r[k] for k in fields} for r in completed], fields)
    else:
        fields = ["phase", "variant", "session", "split", "estimated_pnl_usd", "realized_pnl_usd",
                  "net_fees_usd", "fills", "shares", "maker_fills", "taker_fills",
                  "maker_shares", "taker_shares", "avg_spread_capture_cents",
                  "net_markout_60_usd", "stress_markout_usd", "stress_shares",
                  "max_abs_exposure", "max_abs_worst_exposure", "max_open_orders",
                  "max_messages_batch",
                  "reject_count", "messages", "order_to_trade_ratio", "conversions"]
        fields += [f"max_pos_{s}" for s in ("HPPO", "BNKY", "CRRT", "BRRY")]
        fields += [f"max_worst_pos_{s}" for s in ("HPPO", "BNKY", "CRRT", "BRRY")]
        fields += [f"max_open_{s}" for s in ("HPPO", "BNKY", "CRRT", "BRRY")]
        rows = []
        for r in completed:
            row = {k: r[k] for k in fields if k in r}
            for s in ("HPPO", "BNKY", "CRRT", "BRRY"):
                row[f"max_pos_{s}"] = r["max_abs_position"][s]
                row[f"max_worst_pos_{s}"] = r["max_abs_worst_position"][s]
                row[f"max_open_{s}"] = r["max_open_per_symbol"][s]
            rows.append(row)
        _write_csv(outdir / f"{args.phase}.csv", rows, fields)
        symbol_fields = ["phase", "variant", "session", "split", "symbol", "estimated_pnl_usd",
                         "fills", "shares", "maker_fills", "taker_fills", "maker_shares",
                         "taker_shares", "fees_mills", "avg_spread_capture_cents",
                         "net_markout_60_usd", "stress_markout_usd"]
        symbol_rows = [{**{k: r[k] for k in ("phase", "variant", "session", "split")},
                        "symbol": s,
                        **{k: values[k] for k in symbol_fields[5:]}}
                       for r in completed for s, values in r["by_symbol"].items()]
        _write_csv(outdir / f"{args.phase}_symbol.csv", symbol_rows, symbol_fields)
        if args.phase == "trainval" and not args.smoke_session:
            (ROOT / "results" / "backtest_comparison.csv").write_text(
                (outdir / "trainval.csv").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Saved {len(completed)} replays in {outdir}")


if __name__ == "__main__":
    main()
