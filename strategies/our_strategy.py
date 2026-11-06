"""Coordinated PQT v0.2.3 strategy: causal fair values, cautious quotes and ETF conversion.

Run: pqt backtest strategies/our_strategy.py --data data --name s17
Live: use --cancel-on-disconnect with the SDK runner.
"""

from __future__ import annotations

import json
import math
import sys
from collections import deque
from pathlib import Path

from pqt_client.v2 import BatchState, ParamKind, ParamSpec, Side, Strategy

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.inference import FairValuePredictor  # noqa: E402
from strategies.our_components import (  # noqa: E402
    ETF_LEGS, SYMBOLS, OrderRisk, affected_by_news, arb_clip, basket_edges,
    can_add_order, can_convert, quote_prices, settlement_units, side_name,
)


class OurStrategy(Strategy):
    FAIR_MODE = "selected"  # Stage 5 research subclasses: microprice or cross_asset.
    ETF_ENABLED = True
    CONFIG_OVERRIDES: dict = {}

    MODEL_PARAMS = (
        ParamSpec("half_width", ParamKind.INT, 3, 1, 30),
        ParamSpec("quote_size", ParamKind.INT, 15, 1, 100),
        ParamSpec("inventory_skew", ParamKind.INT, 4, 0, 25),
        ParamSpec("prediction_threshold", ParamKind.INT, 1, 0, 20),
        ParamSpec("min_quote_edge", ParamKind.INT, 1, 0, 20),
        ParamSpec("news_cooldown_s", ParamKind.INT, 60, 0, 300),
        ParamSpec("arb_margin", ParamKind.INT, 8, 0, 50),
        ParamSpec("arb_enabled", ParamKind.INT, 1, 0, 1),
    )

    def on_start(self) -> None:
        config_path = ROOT / "results" / "strategy_config.json"
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.config.update(self.CONFIG_OVERRIDES)
        self.predictor = FairValuePredictor(ROOT / "models" / "fair_value_v1_selected.json")
        self.history = deque(maxlen=241)
        self.last_batch = None
        self.cooldown = {s: -1 for s in SYMBOLS}
        self.pending_orders: list[OrderRisk] = []
        self.pending_cancel: set[int] = set()
        self.arb_phase = "IDLE"
        self.arb_direction = None
        self.arb_baseline = None
        self.arb_quiet_until = 0
        self.reconnect_pending = False
        self.pause_until = 0
        self.stats = {key: 0 for key in ("quotes", "cancels", "arb_attempts", "conversions",
                                         "unwind_orders", "fills", "rejects", "self_trades")}
        self.log.info("OurStrategy: model %s; SDK limits %s; configuration %s",
                      self.predictor.bundle["model_id"], self.limits, config_path)

    def on_news(self, item) -> None:
        cooldown_batches = int(self.model_params["news_cooldown_s"] * 1000
                               / self.session.batch_ms)
        affected = affected_by_news(item.headline, item.body)
        for symbol in affected:
            self.cooldown[symbol] = max(self.cooldown[symbol], item.batch + cooldown_batches)
        self.log.warning("news %s: cooling %s until batch %s", item.news_id,
                         sorted(affected), max(self.cooldown[s] for s in affected))

    def on_reconnect(self) -> None:
        self.history.clear()
        self.last_batch = None
        self.reconnect_pending = True
        self.log.warning("reconnected: history reset; reconciling persistent orders")

    def on_ack(self, ack) -> None:
        pass  # The next BatchState is authoritative for remaining resting quantity.

    def on_fill(self, fill) -> None:
        self.stats["fills"] += 1
        if fill.self_trade:
            self.stats["self_trades"] += 1
            self.pause_until = self.n_batches
            self.log.error("self-trade on %s order %s: stopping new trades", fill.symbol, fill.order_id)

    def on_reject(self, reject) -> None:
        self.stats["rejects"] += 1
        self.pause_until = max(self.pause_until, (reject.batch or 0) + 5)
        self.log.warning("reject code=%s order=%s client=%s detail=%s", reject.code,
                         reject.order_id, reject.client_id, reject.detail)

    def on_cancelled(self, cancelled) -> None:
        self.pending_cancel.discard(cancelled.order_id)

    def on_replaced(self, replaced) -> None:
        pass  # This strategy keeps or cancels quotes; it does not replace them.

    def on_converted(self, converted) -> None:
        self.stats["conversions"] += 1
        self.log.info("converted %s %s units", converted.kind, converted.qty)

    def on_model_params(self, changed) -> None:
        self.log.info("model parameters updated: %s", changed)

    def on_batch(self, state: BatchState) -> None:
        self.pending_orders = []
        self.pending_cancel = set()
        if self.last_batch is not None and state.batch != self.last_batch + 1:
            self.history.clear()
            self.log.warning("batch gap %s -> %s: model warming again", self.last_batch, state.batch)
        self.last_batch = state.batch
        self.history.append({"batch": state.batch, "top": state.top, "trades": state.trades})

        if state.final:
            return  # Resting orders expire; no post-final message can be processed.
        if state.blocked or self.kill_switch.engaged:
            self._cancel_resting(state)
            return
        if self.reconnect_pending:
            self._cancel_resting(state)
            if state.open_orders:
                return
            self.reconnect_pending = False
        for symbol in SYMBOLS:
            if state.top[symbol].halted:
                self._cancel_resting(state, symbol)
            if state.batch <= self.cooldown[symbol]:
                self._cancel_resting(state, symbol)

        # Settlement has priority over new quotes. It can survive rejects and halts.
        if self.arb_phase != "IDLE":
            self._advance_arb(state)
        elif (self.ETF_ENABLED and state.batch >= self.arb_quiet_until and
              self.model_params["arb_enabled"]
              and state.batch >= self.pause_until and
              all(state.batch > self.cooldown[s] for s in ETF_LEGS)):
            self._maybe_prepare_arb(state)

        if state.batch < self.pause_until:
            self._cancel_resting(state)
            return
        if state.batches_left < self.config["stop_quoting_last_batches"]:
            self._cancel_resting(state)
            return
        if len(self.history) < 241:
            return
        for symbol in SYMBOLS:
            if self.arb_phase != "IDLE" and symbol in ETF_LEGS:
                continue
            self._quote_symbol(state, symbol)
        if state.batch % 120 == 119:
            self.log.info("minute %d positions=%s open=%d pnl_est=$%.2f stats=%s",
                          (state.batch + 1) // 120, state.portfolio.positions,
                          len(state.open_orders), state.pnl_est_mills / 1000, self.stats)

    def _live_risk_orders(self, state) -> list[OrderRisk]:
        # Pending cancels still count until the exchange acknowledges them.
        return ([OrderRisk(o.symbol, side_name(o.side), o.remaining) for o in state.open_orders]
                + self.pending_orders)

    def _can_send(self, state, symbol: str, side: str, qty: int, price: int,
                  *, market_making: bool) -> bool:
        if self.messages_left() <= self.config["message_reserve"] or price <= 0:
            return False
        info = self.symbol_info
        caps = {s: (min(info[s].position_limit, self.config["mm_position_caps"][s])
                    if market_making else info[s].position_limit) for s in SYMBOLS}
        sizes = {s: info[s].max_order_qty for s in SYMBOLS}
        weights = {s: info[s].exposure_weight_milli for s in SYMBOLS}
        candidate = OrderRisk(symbol, side, qty)
        if not can_add_order(state.portfolio.positions, self._live_risk_orders(state), candidate,
                             caps, sizes, weights, self.limits.exposure_limit,
                             min(self.limits.max_open_orders_per_symbol,
                                 self.config["max_resting_per_symbol"]) if market_making
                             else self.limits.max_open_orders_per_symbol,
                             self.config["exposure_buffer"]):
            return False
        # An IOC can also self-trade with an existing quote. Pending cancels remain live.
        for o in state.open_orders:
            if o.symbol == symbol and side_name(o.side) != side:
                if (side == "BUY" and price >= o.price) or (side == "SELL" and price <= o.price):
                    return False
        return True

    def _send(self, state, symbol: str, side: str, qty: int, price: int,
              *, ioc: bool = False, market_making: bool = False) -> bool:
        if not self._can_send(state, symbol, side, qty, price, market_making=market_making):
            return False
        if side == "BUY":
            self.buy(symbol, qty, price, ioc=ioc)
        else:
            self.sell(symbol, qty, price, ioc=ioc)
        self.pending_orders.append(OrderRisk(symbol, side, qty))
        if market_making:
            self.stats["quotes"] += 1
        return True

    def _cancel(self, order) -> None:
        if order.order_id not in self.pending_cancel and self.messages_left() > 0:
            self.cancel(order_id=order.order_id)
            self.pending_cancel.add(order.order_id)
            self.stats["cancels"] += 1

    def _cancel_resting(self, state, symbol: str | None = None) -> None:
        for order in state.open_orders:
            if symbol is None or order.symbol == symbol:
                self._cancel(order)

    def _quote_symbol(self, state, symbol: str) -> None:
        top = state.top[symbol]
        if top.halted or state.batch <= self.cooldown[symbol]:
            return
        if not top.two_sided:
            self._cancel_resting(state, symbol)
            return
        if self.FAIR_MODE == "microprice":
            prediction = {"predicted_return_cents": top.microprice - top.mid,
                          "uncertainty_cents": None}
        else:
            try:
                prediction = self.predictor.predict(
                    tuple(self.history), symbol, 60,
                    model_kind="cross_asset" if self.FAIR_MODE == "cross_asset" else "selected")
            except (ValueError, KeyError):
                self._cancel_resting(state, symbol)
                return
        params = self.model_params
        position = state.position(symbol)
        cap = self.config["mm_position_caps"][symbol]
        ret = prediction["predicted_return_cents"]
        if self.FAIR_MODE != "microprice" and abs(ret) < params["prediction_threshold"]:
            ret = 0.0
        ret = max(-self.config["max_prediction_shift_cents"],
                  min(self.config["max_prediction_shift_cents"], ret))
        centre = top.mid + (1.0 if self.FAIR_MODE == "microprice"
                            else self.config["prediction_weight"]) * ret
        if symbol == "HPPO" and all(state.top[s].two_sided for s in ETF_LEGS):
            basket_mid = state.top["BNKY"].mid + 2 * state.top["BRRY"].mid
            centre += self.config["basket_fair_weight"] * max(-10, min(10, basket_mid - top.mid))
        exposure = state.exposure / 1000
        centre -= params["inventory_skew"] * position / cap
        centre -= self.config["exposure_skew_cents"] * exposure / self.limits.exposure_limit
        recent = [r["top"][symbol].mid for r in list(self.history)[-21:]
                  if r["top"][symbol].mid is not None]
        volatility = (math.sqrt(sum((recent[i] - recent[i - 1]) ** 2
                                    for i in range(1, len(recent))) / (len(recent) - 1))
                      if len(recent) >= 2 else 0.0)
        uncertainty = prediction["uncertainty_cents"] or 0.0
        width = (max(float(params["half_width"]), top.spread / 2)
                 + min(5, volatility) * self.config["volatility_width_weight"])
        width += min(3, uncertainty * self.config["uncertainty_width_weight"])
        width += min(2, abs(ret) * self.config["adverse_width_weight"])
        bid, ask = quote_prices(centre, width, top.bid_px, top.ask_px)
        rebate_cents = self.limits.maker_rebate_mills / 10
        forecast = top.mid + ret
        min_edge = params["min_quote_edge"]
        bid_target = bid if forecast - bid + rebate_cents >= min_edge else None
        ask_target = ask if ask - forecast + rebate_cents >= min_edge else None
        size_factor = 1 / (1 + self.config["volatility_size_weight"] * volatility
                           + self.config["uncertainty_size_weight"] * uncertainty
                           + self.config["adverse_size_weight"] * abs(ret))
        self.record("fair_value", centre, symbol=symbol, price=True)
        self._maintain_quote(state, symbol, "BUY", bid_target, cap, size_factor)
        self._maintain_quote(state, symbol, "SELL", ask_target, cap, size_factor)

    def _maintain_quote(self, state, symbol: str, side: str, target: int | None, cap: int,
                        size_factor: float = 1.0) -> None:
        existing = [o for o in state.orders(symbol) if side_name(o.side) == side]
        for extra in existing[1:]:
            self._cancel(extra)
        position = state.position(symbol)
        allowed = (position < cap if side == "BUY" else position > -cap) and target is not None
        if existing:
            order = existing[0]
            stale = target is not None and abs(order.price - target) > self.config["requote_ticks"]
            if not allowed or stale:
                self._cancel(order)
            return  # Wait for cancellation before re-entering; retain queue if acceptable.
        if not allowed or any(o.symbol == symbol and o.side == side for o in self.pending_orders):
            return
        base_size = min(int(self.model_params["quote_size"]),
                        self.config["quote_size_caps"][symbol])
        available = max(0, cap - position) if side == "BUY" else max(0, cap + position)
        size = min(max(1, math.floor(base_size * size_factor)), available)
        if size > 0:
            self._send(state, symbol, side, size, target, market_making=True)

    def _maybe_prepare_arb(self, state) -> None:
        edges = basket_edges(state.top, self.limits.taker_fee_mills,
                             self.limits.conversion_fee_mills)
        if edges is None:
            return
        rich, cheap = edges
        if max(rich, cheap) <= self.model_params["arb_margin"]:
            return
        self.arb_direction = "RICH" if rich >= cheap else "CHEAP"
        self.arb_phase = "PREPARE"
        for symbol in ETF_LEGS:
            self._cancel_resting(state, symbol)

    def _advance_arb(self, state) -> None:
        if self.arb_phase == "PREPARE":
            if any(o.symbol in ETF_LEGS for o in state.open_orders):
                for symbol in ETF_LEGS:
                    self._cancel_resting(state, symbol)
                return
            if any(state.top[s].halted or state.batch <= self.cooldown[s] for s in ETF_LEGS):
                self.arb_phase = "IDLE"
                return
            edges = basket_edges(state.top, self.limits.taker_fee_mills,
                                 self.limits.conversion_fee_mills)
            if edges is None:
                self.arb_phase = "IDLE"
                return
            edge = edges[0 if self.arb_direction == "RICH" else 1]
            qty = arb_clip(state.top, self.arb_direction, self.config["arb_clip"])
            qty = min(qty, self.symbol_info["HPPO"].max_order_qty,
                      self.symbol_info["BNKY"].max_order_qty,
                      self.symbol_info["BRRY"].max_order_qty // 2)
            if edge <= self.model_params["arb_margin"] or qty < self.config["arb_min_clip"]:
                self.arb_phase = "IDLE"
                self.arb_quiet_until = state.batch + self.config["arb_retry_batches"]
                return
            self.arb_baseline = {s: state.position(s) for s in ETF_LEGS}
            h, b, r = (state.top[s] for s in ETF_LEGS)
            if self.arb_direction == "RICH":
                legs = (("HPPO", "SELL", qty, h.bid_px),
                        ("BNKY", "BUY", qty, b.ask_px),
                        ("BRRY", "BUY", 2 * qty, r.ask_px))
            else:
                legs = (("HPPO", "BUY", qty, h.ask_px),
                        ("BNKY", "SELL", qty, b.bid_px),
                        ("BRRY", "SELL", 2 * qty, r.bid_px))
            # Check every leg against the same ledger before sending any of them.
            projected = self._live_risk_orders(state)
            weights = {s: self.symbol_info[s].exposure_weight_milli for s in SYMBOLS}
            limits = {s: self.symbol_info[s].position_limit for s in SYMBOLS}
            sizes = {s: self.symbol_info[s].max_order_qty for s in SYMBOLS}
            safe = self.messages_left() >= 3 + self.config["message_reserve"]
            for symbol, side, amount, price in legs:
                candidate = OrderRisk(symbol, side, amount)
                safe = safe and can_add_order(state.portfolio.positions, projected, candidate,
                                               limits, sizes, weights, self.limits.exposure_limit,
                                               self.limits.max_open_orders_per_symbol,
                                               self.config["exposure_buffer"])
                projected.append(candidate)
            if not safe:
                self.arb_phase = "IDLE"
                self.arb_quiet_until = state.batch + self.config["arb_retry_batches"]
                return
            sent = [self._send(state, s, side, amount, price, ioc=True)
                    for s, side, amount, price in legs]
            if not all(sent):
                self.log.error("ETF leg send incomplete: %s; will settle observed fills", sent)
            self.stats["arb_attempts"] += 1
            self.arb_phase = "WAIT_LEGS"
            self.log.info("ETF %s qty=%s net edge=%.2f cents, sent=%s", self.arb_direction,
                          qty, edge, sent)
            return
        if self.arb_phase == "WAIT_LEGS":
            self._settle_arb(state)
            return
        if self.arb_phase == "WAIT_CONVERSION":
            self.arb_phase = "UNWIND"
        if self.arb_phase == "UNWIND":
            self._unwind_arb(state)

    def _arb_delta(self, state) -> dict[str, int]:
        return {s: state.position(s) - self.arb_baseline[s] for s in ETF_LEGS}

    def _settle_arb(self, state) -> None:
        delta = self._arb_delta(state)
        units = settlement_units(delta, self.arb_direction)
        if units and not state.top["HPPO"].halted and self.messages_left() > 0:
            weights = {s: self.symbol_info[s].exposure_weight_milli for s in SYMBOLS}
            limits = {s: self.symbol_info[s].position_limit for s in SYMBOLS}
            kind = "CREATE" if self.arb_direction == "RICH" else "REDEEM"
            if can_convert(state.portfolio.positions, self._live_risk_orders(state), kind, units,
                           limits, weights, self.limits.exposure_limit):
                (self.create if kind == "CREATE" else self.redeem)(units)
                self.arb_phase = "WAIT_CONVERSION"
                return
        self.arb_phase = "UNWIND"
        self._unwind_arb(state)

    def _unwind_arb(self, state) -> None:
        delta = self._arb_delta(state)
        if all(value == 0 for value in delta.values()):
            self.arb_phase = "IDLE"
            self.arb_direction = None
            self.arb_baseline = None
            self.arb_quiet_until = state.batch + self.config["arb_retry_batches"]
            return
        for symbol in ETF_LEGS:
            value, top = delta[symbol], state.top[symbol]
            if not value or top.halted:
                continue
            side = "SELL" if value > 0 else "BUY"
            price = top.bid_px if side == "SELL" else top.ask_px
            depth = top.bid_qty if side == "SELL" else top.ask_qty
            if price is None or not depth:
                continue
            qty = min(abs(value), depth, self.symbol_info[symbol].max_order_qty)
            if self._send(state, symbol, side, qty, price, ioc=True):
                self.stats["unwind_orders"] += 1

    def on_end(self, state: BatchState) -> None:
        self.log.info("final positions=%s estimated_pnl=$%.2f stats=%s arb_phase=%s",
                      state.portfolio.positions, state.pnl_est_mills / 1000,
                      self.stats, self.arb_phase)
