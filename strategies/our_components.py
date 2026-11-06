"""Pure pricing, basket, and worst-case risk rules for our v0.2.3 strategy."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

SYMBOLS = ("HPPO", "BNKY", "CRRT", "BRRY")
ETF_LEGS = ("HPPO", "BNKY", "BRRY")


@dataclass(frozen=True)
class OrderRisk:
    symbol: str
    side: str
    qty: int


def side_name(side: object) -> str:
    return str(getattr(side, "value", side)).upper()


def worst_case(positions: Mapping[str, int], orders: Sequence[OrderRisk],
               weights_milli: Mapping[str, int]) -> tuple[dict[str, tuple[int, int]], int, int]:
    """Independent fill extremes; negative CRRT exposure weight reverses its endpoints."""
    bounds = {symbol: [int(positions.get(symbol, 0)), int(positions.get(symbol, 0))]
              for symbol in weights_milli}
    for order in orders:
        if order.side == "BUY":
            bounds[order.symbol][1] += order.qty
        elif order.side == "SELL":
            bounds[order.symbol][0] -= order.qty
        else:
            raise ValueError("invalid order side")
    low = sum(min(weight * lo, weight * hi) for symbol, weight in weights_milli.items()
              for lo, hi in [bounds[symbol]])
    high = sum(max(weight * lo, weight * hi) for symbol, weight in weights_milli.items()
               for lo, hi in [bounds[symbol]])
    return {symbol: (pair[0], pair[1]) for symbol, pair in bounds.items()}, low, high


def can_add_order(positions: Mapping[str, int], orders: Sequence[OrderRisk], candidate: OrderRisk,
                  position_limits: Mapping[str, int], max_sizes: Mapping[str, int],
                  weights_milli: Mapping[str, int], exposure_limit: int,
                  max_open: int, exposure_buffer: int = 0) -> bool:
    if candidate.symbol not in position_limits or candidate.side not in ("BUY", "SELL"):
        return False
    if not 1 <= candidate.qty <= max_sizes[candidate.symbol]:
        return False
    if sum(o.symbol == candidate.symbol for o in orders) >= max_open:
        return False
    bounds, low, high = worst_case(positions, [*orders, candidate], weights_milli)
    if any(lo < -position_limits[s] or hi > position_limits[s]
           for s, (lo, hi) in bounds.items()):
        return False
    cap = (exposure_limit - exposure_buffer) * 1000
    return -cap <= low and high <= cap


def can_convert(positions: Mapping[str, int], orders: Sequence[OrderRisk], kind: str, qty: int,
                position_limits: Mapping[str, int], weights_milli: Mapping[str, int],
                exposure_limit: int) -> bool:
    if kind not in ("CREATE", "REDEEM") or qty < 1:
        return False
    sign = 1 if kind == "CREATE" else -1
    changed = dict(positions)
    changed["HPPO"] = changed.get("HPPO", 0) + sign * qty
    changed["BNKY"] = changed.get("BNKY", 0) - sign * qty
    changed["BRRY"] = changed.get("BRRY", 0) - sign * 2 * qty
    bounds, low, high = worst_case(changed, orders, weights_milli)
    return (all(-position_limits[s] <= lo and hi <= position_limits[s]
                for s, (lo, hi) in bounds.items())
            and -exposure_limit * 1000 <= low <= high <= exposure_limit * 1000)


def basket_edges(top: Mapping[str, object], taker_fee_mills: int,
                 conversion_fee_mills: int) -> tuple[float, float] | None:
    """Positive rich/cheap edge in cents per HPPO unit after four taker fees and conversion."""
    if any(not top[s].two_sided or top[s].halted for s in ETF_LEGS):
        return None
    h, b, r = (top[s] for s in ETF_LEGS)
    costs = (4 * taker_fee_mills + conversion_fee_mills) / 10
    return (h.bid_px - b.ask_px - 2 * r.ask_px - costs,
            b.bid_px + 2 * r.bid_px - h.ask_px - costs)


def arb_clip(top: Mapping[str, object], direction: str, cap: int) -> int:
    h, b, r = (top[s] for s in ETF_LEGS)
    if direction == "RICH":
        return min(cap, h.bid_qty or 0, b.ask_qty or 0, (r.ask_qty or 0) // 2)
    if direction == "CHEAP":
        return min(cap, h.ask_qty or 0, b.bid_qty or 0, (r.bid_qty or 0) // 2)
    raise ValueError("invalid arbitrage direction")


def settlement_units(delta: Mapping[str, int], direction: str) -> int:
    if direction == "RICH":
        return max(0, min(-delta["HPPO"], delta["BNKY"], delta["BRRY"] // 2))
    if direction == "CHEAP":
        return max(0, min(delta["HPPO"], -delta["BNKY"], -delta["BRRY"] // 2))
    raise ValueError("invalid arbitrage direction")


def affected_by_news(headline: str, body: str) -> set[str]:
    named = set(re.findall(r"\b(?:HPPO|BNKY|CRRT|BRRY)\b", (headline + " " + body).upper()))
    if not named:
        return set(SYMBOLS)
    if named & set(ETF_LEGS):
        named.update(ETF_LEGS)
    return named


def quote_prices(centre: float, half_width: float, bid: int, ask: int) -> tuple[int, int]:
    width = max(1.0, half_width)
    buy = min(math.floor(centre - width), ask - 1)
    sell = max(math.ceil(centre + width), bid + 1)
    if buy >= sell:
        sell = buy + 1
    return buy, sell
