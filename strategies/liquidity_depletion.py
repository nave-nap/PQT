"""Causal Level 1 depletion and replenishment observations.

One instance belongs to one session. Quantities are compared only at an
unchanged best price. Trade alignment is observational, never an inferred
cancellation or hidden-order count.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

from strategies.orderflow_imbalance import Book, valid

WINDOWS = (2, 5, 10, 20, 40)
MIN_REL_DEPTH = 5
PERSIST_BATCHES = 5


@dataclass(frozen=True)
class FeatureValue:
    symbol: str
    batch: int
    quality: str
    values: dict[str, float | int | None]


@dataclass
class _Side:
    streak: int = 0
    deficit: int = 0
    last_depletion_batch: int | None = None
    pending_replenishments: list[tuple[int, int]] = field(default_factory=list)
    persistence_outcomes: deque[int] = field(default_factory=lambda: deque(maxlen=40))
    history: deque[tuple[float, float, int, float, float, int, int, int, float | None]] = (
        field(default_factory=lambda: deque(maxlen=40)))


class LiquidityStream:
    def __init__(self, windows: tuple[int, ...] = WINDOWS):
        if not windows or any(w < 1 for w in windows):
            raise ValueError("windows must be positive")
        self.windows = tuple(sorted(set(windows)))
        self._state: dict[str, tuple[int, Book, _Side, _Side]] = {}

    def reset(self, symbol: str | None = None) -> None:
        if symbol is None:
            self._state.clear()
        else:
            self._state.pop(symbol, None)

    @staticmethod
    def _trade_at_previous_touch(trades: Iterable[object], symbol: str,
                                 side: str, price: int) -> int:
        aggressor = "SELL" if side == "bid" else "BUY"
        return sum(int(t.qty) for t in trades
                   if t.symbol == symbol and getattr(t.aggressor, "value", t.aggressor) == aggressor
                   and t.price == price)

    def _side_update(self, side: _Side, *, batch: int, same_price: bool,
                     prior_qty: int, qty: int, aligned_trade_qty: int) -> dict:
        if not same_price:
            # A different best price is a different displayed queue.
            side.streak = side.deficit = 0
            side.last_depletion_batch = None
            side.persistence_outcomes.extend(0 for _ in side.pending_replenishments)
            side.pending_replenishments.clear()
            dep = repl = rel_dep = rel_repl = 0
            eligible = cycle = no_trade_dep = 0
            speed = None
        else:
            dep, repl = max(prior_qty - qty, 0), max(qty - prior_qty, 0)
            rel_dep = min(1.0, dep / max(prior_qty, MIN_REL_DEPTH))
            rel_repl = min(1.0, repl / max(prior_qty, MIN_REL_DEPTH))
            eligible = 1
            side.streak = side.streak + 1 if dep else 0
            no_trade_dep = int(dep > 0 and aligned_trade_qty == 0)
            cycle = 0
            speed = None
            if dep:
                if side.deficit == 0:
                    side.last_depletion_batch = batch
                side.deficit += dep
            if repl:
                cycle = int(side.deficit > 0)
                if cycle and side.last_depletion_batch is not None:
                    speed = (batch - side.last_depletion_batch) / 2
                side.deficit = max(0, side.deficit - repl)
                if side.deficit == 0:
                    side.last_depletion_batch = None
                # A replenishment is "persistent" only if half its added
                # shares remain for five later observed batches at this price.
                side.pending_replenishments.append((batch, qty - max(1, repl // 2)))
            pending = []
            for start, floor in side.pending_replenishments:
                if batch > start and qty < floor:
                    side.persistence_outcomes.append(0)
                elif batch >= start + PERSIST_BATCHES:
                    side.persistence_outcomes.append(1)
                else:
                    pending.append((start, floor))
            side.pending_replenishments = pending
        side.history.append((float(dep), float(rel_dep), eligible, float(repl),
                             float(rel_repl), int(repl > 0), cycle, no_trade_dep, speed))
        return {"eligible": eligible, "depletion": dep, "relative_depletion": rel_dep,
                "depletion_per_second": dep * 2 if eligible else None,
                "replenishment": repl, "relative_replenishment": rel_repl,
                "decrease_streak": side.streak, "cycle": cycle,
                "replenishment_speed_s": speed,
                "replenishment_persistence": (
                    sum(side.persistence_outcomes) / len(side.persistence_outcomes)
                    if side.persistence_outcomes else None),
                "no_trade_depletion": no_trade_dep,
                "aligned_trade_qty": aligned_trade_qty if eligible else None}

    def update(self, symbol: str, batch: int, book: Book,
               trades: Iterable[object] = (), *, own_touch: bool = False) -> FeatureValue:
        if batch < 0 or own_touch or not valid(book):
            self.reset(symbol)
            return FeatureValue(symbol, batch, "own_touch" if own_touch else "invalid", {})
        old = self._state.get(symbol)
        if old is None or batch != old[0] + 1:
            self._state[symbol] = (batch, book, _Side(), _Side())
            return FeatureValue(symbol, batch, "warmup", {})
        _, prior, bid_state, ask_state = old
        trades = tuple(trades)
        values: dict[str, float | int | None] = {
            "bid_px_change": book.bid_px - prior.bid_px,
            "ask_px_change": book.ask_px - prior.ask_px,
            "spread_change": (book.ask_px - book.bid_px) - (prior.ask_px - prior.bid_px),
            "total_depth": book.bid_qty + book.ask_qty,
            "depth_change": book.bid_qty + book.ask_qty - prior.bid_qty - prior.ask_qty,
        }
        for name, side_state, old_px, old_qty, px, qty in (
            ("bid", bid_state, prior.bid_px, prior.bid_qty, book.bid_px, book.bid_qty),
            ("ask", ask_state, prior.ask_px, prior.ask_qty, book.ask_px, book.ask_qty),
        ):
            aligned = self._trade_at_previous_touch(trades, symbol, name, old_px)
            local = self._side_update(side_state, batch=batch, same_price=px == old_px,
                                      prior_qty=old_qty, qty=qty, aligned_trade_qty=aligned)
            values.update({f"{name}_{key}": value for key, value in local.items()})
            hist = list(side_state.history)
            for window in self.windows:
                if len(hist) < window:
                    for key in ("dep_intensity", "dep_shares", "replen_intensity", "replen_frequency",
                                "replen_mean_size", "replen_mean_speed_s", "cycles", "no_trade_dep_fraction",
                                "eligible_fraction"):
                        values[f"{name}_{key}_{window}"] = None
                    continue
                recent = hist[-window:]
                eligible = sum(r[2] for r in recent)
                dep_sum = sum(r[0] for r in recent)
                replen_events = sum(r[5] for r in recent)
                speeds = [r[8] for r in recent if r[8] is not None]
                values[f"{name}_dep_intensity_{window}"] = (sum(r[1] for r in recent) / eligible
                                                                 if eligible else None)
                values[f"{name}_dep_shares_{window}"] = dep_sum
                values[f"{name}_replen_intensity_{window}"] = (sum(r[4] for r in recent) / eligible
                                                                    if eligible else None)
                values[f"{name}_replen_frequency_{window}"] = replen_events / eligible if eligible else None
                values[f"{name}_replen_mean_size_{window}"] = (sum(r[3] for r in recent) / replen_events
                                                                  if replen_events else None)
                values[f"{name}_replen_mean_speed_s_{window}"] = (sum(speeds) / len(speeds)
                                                                     if speeds else None)
                values[f"{name}_cycles_{window}"] = sum(r[6] for r in recent)
                values[f"{name}_no_trade_dep_fraction_{window}"] = (sum(r[7] for r in recent) /
                                                                         sum(r[0] > 0 for r in recent)
                                                                         if dep_sum else None)
                values[f"{name}_eligible_fraction_{window}"] = eligible / window
        for window in self.windows:
            ask = values.get(f"ask_dep_intensity_{window}")
            bid = values.get(f"bid_dep_intensity_{window}")
            values[f"net_depletion_pressure_{window}"] = (ask - bid if ask is not None and bid is not None
                                                            else None)
            ar = values.get(f"ask_replen_intensity_{window}")
            br = values.get(f"bid_replen_intensity_{window}")
            values[f"net_replenishment_pressure_{window}"] = (br - ar if ar is not None and br is not None
                                                                else None)
        self._state[symbol] = (batch, book, bid_state, ask_state)
        return FeatureValue(symbol, batch, "valid", values)


def offline_features(rows: Iterable[tuple[str, int, Book, Iterable[object]]],
                     *, windows: tuple[int, ...] = WINDOWS) -> list[FeatureValue]:
    stream = LiquidityStream(windows)
    return [stream.update(symbol, batch, book, trades) for symbol, batch, book, trades in rows]
