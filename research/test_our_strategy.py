"""Stage 4 unit checks for quote, risk, basket, and partial-fill behavior."""

from __future__ import annotations

import sys
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies.our_components import (  # noqa: E402
    OrderRisk, affected_by_news, arb_clip, basket_edges, can_add_order,
    can_convert, quote_prices, settlement_units, worst_case,
)
from strategies.our_strategy import OurStrategy  # noqa: E402

WEIGHTS = {"HPPO": 1000, "BNKY": 150, "CRRT": -290, "BRRY": 425}
LIMITS = {"HPPO": 1000, "BNKY": 2500, "CRRT": 2500, "BRRY": 600}
SIZES = {"HPPO": 200, "BNKY": 500, "CRRT": 500, "BRRY": 120}


def book(bid, ask, bid_qty=100, ask_qty=100):
    return NS(bid_px=bid, ask_px=ask, bid_qty=bid_qty, ask_qty=ask_qty,
              two_sided=True, halted=False)


class ComponentsTest(unittest.TestCase):
    def test_worst_case_negative_weight_and_pending_cancel(self):
        orders = [OrderRisk("HPPO", "BUY", 200), OrderRisk("CRRT", "SELL", 500)]
        bounds, low, high = worst_case({}, orders, WEIGHTS)
        self.assertEqual(bounds["HPPO"], (0, 200))
        self.assertEqual((low, high), (0, 345_000))
        self.assertFalse(can_add_order({}, orders, OrderRisk("HPPO", "BUY", 200),
                                       LIMITS, SIZES, WEIGHTS, 400, 40, 100))
        self.assertFalse(can_add_order({}, [], OrderRisk("BRRY", "BUY", 121),
                                       LIMITS, SIZES, WEIGHTS, 600, 40))
        self.assertFalse(can_add_order({"BRRY": 590}, [], OrderRisk("BRRY", "BUY", 20),
                                       LIMITS, SIZES, WEIGHTS, 600, 40))

    def test_quote_prices_never_cross_public_book(self):
        self.assertEqual(quote_prices(110, 2, 100, 105), (104, 112))
        self.assertEqual(quote_prices(95, 2, 100, 105), (93, 101))

    def test_basket_edges_fees_and_depth(self):
        top = {"HPPO": book(304, 306, 10, 12),
               "BNKY": book(99, 101, 40, 40),
               "BRRY": book(99, 100, 30, 15)}
        rich, cheap = basket_edges(top, 4, 20)
        self.assertAlmostEqual(rich, -0.6)
        self.assertAlmostEqual(cheap, -12.6)
        self.assertEqual(arb_clip(top, "RICH", 20), 7)
        self.assertEqual(arb_clip(top, "CHEAP", 20), 12)

    def test_conversion_and_partial_fills(self):
        delta = {"HPPO": -10, "BNKY": 8, "BRRY": 14}
        self.assertEqual(settlement_units(delta, "RICH"), 7)
        self.assertTrue(can_convert(delta, [], "CREATE", 7, LIMITS, WEIGHTS, 600))
        self.assertFalse(can_convert({"HPPO": 999, "BNKY": 0, "BRRY": 0}, [],
                                     "CREATE", 2, LIMITS, WEIGHTS, 600))
        self.assertEqual(settlement_units({"HPPO": 5, "BNKY": -4, "BRRY": -7},
                                          "CHEAP"), 3)

    def test_news_propagates_to_basket(self):
        self.assertEqual(affected_by_news("BRRY guidance", ""),
                         {"HPPO", "BNKY", "BRRY"})
        self.assertEqual(affected_by_news("CRRT update", ""), {"CRRT"})
        self.assertEqual(len(affected_by_news("market event", "")), 4)


