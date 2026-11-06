"""Passive capture and event-timing tests for the SDK news research prototype."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from pqt_client import backtest_v2 as bt
from pqt_client.v2 import backtest_strategy
from pqt_client.v2.state import TopView
from pqt_protocol.v2 import NewsItem, SessionInfo, TradingStatus

from research.analyze_news_capture import event_rows
from strategies.news_capture import NewsCapture

SYMBOLS = ("HPPO", "BNKY", "CRRT", "BRRY")


def state(batch: int, mid: int, news=()):
    top = {symbol: TopView(symbol, mid - 1, 20, mid + 1, 20,
                           TradingStatus.TRADING, None) for symbol in SYMBOLS}
    return NS(batch=batch, top=top, trades=(), news_stack=tuple(news))


class NewsCaptureTest(unittest.TestCase):
    def test_news_stack_deduplicates_and_labels_executable_horizons(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"PQT_NEWS_CAPTURE_DIR": directory}):
                strategy = NewsCapture()
                strategy.session = SessionInfo("synthetic", 132, 500)
                strategy.symbols = SYMBOLS
                strategy.mode = "test"
                strategy.on_start()
                first = NewsItem(7, 10, "BRRY guidance", "")
                late = NewsItem(8, 0, "CRRT old item", "")
                strategy.on_batch(state(9, 100))
                strategy.on_batch(state(10, 102, (first,)))
                strategy.on_reconnect()
                strategy.on_batch(state(11, 103, (first, late)))
                strategy.on_batch(state(21, 105, (first, late)))
                strategy.on_batch(state(71, 106, (first, late)))
                strategy.on_batch(state(131, 107, (first, late)))
                path = strategy.capture_path
                strategy.on_end(state(131, 107, (first, late)))
            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([r["news_id"] for r in records if r["type"] == "news"], [7, 8])
            self.assertEqual(len([r for r in records if r["type"] == "reconnect"]), 1)
            rows = event_rows(path)
            brry = next(r for r in rows if r["news_id"] == 7 and r["symbol"] == "BRRY")
            self.assertTrue(brry["named_in_text"])
            self.assertEqual(brry["immediate_move_cents"], 2)
            self.assertEqual(brry["earliest_execution_batch"], 11)
            self.assertEqual([brry[f"move_{s}s_cents"] for s in (5, 30, 60)], [2, 3, 4])
            old = next(r for r in rows if r["news_id"] == 8 and r["symbol"] == "CRRT")
            self.assertTrue(old["late_seen"])
            self.assertIsNone(old["immediate_move_cents"])
            self.assertIsNone(old["move_5s_cents"])

    def test_official_sdk_replay_places_no_orders(self):
        tops = tuple({spec.symbol: bt.Top(100, 10, 102, 10)
                      for spec in bt.DEFAULT_SYMBOLS} for _ in range(4))
        session = bt.Session("passive_probe", bt.DEFAULT_SYMBOLS, tops,
                             tuple(() for _ in tops))
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"PQT_NEWS_CAPTURE_DIR": directory}):
                strategy = NewsCapture()
                result = backtest_strategy(session, strategy)
            records = [json.loads(line) for line in Path(strategy.capture_path).read_text().splitlines()]
        self.assertEqual(result.fills, 0)
        self.assertFalse(result.rejects)
        self.assertEqual([r["batch"] for r in records if r["type"] == "batch"], [0, 1, 2, 3])
        self.assertEqual(records[-1]["type"], "end")


if __name__ == "__main__":
    unittest.main()
