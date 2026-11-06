"""Synthetic SDK replay of a halt and empty book after final-strategy warmup."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pqt_client import backtest_v2 as bt
from pqt_client.v2 import backtest_strategy
from strategies.competition_strategy import CompetitionStrategy


class DefensiveProbe(CompetitionStrategy):
    def on_start(self):
        super().on_start()
        self.seen = []

    def on_batch(self, state):
        super().on_batch(state)
        if state.batch in (240, 241, 242, 243, 245, 246, 247):
            self.seen.append({"batch": state.batch,
                              "hppo_halted": state.top["HPPO"].halted,
                              "brry_two_sided": state.top["BRRY"].two_sided,
                              "hppo_orders": len(state.orders("HPPO")),
                              "brry_orders": len(state.orders("BRRY"))})


def run_probe() -> dict:
    normal = {s.symbol: bt.Top(100, 100, 102, 100) for s in bt.DEFAULT_SYMBOLS}
    tops = []
    for batch in range(250):
        row = dict(normal)
        if batch in (242, 243):
            row["HPPO"] = bt.Top(None, None, None, None, "HALTED")
        if batch in (245, 246):
            row["BRRY"] = bt.Top(None, None, None, None, "TRADING")
        tops.append(row)
    session = bt.Session("defensive_probe", bt.DEFAULT_SYMBOLS, tuple(tops),
                         tuple(() for _ in tops))
    logging.getLogger("pqt.strategy").setLevel(logging.ERROR)
    strategy = DefensiveProbe()
    result = backtest_strategy(session, strategy)
    seen = {row["batch"]: row for row in strategy.seen}
    assert seen[242]["hppo_halted"] and seen[243]["hppo_orders"] == 0
    assert not seen[245]["brry_two_sided"] and seen[246]["brry_orders"] == 0
    assert not result.rejects and strategy.stats["rejects"] == 0
    assert not strategy.failure_latched
    return {"batches": result.batches, "observations": strategy.seen,
            "rejects": dict(result.rejects), "failure_latched": strategy.failure_latched}


if __name__ == "__main__":
    report = run_probe()
    output = Path(__file__).resolve().parents[1] / "results" / "readiness" / "defensive_market.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"PASS: halt and empty-book cleanup through SDK; {output}")
