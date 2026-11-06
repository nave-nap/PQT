"""Replay the balanced live-safety candidate on training/validation sessions only."""

from __future__ import annotations

import csv
import json
import logging
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pqt_client import backtest_v2 as bt
from pqt_client.v2 import backtest_strategy

from research.run_backtests import Telemetry, summarize
from strategies.competition_strategy import CompetitionStrategy

SESSIONS = tuple(f"s{i:02d}" for i in range(1, 21))


class CandidateTelemetry(Telemetry, CompetitionStrategy):
    pass


def replay(name: str) -> dict:
    logging.getLogger("pqt.strategy").setLevel(logging.ERROR)
    logging.getLogger("pqt.client").setLevel(logging.ERROR)
    session = bt.load_session(ROOT / "data", name)
    strategy = CandidateTelemetry()
    result = backtest_strategy(session, strategy)
    if strategy.failure_latched:
        raise RuntimeError(f"candidate latched on {name}")
    record = summarize(session, "microprice", strategy, result)
    record["split"] = "training" if int(name[1:]) <= 16 else "validation"
    return record


def main() -> None:
    output = ROOT / "results" / "live_safety_candidate"
    output.mkdir(parents=True, exist_ok=True)
    records = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(replay, name): name for name in SESSIONS}
        for future in as_completed(futures):
            record = future.result()
            records.append(record)
            print(f"{record['session']}: PnL=${record['estimated_pnl_usd']:.2f} "
                  f"markout=${record['net_markout_60_usd']:.2f} "
                  f"fills={record['fills']} rejects={record['reject_count']}", flush=True)
    records.sort(key=lambda row: row["session"])
    (output / "sessions.json").write_text(json.dumps(records, indent=2) + "\n",
                                          encoding="utf-8")
    fields = ("session", "split", "estimated_pnl_usd", "net_markout_60_usd",
              "stress_markout_usd", "fills", "shares", "reject_count", "messages",
              "max_abs_worst_exposure", "max_messages_batch", "conversions")
    with (output / "sessions.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: record[key] for key in fields} for record in records)
    print(f"Saved {len(records)} full sessions in {output}")


if __name__ == "__main__":
    main()
