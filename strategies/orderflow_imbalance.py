"""Causal Level 1 OFI for archived rows and SDK batch callbacks.

Each instance belongs to one session. A gap, invalid book, halt, reset, or own
touch invalidates its chain. Positive increments indicate buy-side pressure.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from numbers import Integral

WINDOWS = (2, 5, 10, 20, 40)


@dataclass(frozen=True)
class Book:
    bid_px: int | None
    bid_qty: int | None
    ask_px: int | None
    ask_qty: int | None
    status: str = "TRADING"


@dataclass(frozen=True)
class OFIValue:
    increment: int | None
    raw: dict[int, int | None]
    normalized: dict[int, float | None]
    prior_mean_depth: float | None


def valid(book: Book) -> bool:
    return (str(book.status) == "TRADING"
            and all(isinstance(x, Integral) and not isinstance(x, bool)
                    for x in (book.bid_px, book.bid_qty, book.ask_px, book.ask_qty))
            and book.bid_px > 0 and book.ask_px > book.bid_px
            and book.bid_qty > 0 and book.ask_qty > 0)


def increment(previous: Book, current: Book) -> int:
    """Price-aware, buy-positive OFI in shares for two valid books."""
    if not valid(previous) or not valid(current):
        raise ValueError("OFI requires two valid trading books")
    bid = (current.bid_qty if current.bid_px > previous.bid_px else
           -previous.bid_qty if current.bid_px < previous.bid_px else
           current.bid_qty - previous.bid_qty)
    ask = (-current.ask_qty if current.ask_px < previous.ask_px else
           previous.ask_qty if current.ask_px > previous.ask_px else
           previous.ask_qty - current.ask_qty)
    return bid + ask


class OFIStream:
    def __init__(self, windows: tuple[int, ...] = WINDOWS):
        if not windows or any(w < 1 for w in windows):
            raise ValueError("windows must be positive")
        self.windows = tuple(sorted(set(windows)))
        self._state: dict[str, tuple[int, Book, deque[int], deque[float]]] = {}

    def reset(self, symbol: str | None = None) -> None:
        if symbol is None:
            self._state.clear()
        else:
            self._state.pop(symbol, None)

    def update(self, symbol: str, batch: int, book: Book,
               *, own_touch: bool = False) -> OFIValue:
        empty = OFIValue(None, {w: None for w in self.windows},
                         {w: None for w in self.windows}, None)
        if batch < 0 or own_touch or not valid(book):
            self.reset(symbol)
            return empty
        old = self._state.get(symbol)
        if old is None or batch != old[0] + 1:
            increments: deque[int] = deque(maxlen=max(self.windows))
            depths: deque[float] = deque(maxlen=max(self.windows))
            depths.append((book.bid_qty + book.ask_qty) / 2)
            self._state[symbol] = (batch, book, increments, depths)
            return empty
        _, previous, increments, depths = old
        # Prior observed depth is a causal scale; the current book cannot set
        # the normalization denominator for its own increment.
        mean_depth = sum(depths) / len(depths)
        value = increment(previous, book)
        increments.append(value)
        depths.append((book.bid_qty + book.ask_qty) / 2)
        raw = {w: sum(list(increments)[-w:]) if len(increments) >= w else None
               for w in self.windows}
        normalized = {w: v / (w * mean_depth) if v is not None and mean_depth > 0
                      else None for w, v in raw.items()}
        self._state[symbol] = (batch, book, increments, depths)
        return OFIValue(value, raw, normalized, mean_depth)


def from_top(top: object) -> Book:
    """Convert a typed SDK top, without changing its published quantities."""
    return Book(top.bid_px, top.bid_qty, top.ask_px, top.ask_qty,
                getattr(top.status, "value", top.status))


def own_at_touch(top: object, orders: object) -> bool:
    """Conservatively flag possible contamination; do not estimate external depth."""
    return any(o.price == (top.bid_px if getattr(o.side, "value", o.side) == "BUY"
                           else top.ask_px) for o in orders)


def offline_features(rows: object, *, windows: tuple[int, ...] = WINDOWS) -> list[OFIValue]:
    """Process chronological row mappings with the same streaming implementation."""
    stream = OFIStream(windows)
    output = []
    for row in rows:
        output.append(stream.update(str(row["symbol"]), int(row["batch"]),
                                    Book(row["bid_px"], row["bid_qty"], row["ask_px"],
                                         row["ask_qty"], row["status"])))
    return output
