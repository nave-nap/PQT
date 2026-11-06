"""Checks for the synthetic diagnostics, separate from SDK replay rules."""

from __future__ import annotations

import unittest
from types import SimpleNamespace as NS

from research.study_execution_risk import overlay_microprice, stress_rows


class ExecutionRiskDiagnosticTest(unittest.TestCase):
    def test_own_better_bid_and_joined_ask_change_display_microprice(self):
        top = NS(bid_px=100, bid_qty=10, ask_px=104, ask_qty=30,
                 microprice=101.0)
        self.assertAlmostEqual(overlay_microprice(top, [NS(price=102, remaining=10)], []), 1.5)
        self.assertAlmostEqual(overlay_microprice(
            top, [NS(price=102, remaining=10)], [NS(price=104, remaining=10)]), 1.4)
        with self.assertRaisesRegex(ValueError, "locked/crossed"):
            overlay_microprice(top, [NS(price=104, remaining=10)], [])

    def test_fill_stress_break_even_and_adverse_move(self):
        session = NS(name="s01", n_batches=121)
        fill = {"symbol": "HPPO", "liquidity": "MAKER", "order_id": 1,
                "batch": 0, "side": "BUY", "price": 100, "qty": 10,
                "fee_mills": -20, "penalty_mills": 0}
        mids = {"HPPO": [100] * 120 + [102],
                **{s: [100] * 121 for s in ("BNKY", "CRRT", "BRRY")}}
        rows = [r for r in stress_rows(session, [fill], mids, "frozen")
                if r["symbol"] == "HPPO" and r["maker_fill_rate_pct"] == 100]
        self.assertEqual([r["net_markout_usd"] for r in rows], [.22, .12, .02, -.08])
        self.assertEqual(rows[0]["break_even_adverse_cents"], 2.2)
        self.assertEqual(rows[0]["first_nonpositive_tested_cents"], 3)


if __name__ == "__main__":
    unittest.main()
