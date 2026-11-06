"""Stage 6 operational wrapper for the frozen microprice strategy.

Select a tested preset with PQT_PRESET=conservative|balanced|aggressive (default balanced).
The trading logic and SDK parameter ranges remain those of final_strategy.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategies.final_strategy import FinalStrategy  # noqa: E402

PRESET_PATH = ROOT / "results" / "competition_presets.json"
PRESETS = json.loads(PRESET_PATH.read_text(encoding="utf-8"))
PRESET_NAME = os.environ.get("PQT_PRESET", PRESETS["default"]).lower()
if PRESET_NAME not in PRESETS["presets"]:
    raise ValueError(f"unknown PQT_PRESET {PRESET_NAME!r}; choose conservative, balanced, aggressive")
PRESET = PRESETS["presets"][PRESET_NAME]
FREEZE_PATH = ROOT / "results" / "competition_freeze.json"
if FREEZE_PATH.exists():
    freeze = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    if freeze.get("preset_sha256") != hashlib.sha256(PRESET_PATH.read_bytes()).hexdigest():
        raise RuntimeError("competition presets differ from the frozen verified file")


class CompetitionStrategy(FinalStrategy):
    CONFIG_OVERRIDES = {**FinalStrategy.CONFIG_OVERRIDES, **PRESET["config"]}
    MODEL_PARAMS = tuple(
        replace(spec, default=PRESET["params"].get(spec.name, spec.default))
        for spec in FinalStrategy.MODEL_PARAMS
    )

    def on_start(self) -> None:
        super().on_start()
        self.failure_latched = False
        self.last_cleanup_batch = -10
        self.fill_count_since_report = 0
        self.fill_qty_since_report = 0
        self.fee_mills_since_report = 0
        existing = self.open_orders()
        if existing:
            self.reconnect_pending = True
            self.log.warning("startup found %d resting orders; cancel before quoting", len(existing))
        self.log.info("competition preset=%s; purpose=%s", PRESET_NAME, PRESET["purpose"])

    def _cleanup_after_failure(self, state) -> None:
        if (state.open_orders and state.batch >= self.last_cleanup_batch + 2
                and self.messages_left() > 0):
            try:
                self.cancel_all()
                self.last_cleanup_batch = state.batch
                self.log.warning("defensive cancel_all: %d resting orders", len(state.open_orders))
            except Exception:
                self.log.exception("defensive cancel_all failed; inspect orders in trader GUI")

    def on_batch(self, state) -> None:
        if self.failure_latched:
            self._cleanup_after_failure(state)
            return
        try:
            super().on_batch(state)
        except Exception:
            self.failure_latched = True
            self.log.exception("strategy exception at batch %d: trading latched off", state.batch)
            self._cleanup_after_failure(state)
            if self.mode == "backtest":
                raise  # Local validation must fail loudly.
            return
        if state.batch % 120 == 119:
            orders = {s: [f"{o.side.value}:{o.price}x{o.remaining}" for o in state.orders(s)]
                      for s in self.symbols}
            fair = {s: round(top.microprice, 2) if top.microprice is not None else None
                    for s, top in state.top.items()}
            cooling = [s for s in self.symbols if state.batch <= self.cooldown[s]]
            self.log.info(
                "monitor batch=%d preset=%s pos=%s exposure=%.3f worst=[%.3f,%.3f] "
                "orders=%s msgs_left=%d fair_micro=%s fills_1m=%d shares_1m=%d "
                "fees_1m=$%.2f rejects=%d pnl_est=$%.2f cooling=%s failure=%s",
                state.batch, PRESET_NAME, dict(state.portfolio.positions), state.exposure / 1000,
                state.limit_usage.exposure_low / 1000, state.limit_usage.exposure_high / 1000,
                orders, self.messages_left(), fair, self.fill_count_since_report,
                self.fill_qty_since_report, self.fee_mills_since_report / 1000,
                self.stats["rejects"], state.pnl_est_mills / 1000, cooling, self.failure_latched,
            )
            self.fill_count_since_report = 0
            self.fill_qty_since_report = 0
            self.fee_mills_since_report = 0

    def on_fill(self, fill) -> None:
        super().on_fill(fill)
        self.fill_count_since_report += 1
        self.fill_qty_since_report += fill.qty
        self.fee_mills_since_report += fill.fee_mills + fill.penalty_mills
        self.log.debug("fill batch=%d %s %s %dx%d liquidity=%s remaining=%d",
                       fill.batch, fill.symbol, fill.side, fill.qty, fill.price,
                       fill.liquidity, fill.remaining)

    def on_ack(self, ack) -> None:
        super().on_ack(ack)
        self.log.debug("ack order=%d %s %s %dx%d", ack.order_id, ack.symbol, ack.side,
                       ack.qty, ack.price)

    def on_cancelled(self, cancelled) -> None:
        super().on_cancelled(cancelled)
        self.log.debug("cancelled order=%d %s reason=%s", cancelled.order_id,
                       cancelled.symbol, cancelled.reason)

    def on_replaced(self, replaced) -> None:
        super().on_replaced(replaced)
        self.log.debug("replaced order=%d %s %dx%d priority=%s", replaced.order_id,
                       replaced.symbol, replaced.qty, replaced.price,
                       replaced.kept_priority)

    def on_news(self, item) -> None:
        super().on_news(item)
        self.log.warning("defensive news mode: id=%s headline=%s", item.news_id, item.headline)

    def on_reconnect(self) -> None:
        super().on_reconnect()
        self.log.warning("reconnect: cancelling persistent orders, warming observations again")
