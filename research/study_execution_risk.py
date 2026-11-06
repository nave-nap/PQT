"""Diagnostic quote overlay and fill stress on s01-s20; SDK replay is unchanged.

Run: .venv/bin/python -m research.study_execution_risk
The frozen class is read from ab85993; the candidate is the current branch class.
Overlay and fill-thinning calculations are synthetic diagnostics, not SDK outcomes.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import subprocess
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from pqt_client import backtest_v2 as bt
from pqt_client.v2 import backtest_strategy

from research.run_backtests import Telemetry, _mids, summarize
from strategies.competition_strategy import CompetitionStrategy as CandidateStrategy
from strategies.our_components import SYMBOLS, side_name

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "execution_risk"
SESSIONS = tuple(f"s{i:02d}" for i in range(1, 21))
RATES = (100, 75, 50, 25)
ADVERSE_CENTS = (0, 1, 2, 3)


def frozen_strategy_class():
    source = subprocess.check_output(
        ["git", "show", "ab85993:strategies/competition_strategy.py"],
        cwd=ROOT, text=True)
    namespace = {"__name__": "pqt_frozen_execution_study",
                 "__file__": str(ROOT / "strategies" / "competition_strategy.py")}
    exec(compile(source, namespace["__file__"], "exec"), namespace)
    return namespace["CompetitionStrategy"]


class QuoteAudit:
    """Observe SDK decisions and end-of-batch orders without altering either."""

    def on_start(self):
        self.quote_counts = {s: {"sent_bid": 0, "sent_ask": 0,
                                 "sent_inside_bid": 0, "sent_inside_ask": 0}
                             for s in SYMBOLS}
        self.overlay = {s: {"observed_batches": 0, "two_sided_batches": 0,
                            "active_batches": 0, "active_bid_batches": 0,
                            "active_ask_batches": 0, "inside_bid_batches": 0,
                            "inside_ask_batches": 0, "join_bid_batches": 0,
                            "join_ask_batches": 0, "locked_crossed_batches": 0,
                            "microprice_samples": 0, "microprice_delta_sum": 0.0,
                            "microprice_abs_sum": 0.0, "microprice_abs_values": []}
                        for s in SYMBOLS}
        super().on_start()

    def _record_send(self, symbol, side, price):
        counts = self.quote_counts[symbol]
        label = "bid" if side == "BUY" else "ask"
        counts[f"sent_{label}"] += 1
        top = self.state.top[symbol]
        if top.two_sided and (top.bid_px < price < top.ask_px):
            counts[f"sent_inside_{label}"] += 1

    def buy(self, symbol, qty, price, **kwargs):
        if not kwargs.get("ioc", False):
            self._record_send(symbol, "BUY", price)
        return super().buy(symbol, qty, price, **kwargs)

    def sell(self, symbol, qty, price, **kwargs):
        if not kwargs.get("ioc", False):
            self._record_send(symbol, "SELL", price)
        return super().sell(symbol, qty, price, **kwargs)

    def on_batch(self, state):
        for symbol in SYMBOLS:
            item = self.overlay[symbol]
            item["observed_batches"] += 1
            top = state.top[symbol]
            if not top.two_sided or top.halted:
                continue
            item["two_sided_batches"] += 1
            orders = state.orders(symbol)
            bids = [o for o in orders if side_name(o.side) == "BUY"]
            asks = [o for o in orders if side_name(o.side) == "SELL"]
            if not orders:
                continue
            item["active_batches"] += 1
            item["active_bid_batches"] += bool(bids)
            item["active_ask_batches"] += bool(asks)
            best_bid = max((o.price for o in bids), default=None)
            best_ask = min((o.price for o in asks), default=None)
            if ((best_bid is not None and best_bid >= top.ask_px) or
                    (best_ask is not None and best_ask <= top.bid_px) or
                    (best_bid is not None and best_ask is not None and best_bid >= best_ask)):
                item["locked_crossed_batches"] += 1
                continue  # A live crossing quote might execute; no stable overlaid book.
            if best_bid is not None:
                item["inside_bid_batches"] += best_bid > top.bid_px
                item["join_bid_batches"] += best_bid == top.bid_px
            if best_ask is not None:
                item["inside_ask_batches"] += best_ask < top.ask_px
                item["join_ask_batches"] += best_ask == top.ask_px
            delta = overlay_microprice(top, bids, asks)
            item["microprice_samples"] += 1
            item["microprice_delta_sum"] += delta
            item["microprice_abs_sum"] += abs(delta)
            item["microprice_abs_values"].append(abs(delta))
        super().on_batch(state)


def overlay_microprice(top, bids, asks):
    """Counterfactual display-only microprice after adding our resting top quantity."""
    bid = max([top.bid_px, *(o.price for o in bids)])
    ask = min([top.ask_px, *(o.price for o in asks)])
    if bid >= ask:
        raise ValueError("locked/crossed quote has no stable overlay")
    bid_qty = (top.bid_qty if bid == top.bid_px else 0)
    bid_qty += sum(o.remaining for o in bids if o.price == bid)
    ask_qty = (top.ask_qty if ask == top.ask_px else 0)
    ask_qty += sum(o.remaining for o in asks if o.price == ask)
    augmented = (bid * ask_qty + ask * bid_qty) / (bid_qty + ask_qty)
    return augmented - top.microprice


def _retained(fill: dict, session: str, rate: int) -> bool:
    if rate == 100:
        return True
    key = f"{session}|{fill['order_id']}|{fill['batch']}|{fill['symbol']}"
    fraction = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") / 2**64
    return fraction < rate / 100


def stress_rows(session, fills, mids, mode):
    """Ex-post thinning plus adverse cents; positions and future paths are not rerun."""
    rows = []
    for symbol in SYMBOLS:
        symbol_fills = [f for f in fills if f["symbol"] == symbol]
        for rate in RATES:
            retained = [f for f in symbol_fills if f["liquidity"] != "MAKER"
                        or _retained(f, session.name, rate)]
            base_mills = 0.0
            maker_shares = maker_fills = 0
            for f in retained:
                future = mids[symbol][min(session.n_batches - 1, f["batch"] + 120)]
                if future is None:
                    continue
                sign = 1 if f["side"] == "BUY" else -1
                base_mills += sign * (future - f["price"]) * f["qty"] * 10
                base_mills -= f["fee_mills"] + f["penalty_mills"]
                if f["liquidity"] == "MAKER":
                    maker_fills += 1
                    maker_shares += f["qty"]
            break_even = base_mills / (10 * maker_shares) if maker_shares else None
            first_loss = next((c for c in ADVERSE_CENTS
                               if base_mills - 10 * c * maker_shares <= 0), ">3")
            for adverse in ADVERSE_CENTS:
                rows.append({"mode": mode, "session": session.name,
                             "split": "training" if int(session.name[1:]) <= 16 else "validation",
                             "symbol": symbol, "maker_fill_rate_pct": rate,
                             "adverse_cents": adverse, "retained_maker_fills": maker_fills,
                             "retained_maker_shares": maker_shares,
                             "net_markout_usd": round((base_mills - 10 * adverse * maker_shares) / 1000, 4),
                             "break_even_adverse_cents": (round(break_even, 4)
                                                          if break_even is not None else None),
                             "first_nonpositive_tested_cents": first_loss})
    return rows


def replay(mode, name):
    logging.getLogger("pqt.strategy").setLevel(logging.ERROR)
    logging.getLogger("pqt.client").setLevel(logging.ERROR)
    base = frozen_strategy_class() if mode == "frozen" else CandidateStrategy
    measured = type(f"Measured_{mode}", (Telemetry, QuoteAudit, base), {})
    session = bt.load_session(ROOT / "data", name)
    strategy = measured()
    result = backtest_strategy(session, strategy)
    if getattr(strategy, "failure_latched", False):
        raise RuntimeError(f"failure latch on {mode} {name}")
    summary = summarize(session, "microprice", strategy, result)
    mids = _mids(session)
    stress = stress_rows(session, strategy.fill_events, mids, mode)
    for symbol in SYMBOLS:
        baseline = next(r for r in stress if r["symbol"] == symbol
                        and r["maker_fill_rate_pct"] == 100 and r["adverse_cents"] == 0)
        if abs(baseline["net_markout_usd"] - summary["by_symbol"][symbol]["net_markout_60_usd"]) > .00011:
            raise AssertionError(f"markout mismatch on {mode} {name} {symbol}")
    per_symbol = []
    for symbol in SYMBOLS:
        overlay = strategy.overlay[symbol]
        samples = sorted(overlay.pop("microprice_abs_values"))
        n = len(samples)
        overlay["microprice_mean_delta_cents"] = (round(overlay.pop("microprice_delta_sum") / n, 4)
                                                    if n else None)
        overlay["microprice_mean_abs_delta_cents"] = (round(overlay.pop("microprice_abs_sum") / n, 4)
                                                        if n else None)
        overlay["microprice_p95_abs_delta_cents"] = samples[max(0, (95 * n + 99) // 100 - 1)] if n else None
        overlay["microprice_max_abs_delta_cents"] = samples[-1] if n else None
        per_symbol.append({"mode": mode, "session": name,
                           "split": "training" if int(name[1:]) <= 16 else "validation",
                           "symbol": symbol,
                           "sdk_estimated_pnl_usd": summary["by_symbol"][symbol]["estimated_pnl_usd"],
                           "baseline_net_markout_usd": summary["by_symbol"][symbol]["net_markout_60_usd"],
                           "maker_fills": summary["by_symbol"][symbol]["maker_fills"],
                           "maker_shares": summary["by_symbol"][symbol]["maker_shares"],
                           **strategy.quote_counts[symbol], **overlay})
    return per_symbol, stress


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def aggregate(exposures, stresses):
    quote_groups = defaultdict(list)
    for row in exposures:
        quote_groups[(row["mode"], row["split"], row["symbol"])].append(row)
    quote_rows = []
    count_fields = ("observed_batches", "two_sided_batches", "active_batches",
                    "active_bid_batches", "active_ask_batches", "inside_bid_batches",
                    "inside_ask_batches", "join_bid_batches", "join_ask_batches",
                    "locked_crossed_batches", "microprice_samples", "sent_bid",
                    "sent_ask", "sent_inside_bid", "sent_inside_ask", "maker_fills",
                    "maker_shares")
    for (mode, split, symbol), group in sorted(quote_groups.items()):
        row = {"mode": mode, "split": split, "symbol": symbol,
               "sessions": len(group)}
        row.update({field: sum(int(x[field]) for x in group) for field in count_fields})
        sample_n = row["microprice_samples"]
        row["microprice_mean_abs_delta_cents"] = round(
            sum(float(x["microprice_mean_abs_delta_cents"]) * int(x["microprice_samples"])
                for x in group) / sample_n, 4) if sample_n else None
        row["inside_side_rate_pct"] = round(
            100 * (row["inside_bid_batches"] + row["inside_ask_batches"]) /
            (row["active_bid_batches"] + row["active_ask_batches"]), 4)
        row["inside_sent_rate_pct"] = round(
            100 * (row["sent_inside_bid"] + row["sent_inside_ask"]) /
            (row["sent_bid"] + row["sent_ask"]), 4)
        quote_rows.append(row)
    stress_groups = defaultdict(list)
    for row in stresses:
        stress_groups[(row["mode"], row["split"], row["symbol"],
                       int(row["maker_fill_rate_pct"]), int(row["adverse_cents"]))].append(row)
    stress_rows_out = []
    for (mode, split, symbol, rate, adverse), group in sorted(stress_groups.items()):
        net = round(sum(float(x["net_markout_usd"]) for x in group), 4)
        shares = sum(int(x["retained_maker_shares"]) for x in group)
        base = sum(float(x["net_markout_usd"]) for x in stress_groups[
            (mode, split, symbol, rate, 0)])
        break_even = round(base * 100 / shares, 4) if shares else None
        stress_rows_out.append({"mode": mode, "split": split, "symbol": symbol,
                                "sessions": len(group), "maker_fill_rate_pct": rate,
                                "adverse_cents": adverse,
                                "retained_maker_fills": sum(int(x["retained_maker_fills"])
                                                            for x in group),
                                "retained_maker_shares": shares,
                                "net_markout_usd": net,
                                "break_even_adverse_cents": break_even,
                                "edge_nonpositive": net <= 0})
    return quote_rows, stress_rows_out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    dependencies = ("strategies/final_strategy.py", "strategies/our_strategy.py",
                    "strategies/our_components.py", "results/competition_presets.json",
                    "results/strategy_selected.json", "results/strategy_config.json")
    subprocess.run(["git", "diff", "--exit-code", "ab85993", "--", *dependencies],
                   cwd=ROOT, check=True, capture_output=True)
    subprocess.run(["git", "diff", "--exit-code", "e3d5dd2", "--",
                    "strategies/competition_strategy.py"],
                   cwd=ROOT, check=True, capture_output=True)
    exposures, stresses = [], []
    jobs = [(mode, session) for mode in ("frozen", "candidate") for session in SESSIONS]
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(replay, *job): job for job in jobs}
        for future in as_completed(futures):
            per_symbol, stress = future.result()
            exposures.extend(per_symbol)
            stresses.extend(stress)
            mode, name = futures[future]
            print(f"{mode} {name}: {len(per_symbol)} symbols, {len(stress)} stress cases", flush=True)
    exposures.sort(key=lambda r: (r["mode"], r["session"], r["symbol"]))
    stresses.sort(key=lambda r: (r["mode"], r["session"], r["symbol"],
                                 -r["maker_fill_rate_pct"], r["adverse_cents"]))
    write_csv(OUT / "session_symbol_quote_overlay.csv", exposures)
    write_csv(OUT / "session_symbol_stress.csv", stresses)
    quote_summary, stress_summary = aggregate(exposures, stresses)
    write_csv(OUT / "split_symbol_quote_overlay.csv", quote_summary)
    write_csv(OUT / "split_symbol_stress.csv", stress_summary)
    metadata = {"sdk_rules": "unchanged pqt-client 0.2.3 default backtest_strategy",
                "frozen_commit": "ab85993", "candidate_commit": "e3d5dd2",
                "sessions": list(SESSIONS), "rates_pct": RATES,
                "adverse_cents": ADVERSE_CENTS,
                "diagnostic": "own orders overlaid on public top; ex-post deterministic maker-fill thinning"}
    (OUT / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(exposures)} symbol-session overlays and {len(stresses)} stress rows")


if __name__ == "__main__":
    main()
