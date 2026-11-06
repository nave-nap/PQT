"""Stage 6 fail-safe and reconnect checks without live credentials."""

from __future__ import annotations

import unittest
from collections import deque
from types import SimpleNamespace as NS
from unittest.mock import patch

from strategies.competition_strategy import CompetitionStrategy
from strategies.final_strategy import FinalStrategy
from strategies.our_strategy import OurStrategy


class CompetitionSafetyTest(unittest.TestCase):
    def test_startup_with_resting_orders_enters_cleanup(self):
        strategy = CompetitionStrategy()
        strategy.open_orders = lambda: (NS(order_id=8),)
        def start(self):
            self.reconnect_pending = False
        with patch.object(FinalStrategy, "on_start", start):
            strategy.on_start()
        self.assertTrue(strategy.reconnect_pending)

    def test_unexpected_live_exception_latches_and_cleans_orders(self):
        strategy = CompetitionStrategy()
        strategy.failure_latched = False
        strategy.last_cleanup_batch = -10
        strategy.mode = "test"
        cancelled = []
        strategy.messages_left = lambda: 10
        strategy.cancel_all = lambda: cancelled.append("all")
        state = NS(batch=42, open_orders=(NS(order_id=1),))
        def fail(self, state):
            raise RuntimeError("synthetic failure")
        with patch.object(FinalStrategy, "on_batch", fail):
            strategy.on_batch(state)
        self.assertTrue(strategy.failure_latched)
        self.assertEqual(cancelled, ["all"])
        strategy.on_batch(NS(batch=43, open_orders=state.open_orders))
        self.assertEqual(cancelled, ["all"])
        strategy.on_batch(NS(batch=44, open_orders=state.open_orders))
        self.assertEqual(cancelled, ["all", "all"])

    def test_unexpected_backtest_exception_fails_loudly(self):
        strategy = CompetitionStrategy()
        strategy.failure_latched = False
        strategy.last_cleanup_batch = -10
        strategy.mode = "backtest"
        strategy.messages_left = lambda: 10
        strategy.cancel_all = lambda: None
        def fail(self, state):
            raise RuntimeError("synthetic failure")
        with patch.object(FinalStrategy, "on_batch", fail):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                strategy.on_batch(NS(batch=42, open_orders=(NS(order_id=1),)))
        self.assertTrue(strategy.failure_latched)

    def test_reconnect_cancels_persistent_orders_before_quoting(self):
        strategy = OurStrategy()
        strategy.history = deque([{"batch": 9}], maxlen=241)
        strategy.last_batch = 9
        strategy.reconnect_pending = False
        strategy.cooldown = {s: -1 for s in ("HPPO", "BNKY", "CRRT", "BRRY")}
        strategy._core = NS(kill_switch=NS(engaged=False))
        cancelled = []
        strategy._cancel_resting = lambda state, symbol=None: cancelled.append(symbol)
        strategy.on_reconnect()
        state = NS(batch=10, final=False, blocked=False, open_orders=(NS(order_id=1),),
                   top={}, trades=())
        strategy.on_batch(state)
        self.assertEqual(cancelled, [None])
        self.assertTrue(strategy.reconnect_pending)
        self.assertEqual(len(strategy.history), 1)  # New batch, old history removed.


if __name__ == "__main__":
    unittest.main()
