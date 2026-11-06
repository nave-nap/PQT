"""Exercise v0.2.3 ACK, replace, fill, cancel, and state reconciliation offline."""

from __future__ import annotations

import json
from pathlib import Path

from pqt_client import backtest_v2 as bt
from pqt_client.v2 import Strategy, backtest_strategy


class LifecycleProbe(Strategy):
    def on_start(self) -> None:
        self.events = {k: [] for k in ("acks", "replaced", "fills", "cancelled", "rejects")}
        self.snapshots = []

    def on_batch(self, state) -> None:
        self.snapshots.append({"batch": state.batch, "position": state.position("BNKY"),
                               "open": [(o.order_id, o.side.value, o.price, o.remaining)
                                        for o in state.orders("BNKY")],
                               "long_worst": state.limit_usage.symbols["BNKY"].long_worst,
                               "short_worst": state.limit_usage.symbols["BNKY"].short_worst})
        orders = state.orders("BNKY")
        if state.batch == 0:
            self.buy("BNKY", 5, 99)
        elif state.batch == 1:
            assert len(orders) == 1 and orders[0].price == 99
            self.replace(order_id=orders[0].order_id, price=100, qty=5)
        elif state.batch == 2:
            assert len(orders) == 1 and orders[0].price == 100
        elif state.batch == 3:
            assert state.position("BNKY") == 5 and not orders
            self.sell("BNKY", 5, 104)
        elif state.batch == 4:
            assert len(orders) == 1 and orders[0].side.value == "SELL"
            self.cancel(order_id=orders[0].order_id)
        elif state.batch == 5:
            assert state.position("BNKY") == 5 and not orders

    def on_ack(self, ack) -> None:
        self.events["acks"].append({"batch": ack.batch, "id": ack.order_id})

    def on_replaced(self, replaced) -> None:
        self.events["replaced"].append({"batch": replaced.batch, "id": replaced.order_id,
                                        "price": replaced.price,
                                        "kept_priority": replaced.kept_priority})

    def on_fill(self, fill) -> None:
        self.events["fills"].append({"batch": fill.batch, "id": fill.order_id,
                                     "qty": fill.qty, "remaining": fill.remaining})

    def on_cancelled(self, cancelled) -> None:
        self.events["cancelled"].append({"batch": cancelled.batch,
                                         "id": cancelled.order_id,
                                         "reason": cancelled.reason.value})

    def on_reject(self, reject) -> None:
        self.events["rejects"].append({"batch": reject.batch,
                                       "code": reject.code.value,
                                       "detail": reject.detail})


def run_probe() -> dict:
    tops = tuple({s.symbol: bt.Top(100, 100, 102, 100) for s in bt.DEFAULT_SYMBOLS}
                 for _ in range(7))
    trades = tuple((bt.Trade(3, "BNKY", 98, 5, "SELL"),) if b == 3 else ()
                   for b in range(7))
    session = bt.Session("lifecycle_probe", bt.DEFAULT_SYMBOLS, tops, trades)
    strategy = LifecycleProbe()
    result = backtest_strategy(session, strategy)
    assert len(strategy.events["acks"]) == 2
    assert len(strategy.events["replaced"]) == 1
    assert len(strategy.events["fills"]) == 1
    assert len(strategy.events["cancelled"]) == 1
    assert not strategy.events["rejects"] and not result.rejects
    assert result.positions["BNKY"] == 5
    assert strategy.snapshots[1]["long_worst"] == 5
    assert strategy.snapshots[3]["position"] == 5
    assert not strategy.snapshots[5]["open"]
    return {"sdk_result": {"fills": result.fills, "shares": result.shares_traded,
                           "positions": dict(result.positions), "rejects": dict(result.rejects)},
            "events": strategy.events, "snapshots": strategy.snapshots}


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    report = run_probe()
    output = root / "results" / "readiness" / "lifecycle.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"PASS: two ACKs, one replace, one fill, one cancel, zero rejects; {output}")
