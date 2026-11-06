"""Switchable depletion quote variants; frozen CompetitionStrategy unchanged.

This module is experimental and is not a competition entry point.
"""

from __future__ import annotations

import json

from strategies.competition_strategy import CompetitionStrategy, ROOT
from strategies.liquidity_depletion import LiquidityStream
from strategies.liquidity_predictor import LiquidityPredictor
from strategies.orderflow_imbalance import OFIStream, from_top, own_at_touch
from strategies.our_components import side_name


class LiquidityExperimental(CompetitionStrategy):
    POLICY = "disabled"  # protection, asymmetric, replenishment, combined
    STRICT_OWN_TOUCH = False

    def on_start(self) -> None:
        try:
            super().on_start()
            if self.failure_latched:
                return
            self.liquidity = LiquidityStream()
            self.ofi = OFIStream()
            self.ld_policy = json.loads((ROOT / "results" / "liquidity_depletion" /
                                         "policy.json").read_text(encoding="utf-8"))
            if self.ld_policy["version"] != "liquidity-policy-v1":
                raise ValueError("liquidity policy version mismatch")
            self.ld_predictor = LiquidityPredictor(ROOT / "results" / "liquidity_depletion" /
                                                   "predictor.json")
            self.ld_features = {}
            self.ld_predictions = {}
            self.ld_ofi = {}
            self.ld_levels = {symbol: {"BUY": 0, "SELL": 0} for symbol in self.symbols}
            self.ld_last_change = {symbol: {"BUY": -100, "SELL": -100} for symbol in self.symbols}
            self.ld_stats = {"valid": 0, "own_touch": 0, "active_side_batches": 0,
                             "quote_interventions": 0}
        except Exception:
            self._fail_closed("liquidity startup")

    def on_reconnect(self) -> None:
        if hasattr(self, "liquidity"):
            self.liquidity.reset()
            self.ofi.reset()
        super().on_reconnect()

    def _quiet(self, batch: int, reason: str) -> None:
        if hasattr(self, "liquidity"):
            self.liquidity.reset()
            self.ofi.reset()
            for symbol in self.symbols:
                self.ld_levels[symbol] = {"BUY": 0, "SELL": 0}
        super()._quiet(batch, reason)

    def on_batch(self, state) -> None:
        if self.failure_latched:
            super().on_batch(state)
            return
        try:
            self._update_liquidity(state)
        except Exception:
            self._fail_closed("liquidity batch", state)
            return
        super().on_batch(state)

    def _update_liquidity(self, state) -> None:
        for symbol in self.symbols:
            top = state.top[symbol]
            own = ((self.mode != "backtest" or self.STRICT_OWN_TOUCH) and
                   own_at_touch(top, state.orders(symbol)))
            feature = self.liquidity.update(symbol, state.batch, from_top(top),
                                            state.trades, own_touch=own)
            ofi = self.ofi.update(symbol, state.batch, from_top(top), own_touch=own)
            self.ld_features[symbol] = feature
            self.ld_ofi[symbol] = ofi.normalized[40]
            self.ld_stats["valid"] += int(feature.quality == "valid" and
                                           feature.values.get("net_depletion_pressure_40") is not None)
            self.ld_stats["own_touch"] += int(own)
            past_mid = None
            if len(self.history) >= 20:
                past_top = list(self.history)[-20]["top"][symbol]
                past_mid = past_top.mid
            depth = (top.bid_qty or 0) + (top.ask_qty or 0)
            values = {"ofi_norm40": self.ld_ofi[symbol],
                      "micro_dev": top.microprice - top.mid if top.microprice is not None
                      and top.mid is not None else None,
                      "imbalance": ((top.bid_qty - top.ask_qty) / depth if top.bid_qty is not None
                                    and top.ask_qty is not None and depth > 0 else None),
                      "momentum_10s": top.mid - past_mid if top.mid is not None
                      and past_mid is not None else None,
                      "net_dep_40": feature.values.get("net_depletion_pressure_40")}
            self.ld_predictions[symbol] = self.ld_predictor.predict(symbol, values,
                                                                     quality=feature.quality)
            for side in ("BUY", "SELL"):
                current = self.ld_levels[symbol][side]
                signal = feature.values.get("net_depletion_pressure_40")
                spec = self.ld_policy["symbols"][symbol]
                threshold = spec["low_threshold"] if side == "BUY" else spec["high_threshold"]
                adverse = ((-signal if side == "BUY" else signal) if signal is not None else None)
                magnitude = abs(threshold)
                if adverse is None or symbol not in self.ld_policy["enabled_symbols"]:
                    desired = 0
                else:
                    ratio = adverse / magnitude if magnitude > 0 else 0
                    if self.POLICY == "asymmetric":
                        desired = min(3, max(0, int(ratio + .2)))
                    else:
                        desired = int(ratio >= (1 if current == 0 else
                                                self.ld_policy["hysteresis_exit_ratio"]))
                if desired != current and (
                    (desired == 0 and (adverse is None or adverse <= 0)) or
                    state.batch >= self.ld_last_change[symbol][side] +
                    self.ld_policy["min_hold_batches"]
                ):
                    self.ld_levels[symbol][side] = desired
                    self.ld_last_change[symbol][side] = state.batch
                self.ld_stats["active_side_batches"] += int(self.ld_levels[symbol][side] > 0)

    def _quote_net_edge(self, state, symbol: str, side: str, price: int) -> float:
        spec = self.ld_policy["symbols"][symbol]
        shift = spec["train_low_return_cents"] if side == "BUY" else spec["train_high_return_cents"]
        future = state.top[symbol].mid + shift
        rebate = self.limits.maker_rebate_mills / 10
        return (future - price if side == "BUY" else price - future) + rebate

    def _replenishment_blocks(self, symbol: str, side: str) -> bool:
        values = self.ld_features[symbol].values
        name = "bid" if side == "BUY" else "ask"
        dep = values.get(f"{name}_dep_intensity_20")
        repl = values.get(f"{name}_replen_intensity_20")
        return bool(dep is not None and repl is not None and dep > 0 and
                    repl / dep >= self.ld_policy["replenishment_recovery_ratio"])

    def _maintain_quote(self, state, symbol: str, side: str, target: int | None,
                        cap: int, size_factor: float = 1.0) -> None:
        if (self.POLICY == "disabled" or symbol not in self.ld_policy["enabled_symbols"] or
                target is None or self.ld_levels[symbol][side] == 0 or
                self.ld_predictions[symbol]["quality"] != "valid"):
            return super()._maintain_quote(state, symbol, side, target, cap, size_factor)
        if self.POLICY == "combined":
            signal = self.ld_ofi[symbol]
            threshold = self.ld_policy["symbols"][symbol]["ofi_abs_threshold"]
            if signal is None or (signal if side == "SELL" else -signal) < threshold:
                return super()._maintain_quote(state, symbol, side, target, cap, size_factor)
        if self.POLICY == "replenishment" and self._replenishment_blocks(symbol, side):
            return super()._maintain_quote(state, symbol, side, target, cap, size_factor)
        existing = [o for o in state.orders(symbol) if side_name(o.side) == side]
        reference_price = existing[0].price if existing else target
        edge = self._quote_net_edge(state, symbol, side, reference_price)
        if self.POLICY == "asymmetric":
            if edge < 1:
                level = self.ld_levels[symbol][side]
                shift = min(self.ld_policy["max_widen_cents"], 2 * level)
                target += (-shift if side == "BUY" else shift)
                size_factor *= 1 - .15 * level
                if existing and edge < 0:
                    self._cancel(existing[0])
                    self.ld_stats["quote_interventions"] += 1
                    return
                self.ld_stats["quote_interventions"] += 1
        elif edge < 0:
            target = None
            self.ld_stats["quote_interventions"] += 1
        return super()._maintain_quote(state, symbol, side, target, cap, size_factor)


class DepletionProtection(LiquidityExperimental):
    POLICY = "protection"


class DepletionAsymmetric(LiquidityExperimental):
    POLICY = "asymmetric"


class ReplenishmentAware(LiquidityExperimental):
    POLICY = "replenishment"


class DepletionPlusOFI(LiquidityExperimental):
    POLICY = "combined"