class StrategyLifecycleTest(unittest.TestCase):
    def test_halt_and_news_batch_cancel_quotes(self):
        strategy = OurStrategy()
        strategy._core = NS(param_values={"arb_enabled": 0}, kill_switch=NS(engaged=False))
        strategy.history = deque(maxlen=241)
        strategy.last_batch = None
        strategy.cooldown = {s: -1 for s in LIMITS}
        strategy.cooldown["BNKY"] = 12
        strategy.arb_phase = "IDLE"
        strategy.arb_quiet_until = 0
        strategy.reconnect_pending = False
        strategy.pause_until = 0
        strategy.config = {"stop_quoting_last_batches": 2}
        seen = []
        strategy._cancel_resting = lambda state, symbol=None: seen.append(symbol)
        top = {s: NS(halted=(s == "HPPO")) for s in LIMITS}
        state = NS(batch=10, final=False, blocked=False, top=top, trades=(),
                   open_orders=(), batches_left=100)
        strategy.on_batch(state)
        self.assertEqual(seen, ["HPPO", "BNKY"])

    def test_empty_book_cancels_and_reject_pauses(self):
        strategy = OurStrategy()
        strategy.cooldown = {s: -1 for s in LIMITS}
        cancelled = []
        strategy._cancel_resting = lambda state, symbol=None: cancelled.append(symbol)
        strategy.predictor = NS(predict=lambda *args: self.fail("model must not run"))
        state = NS(batch=300, top={"HPPO": NS(halted=False, two_sided=False)})
        strategy._quote_symbol(state, "HPPO")
        self.assertEqual(cancelled, ["HPPO"])
        strategy.stats = {"rejects": 0, "fills": 0, "self_trades": 0}
        strategy.pause_until = 0
        strategy.on_reject(NS(batch=300, code="EXPOSURE_LIMIT", order_id=1,
                              client_id="x", detail="test"))
        self.assertEqual(strategy.pause_until, 305)
        self.assertEqual(strategy.stats["rejects"], 1)
        strategy.n_batches = 5400
        strategy.on_fill(NS(self_trade=True, symbol="HPPO", order_id=1))
        self.assertEqual(strategy.pause_until, 5400)
        strategy.pending_cancel = {1}
        strategy.on_cancelled(NS(order_id=1))
        self.assertFalse(strategy.pending_cancel)

    def test_news_cooldown_and_reconnect_reset(self):
        strategy = OurStrategy()
        strategy._core = NS(param_values={"news_cooldown_s": 60})
        strategy.session = NS(batch_ms=500)
        strategy.cooldown = {s: -1 for s in LIMITS}
        strategy.history = deque([{"batch": 100}], maxlen=241)
        strategy.last_batch = 100
        strategy.on_news(NS(news_id=1, batch=100, headline="BRRY update", body=""))
        self.assertEqual(strategy.cooldown["BRRY"], 220)
        self.assertEqual(strategy.cooldown["HPPO"], 220)
        self.assertEqual(strategy.cooldown["CRRT"], -1)
        strategy.on_reconnect()
        self.assertEqual(len(strategy.history), 0)
        self.assertTrue(strategy.reconnect_pending)

    def test_own_opposite_order_blocks_crossing_send(self):
        strategy = OurStrategy()
        strategy.config = {"message_reserve": 2, "mm_position_caps": LIMITS,
                           "max_resting_per_symbol": 2, "exposure_buffer": 100}
        strategy.limits = NS(exposure_limit=600, max_open_orders_per_symbol=40)
        strategy.symbol_info = {s: NS(position_limit=LIMITS[s], max_order_qty=SIZES[s],
                                      exposure_weight_milli=WEIGHTS[s]) for s in LIMITS}
        strategy.pending_orders = []
        strategy.messages_left = lambda: 10
        own_ask = NS(symbol="HPPO", side="SELL", price=300, remaining=10)
        state = NS(open_orders=(own_ask,), portfolio=NS(positions={}))
        self.assertFalse(strategy._can_send(state, "HPPO", "BUY", 10, 300,
                                            market_making=True))
        self.assertTrue(strategy._can_send(state, "HPPO", "BUY", 10, 299,
                                           market_making=True))

    def test_keep_acceptable_quote_and_cancel_stale(self):
        strategy = OurStrategy()
        strategy.config = {"requote_ticks": 3, "quote_size_caps": {"HPPO": 15}}
        strategy.pending_orders = []
        strategy.stats = {"cancels": 0}
        strategy._core = NS(param_values={"quote_size": 15})
        seen = []
        strategy._cancel = lambda order: seen.append(("cancel", order.order_id))
        strategy._send = lambda *args, **kwargs: seen.append(("send", args[1:]))
        order = NS(order_id=9, symbol="HPPO", side="BUY", price=100, remaining=10)
        state = NS(orders=lambda symbol: (order,), position=lambda symbol: 0)
        strategy._maintain_quote(state, "HPPO", "BUY", 102, 150)
        self.assertEqual(seen, [])
        strategy._maintain_quote(state, "HPPO", "BUY", 105, 150)
        self.assertEqual(seen, [("cancel", 9)])
        strategy._maintain_quote(state, "HPPO", "BUY", None, 150)
        self.assertEqual(seen[-1], ("cancel", 9))

    def test_partial_fill_unwind_uses_baseline_delta(self):
        strategy = OurStrategy()
        strategy.arb_baseline = {"HPPO": 30, "BNKY": -12, "BRRY": 4}
        strategy.arb_phase = "UNWIND"
        strategy.arb_direction = "RICH"
        strategy.config = {"arb_retry_batches": 10}
        strategy.stats = {"unwind_orders": 0}
        strategy.symbol_info = {s: NS(max_order_qty=SIZES[s]) for s in LIMITS}
        sent = []
        strategy._send = lambda state, s, side, qty, px, **kwargs: (
            sent.append((s, side, qty, px)) or True)
        top = {"HPPO": book(300, 302), "BNKY": book(99, 101),
               "BRRY": book(99, 101)}
        positions = {"HPPO": 27, "BNKY": -11, "BRRY": 4}
        state = NS(batch=500, top=top, position=lambda s: positions[s])
        strategy._unwind_arb(state)
        self.assertEqual(sent, [("HPPO", "BUY", 3, 302),
                                ("BNKY", "SELL", 1, 99)])
        self.assertEqual(strategy.stats["unwind_orders"], 2)
        positions.update(strategy.arb_baseline)
        strategy._unwind_arb(state)
        self.assertEqual(strategy.arb_phase, "IDLE")
        self.assertEqual(strategy.arb_quiet_until, 510)


if __name__ == "__main__":
    unittest.main()
