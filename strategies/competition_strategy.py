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
from pqt_protocol.v2 import CancelReason, Phase

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
    # A restart or halt invalidates recent book observations. The normal 241-batch
    # warmup remains in force; this extra quiet period protects even a warm history.
    POST_HALT_QUIET_BATCHES = 120
    CONFIG_OVERRIDES = {**FinalStrategy.CONFIG_OVERRIDES, **PRESET["config"]}
    MODEL_PARAMS = tuple(
        replace(spec, default=PRESET["params"].get(spec.name, spec.default))
        for spec in FinalStrategy.MODEL_PARAMS
    )

    def on_start(self) -> None:
        self.last_cleanup_batch = -10
        self.failure_latched = True
        try:
            super().on_start()
            self.fill_count_since_report = 0
            self.fill_qty_since_report = 0
            self.fee_mills_since_report = 0
            self.seen_news_ids = set()
            self.halted_symbols = set()
            self.quiet_until = -1
            self.last_phase = None
            existing = self.open_orders()
            if existing:
                self.reconnect_pending = True
                self.log.warning("startup found %d resting orders; cancel before quoting", len(existing))
            self.log.info("competition preset=%s; purpose=%s", PRESET_NAME, PRESET["purpose"])
            self.failure_latched = False
        except Exception:
            self._fail_closed("startup")

    def _cleanup_after_failure(self, state=None) -> None:
        try:
            try:
                orders = self.open_orders()  # Core knows the latest event, even before on_batch.
            except Exception:
                orders = state.open_orders if state is not None else ()
            batch = state.batch if state is not None else getattr(self.state, "batch", None)
            if (orders and (batch is None or batch >= self.last_cleanup_batch + 2)
                    and self.messages_left() > 0):
                self.cancel_all()
                if batch is not None:
                    self.last_cleanup_batch = batch
                self.log.warning("defensive cancel_all: %d resting orders", len(orders))
        except Exception:
            self.log.exception("defensive cancel_all failed; inspect orders in trader GUI")

    def _fail_closed(self, where: str, state=None) -> None:
        self.failure_latched = True
        self.log.exception("unexpected %s failure: trading latched off", where)
        self._cleanup_after_failure(state if state is not None else getattr(self, "state", None))
        if self.mode == "backtest":
            raise

    def _quiet(self, batch: int, reason: str) -> None:
        self.quiet_until = max(self.quiet_until, batch + self.POST_HALT_QUIET_BATCHES)
        self.history.clear()
        self.last_batch = None
        self.log.warning("%s: quoting quiet through batch %d; history reset", reason,
                         self.quiet_until)

    def _reconcile_news(self, state) -> None:
        if not state.news_stack:
            return
        cooldown_batches = int(self.model_params["news_cooldown_s"] * 1000
                               / self.session.batch_ms)
        for item in state.news_stack:
            if item.news_id not in self.seen_news_ids:
                if item.batch + cooldown_batches >= state.batch:
                    super().on_news(item)
                self.seen_news_ids.add(item.news_id)

    def on_batch(self, state) -> None:
        if self.failure_latched:
            self._cleanup_after_failure(state)
            return
        try:
            self._reconcile_news(state)
            halted = {s for s in self.symbols if state.top[s].halted}
            resumed = self.halted_symbols - halted
            if resumed:
                self._quiet(state.batch, f"halt ended on {sorted(resumed)}")
            self.halted_symbols = halted
            if self.last_phase is Phase.PAUSED and state.phase is Phase.RUNNING:
                self._quiet(state.batch, "session resumed")
            self.last_phase = state.phase
            if state.batch <= self.quiet_until:
                self._cancel_resting(state)
                return
            super().on_batch(state)
            if state.batch % 120 != 119:
                return
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
        except Exception:
            self._fail_closed("batch/monitoring", state)

    def on_fill(self, fill) -> None:
        try:
            super().on_fill(fill)
            self.fill_count_since_report += 1
            self.fill_qty_since_report += fill.qty
            self.fee_mills_since_report += fill.fee_mills + fill.penalty_mills
            self.log.debug("fill batch=%d %s %s %dx%d liquidity=%s remaining=%d",
                           fill.batch, fill.symbol, fill.side, fill.qty, fill.price,
                           fill.liquidity, fill.remaining)
        except Exception:
            self._fail_closed("fill")

    def on_ack(self, ack) -> None:
        super().on_ack(ack)
        self.log.debug("ack order=%d %s %s %dx%d", ack.order_id, ack.symbol, ack.side,
                       ack.qty, ack.price)

    def on_replaced(self, replaced) -> None:
        super().on_replaced(replaced)
        self.log.debug("replaced order=%d %s %dx%d priority=%s", replaced.order_id,
                       replaced.symbol, replaced.qty, replaced.price,
                       replaced.kept_priority)

    def on_news(self, item) -> None:
        try:
            super().on_news(item)
            self.seen_news_ids.add(item.news_id)
            self.log.warning("defensive news mode: id=%s headline=%s", item.news_id, item.headline)
        except Exception:
            self._fail_closed("news")

    def on_reconnect(self) -> None:
        try:
            super().on_reconnect()
            self.log.warning("reconnect: cancelling persistent orders, warming observations again")
        except Exception:
            self._fail_closed("reconnect")

    def on_model_params(self, changed) -> None:
        try:
            super().on_model_params(changed)
        except Exception:
            self._fail_closed("model parameters")

    def on_cancelled(self, cancelled) -> None:
        try:
            super().on_cancelled(cancelled)
            if cancelled.reason is CancelReason.SYSTEM:
                self._quiet(cancelled.batch, "exchange system cancellation")
            self.log.debug("cancelled order=%d %s reason=%s", cancelled.order_id,
                           cancelled.symbol, cancelled.reason)
        except Exception:
            self._fail_closed("cancellation")

    def on_round_state(self, round_state) -> None:
        try:
            if round_state.phase is Phase.PAUSED:
                self.last_phase = Phase.PAUSED
            elif self.last_phase is Phase.PAUSED and round_state.phase is Phase.RUNNING:
                batch = round_state.batch
                if batch is None:
                    batch = getattr(self._core, "batch", None)
                if batch is None:
                    raise RuntimeError("resume batch unavailable")
                self._quiet(batch, "session resumed")
                self.last_phase = Phase.RUNNING
            super().on_round_state(round_state)
        except Exception:
            self._fail_closed("round state")
