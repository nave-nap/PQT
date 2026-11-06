"""Passive PQT v0.2.3 news and market recorder. It never submits orders.

Run with a separate algorithm key if the trading strategy is also connected:
    .venv/bin/pqt run strategies/news_capture.py --mode test --server "$PQT_SERVER"

Output defaults to results/news_capture/<session>-<run>.jsonl. The capture directory
is gitignored because live news text and account-adjacent observations are research
data, not source code. PQT_NEWS_CAPTURE_DIR can override the output directory.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pqt_client.v2 import BatchState, Strategy

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAPTURE_DIR = ROOT / "results" / "news_capture"
CAPTURE_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _enum_value(value):
    return getattr(value, "value", value)


class NewsCapture(Strategy):
    """Record SDK news and public market state without calling trading helpers."""

    def on_start(self) -> None:
        if self.session is None:
            raise RuntimeError("SDK session is unavailable")
        self.seen_news_ids: set[int] = set()
        session_id = re.sub(r"[^A-Za-z0-9_.-]", "_", self.session.session_id)
        run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:12]}"
        capture_dir = Path(os.environ.get("PQT_NEWS_CAPTURE_DIR", DEFAULT_CAPTURE_DIR))
        capture_dir.mkdir(parents=True, exist_ok=True)
        self.capture_path = capture_dir / f"{session_id}-{run_id}.jsonl"
        self._capture = self.capture_path.open("x", encoding="utf-8")
        self._write({"type": "start", "version": CAPTURE_VERSION,
                     "session_id": self.session.session_id,
                     "batch_ms": self.session.batch_ms,
                     "n_batches": self.session.n_batches,
                     "symbols": list(self.symbols), "mode": self.mode,
                     "captured_at_utc": _utc_now()})
        self.log.info("passive news capture: %s", self.capture_path)

    def _write(self, record: dict) -> None:
        self._capture.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._capture.flush()

    def on_reconnect(self) -> None:
        self._write({"type": "reconnect", "last_seen_batch": (
            self.state.batch if self.state is not None else None),
            "captured_at_utc": _utc_now()})

    def on_batch(self, state: BatchState) -> None:
        # The SDK may suppress on_news for items already present in a late-start or
        # reconnect snapshot. The cumulative stack is authoritative for capture.
        for item in state.news_stack:
            if item.news_id in self.seen_news_ids:
                continue
            self._write({"type": "news", "news_id": item.news_id,
                         "published_batch": item.batch,
                         "first_seen_batch": state.batch,
                         "late_seen": state.batch > item.batch,
                         "headline": item.headline, "body": item.body,
                         "captured_at_utc": _utc_now()})
            self.seen_news_ids.add(item.news_id)
        self._write({"type": "batch", "batch": state.batch,
                     "captured_at_utc": _utc_now(),
                     "top": {symbol: {"bid_px": top.bid_px, "bid_qty": top.bid_qty,
                                       "ask_px": top.ask_px, "ask_qty": top.ask_qty,
                                       "status": _enum_value(top.status), "last": top.last,
                                       "mid": top.mid, "microprice": top.microprice}
                             for symbol, top in state.top.items()},
                     "trades": [{"symbol": trade.symbol, "price": trade.price,
                                 "qty": trade.qty,
                                 "aggressor": _enum_value(trade.aggressor),
                                 "self_trade": trade.self_trade,
                                 "aux": trade.aux} for trade in state.trades]})

    def on_end(self, state: BatchState) -> None:
        try:
            self._write({"type": "end", "batch": state.batch,
                         "captured_at_utc": _utc_now()})
        finally:
            self._capture.close()
