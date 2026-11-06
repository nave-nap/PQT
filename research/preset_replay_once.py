"""One isolated SDK replay for one PQT_PRESET; emits machine-readable telemetry."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pqt_client import backtest_v2 as bt  # noqa: E402
from pqt_client.v2 import backtest_strategy  # noqa: E402
from research.run_backtests import Telemetry, summarize  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", choices=tuple(f"s{i:02d}" for i in range(17, 21)),
                        required=True)
    args = parser.parse_args()
    preset = os.environ.get("PQT_PRESET", "balanced")
    from strategies.competition_strategy import CompetitionStrategy  # noqa: E402

    class MeasuredPreset(Telemetry, CompetitionStrategy):
        pass

    logging.getLogger("pqt.strategy").setLevel(logging.ERROR)
    logging.getLogger("pqt.client").setLevel(logging.ERROR)
    session = bt.load_session(ROOT / "data", args.session)
    strategy = MeasuredPreset()
    result = backtest_strategy(session, strategy)
    record = summarize(session, preset, strategy, result)
    record["preset"] = preset
    record["failure_latched"] = strategy.failure_latched
    record["self_trades"] = strategy.stats["self_trades"]
    print(json.dumps(record))


if __name__ == "__main__":
    main()
