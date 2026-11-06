"""Stage 6 fail-safe and reconnect checks without live credentials."""

from __future__ import annotations

import unittest
from collections import deque
from types import SimpleNamespace as NS
from unittest.mock import patch
from pqt_protocol.v2 import CancelReason, NewsItem, Phase
from pqt_client import backtest_v2 as bt
from pqt_client.v2 import backtest_strategy
from strategies.our_components import SYMBOLS

from strategies.competition_strategy import CompetitionStrategy
from strategies.final_strategy import FinalStrategy
from strategies.our_strategy import OurStrategy


class CompetitionSafetyTest(unittest.TestCase):
    def candidate(self, mode="test"):
        strategy = CompetitionStrategy()
        strategy.mode = mode
        strategy._core = NS(param_values={"news_cooldown_s": 60},
                            kill_switch=NS(engaged=False))
        strategy.session = NS(batch_ms=500)
        strategy.symbols = SYMBOLS
        strategy.failure_latched = False
        strategy.last_cleanup_batch = -10
        strategy.seen_news_ids = set()
        strategy.halted_symbols = set()
        strategy.quiet_until = -1
        strategy.last_phase = None
        strategy.history = deque([{"batch": 1}], maxlen=241)
        strategy.last_batch = 1
        strategy.cooldown = {s: -1 for s in SYMBOLS}
        strategy.reconnect_pending = False
        strategy.messages_left = lambda: 10
        return strategy

    def state(self, batch, halted=(), news=(), phase=Phase.RUNNING, orders=()):
        return NS(batch=batch, news_stack=tuple(news), phase=phase,
                  top={s: NS(halted=s in halted) for s in SYMBOLS},
                  open_orders=tuple(orders))

    def test_startup_with_resting_orders_enters_cleanup(self):
        strategy = CompetitionStrategy()
        strategy.open_orders = lambda: (NS(order_id=8),)
        def start(self):
            self.reconnect_pending = False
        with patch.object(FinalStrategy, "on_start", start):
            strategy.on_start()
        self.assertTrue(strategy.reconnect_pending)

    def test_unexpected_live_exception_latches_and_cleans_orders(self):
        strategy = self.candidate()
        cancelled = []
        strategy.messages_left = lambda: 10
        strategy.cancel_all = lambda: cancelled.append("all")
        state = self.state(42, orders=(NS(order_id=1),))
        def fail(self, state):
            raise RuntimeError("synthetic failure")
        with patch.object(FinalStrategy, "on_batch", fail):
            strategy.on_batch(state)
        self.assertTrue(strategy.failure_latched)
        self.assertEqual(cancelled, ["all"])
        strategy.on_batch(self.state(43, orders=state.open_orders))
        self.assertEqual(cancelled, ["all"])
        strategy.on_batch(self.state(44, orders=state.open_orders))
        self.assertEqual(cancelled, ["all", "all"])

    def test_unexpected_backtest_exception_fails_loudly(self):
        strategy = self.candidate("backtest")
        strategy.messages_left = lambda: 10
        strategy.cancel_all = lambda: None
        def fail(self, state):
            raise RuntimeError("synthetic failure")
        with patch.object(FinalStrategy, "on_batch", fail):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                strategy.on_batch(self.state(42, orders=(NS(order_id=1),)))
        self.assertTrue(strategy.failure_latched)

    def test_startup_failure_cancels_and_latches(self):
        strategy = CompetitionStrategy()
        strategy.mode = "test"
        strategy.open_orders = lambda: (NS(order_id=8),)
        strategy.messages_left = lambda: 10
        cancelled = []
        strategy.cancel_all = lambda: cancelled.append("all")
        with patch.object(FinalStrategy, "on_start", side_effect=RuntimeError("load failed")):
            strategy.on_start()
        self.assertTrue(strategy.failure_latched)
        self.assertEqual(cancelled, ["all"])

    def test_late_and_reconnect_news_stack(self):
        strategy = self.candidate()
        item = NewsItem(7, 100, "BRRY guidance")
        with patch.object(FinalStrategy, "on_batch", return_value=None):
            strategy.on_batch(self.state(140, news=(item,)))
            strategy.on_batch(self.state(141, news=(item,)))
            strategy.on_reconnect()
            strategy.on_batch(self.state(142, news=(item, NewsItem(8, 140, "CRRT news"))))
            strategy.on_batch(self.state(300, news=(NewsItem(9, 10, "old news"),)))
        self.assertEqual(strategy.cooldown["HPPO"], 220)
        self.assertEqual(strategy.cooldown["BRRY"], 220)
        self.assertEqual(strategy.cooldown["CRRT"], 260)
        self.assertEqual(strategy.seen_news_ids, {7, 8, 9})
        self.assertFalse(strategy.failure_latched)

    def test_halt_restart_and_resume_cooldown(self):
        strategy = self.candidate()
        calls, cancels = [], []
        strategy._cancel_resting = lambda state: cancels.append(state.batch)
        with patch.object(FinalStrategy, "on_batch", side_effect=lambda state: calls.append(state.batch)):
            strategy.on_batch(self.state(400, halted=("HPPO",)))
            strategy.on_batch(self.state(401))
            strategy.on_batch(self.state(521))
            strategy.on_batch(self.state(522))
        self.assertEqual(strategy.quiet_until, 521)
        self.assertEqual(cancels, [401, 521])
        self.assertEqual(calls, [400, 522])
        self.assertEqual(len(strategy.history), 0)

        strategy = self.candidate()
        with patch.object(FinalStrategy, "on_cancelled", return_value=None):
            strategy.on_cancelled(NS(reason=CancelReason.SYSTEM, batch=500,
                                     order_id=1, symbol="BNKY"))
        self.assertEqual(strategy.quiet_until, 620)
        strategy = self.candidate()
        strategy._cancel_resting = lambda state: None
        with patch.object(FinalStrategy, "on_batch", return_value=None):
            strategy.on_batch(self.state(600, phase=Phase.PAUSED))
            strategy.on_batch(self.state(601))
        self.assertEqual(strategy.quiet_until, 721)

        strategy = self.candidate()
        strategy.on_round_state(NS(phase=Phase.PAUSED, batch=700))
        strategy.on_round_state(NS(phase=Phase.RUNNING, batch=701))
        self.assertEqual(strategy.quiet_until, 821)

    def test_monitoring_exception_latches_after_trading_hook(self):
        strategy = self.candidate()
        strategy._core.param_values = {"news_cooldown_s": 60}
        strategy.cancel_all = lambda: None
        with patch.object(FinalStrategy, "on_batch", return_value=None):
            strategy.on_batch(self.state(119, orders=(NS(order_id=1),)))
        self.assertTrue(strategy.failure_latched)

    def test_sdk_replay_halt_blocks_and_rewarms_quotes(self):
        class Probe(CompetitionStrategy):
            def on_start(self):
                super().on_start()
                self.sent_batches = []

            def buy(self, *args, **kwargs):
                self.sent_batches.append(self.state.batch)
                return super().buy(*args, **kwargs)

            def sell(self, *args, **kwargs):
                self.sent_batches.append(self.state.batch)
                return super().sell(*args, **kwargs)

        tops = tuple({spec.symbol: (bt.Top(None, None, None, None, "HALTED")
                                      if 300 <= b <= 304 else bt.Top(100, 100, 104, 100))
                      for spec in bt.DEFAULT_SYMBOLS} for b in range(800))
        session = bt.Session("synthetic_halt", bt.DEFAULT_SYMBOLS, tops,
                             tuple(() for _ in tops))
        strategy = Probe()
        result = backtest_strategy(session, strategy)
        self.assertFalse(strategy.failure_latched)
        self.assertTrue(any(b < 300 for b in strategy.sent_batches))
        self.assertFalse(any(300 <= b <= 665 for b in strategy.sent_batches))
        self.assertTrue(any(b > 665 for b in strategy.sent_batches))
        self.assertFalse(result.rejects)

    def test_callback_failures_latch_and_cancel(self):
        for method, arg in (("on_news", NewsItem(1, 1, "CRRT")),
                            ("on_reconnect", None), ("on_fill", NS(qty=1)),
                            ("on_model_params", {"half_width": 4})):
            with self.subTest(method=method):
                strategy = self.candidate()
                strategy.open_orders = lambda: (NS(order_id=1),)
                cancelled = []
                strategy.cancel_all = lambda: cancelled.append("all")
                with patch.object(FinalStrategy, method, side_effect=RuntimeError("failure")):
                    getattr(strategy, method)(*(() if arg is None else (arg,)))
                self.assertTrue(strategy.failure_latched)
                self.assertEqual(cancelled, ["all"])

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
