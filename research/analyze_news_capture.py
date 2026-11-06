"""Turn passive SDK captures into descriptive, timing-correct news event rows.

Run: .venv/bin/python -m research.analyze_news_capture results/news_capture/*.jsonl
This reports public-mid movement; it is not a trading backtest or score estimate.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

SYMBOLS = ("HPPO", "BNKY", "CRRT", "BRRY")
TICKERS = re.compile(r"\b(?:HPPO|BNKY|CRRT|BRRY)\b", re.IGNORECASE)
HORIZONS_S = (5, 30, 60)
FIELDS = ("capture_file", "session_id", "news_id", "published_batch",
          "first_seen_batch", "late_seen", "symbol", "named_in_text",
          "headline", "pre_news_mid", "first_seen_mid", "immediate_move_cents",
          "earliest_execution_batch", "earliest_execution_mid",
          "move_5s_cents", "move_30s_cents", "move_60s_cents")


def _mid(batches: dict, batch: int, symbol: str):
    return batches.get(batch, {}).get("top", {}).get(symbol, {}).get("mid")


def event_rows(path: Path) -> list[dict]:
    """Missing snapshots or horizons stay blank; no future book enters an event feature."""
    start = None
    batches = {}
    news = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record["type"] == "start":
                start = record
            elif record["type"] == "batch":
                batches[record["batch"]] = record
            elif record["type"] == "news":
                news.append(record)
    if start is None:
        raise ValueError(f"{path}: missing start record")
    batch_ms = start["batch_ms"]
    if any(seconds * 1000 % batch_ms for seconds in HORIZONS_S):
        raise ValueError(f"{path}: horizons are not whole batches")
    rows = []
    for item in news:
        published = item["published_batch"]
        first_seen = item["first_seen_batch"]
        entry = first_seen + 1  # First batch in which a decision after first sight can execute.
        named = set(match.upper() for match in TICKERS.findall(
            f"{item['headline']} {item['body']}"))
        for symbol in SYMBOLS:
            pre = _mid(batches, published - 1, symbol)
            seen = _mid(batches, first_seen, symbol)
            entry_mid = _mid(batches, entry, symbol)
            row = {"capture_file": str(path), "session_id": start["session_id"],
                   "news_id": item["news_id"], "published_batch": published,
                   "first_seen_batch": first_seen, "late_seen": item["late_seen"],
                   "symbol": symbol, "named_in_text": symbol in named,
                   "headline": item["headline"], "pre_news_mid": pre,
                   "first_seen_mid": seen,
                   "immediate_move_cents": (seen - pre if pre is not None and seen is not None
                                            and not item["late_seen"] else None),
                   "earliest_execution_batch": entry,
                   "earliest_execution_mid": entry_mid}
            for seconds in HORIZONS_S:
                future = _mid(batches, entry + seconds * 1000 // batch_ms, symbol)
                row[f"move_{seconds}s_cents"] = (future - entry_mid
                                                   if future is not None and entry_mid is not None
                                                   else None)
            rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, help="CSV path; default: beside the first capture")
    args = parser.parse_args()
    output = args.output or args.captures[0].parent / "news_events.csv"
    rows = [row for path in args.captures for row in event_rows(path)]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} symbol-event rows from {len(args.captures)} captures to {output}")


if __name__ == "__main__":
    main()
