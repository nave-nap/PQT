"""Check session-boundary and one-batch feature timing for signal analysis."""

from pathlib import Path
import tempfile
import unittest

import numpy as np

from analyze_signals import future_change, load_features


class SignalTimingTest(unittest.TestCase):
    def test_features_never_use_later_batches_and_targets_stop_at_session_end(self) -> None:
        symbols = ["HPPO", "BNKY", "CRRT", "BRRY"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            top_path = root / "s01.top.csv"
            trade_path = root / "s01.trades.csv"
            n = 130
            def write_top(future_bump: int) -> None:
                lines = ["batch,symbol,bid_px,bid_qty,ask_px,ask_qty,status"]
                for batch in range(n):
                    for offset, symbol in enumerate(symbols):
                        price = 1000 + offset * 100 + batch + (future_bump if batch >= 100 else 0)
                        lines.append(f"{batch},{symbol},{price},10,{price + 2},20,TRADING")
                top_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            trade_path.write_text(
                "batch,symbol,price,qty,aggressor,self_trade,aux\n"
                "50,HPPO,1051,10,BUY,false,{}\n"
                "101,HPPO,1102,20,SELL,false,{}\n", encoding="utf-8")
            write_top(0)
            first, mids, _ = load_features(root, "s01", symbols)
            self.assertTrue(np.isnan(first["HPPO"]["signed_flow_30s"][0]))
            write_top(1000)
            changed, _, _ = load_features(root, "s01", symbols)
        for symbol in symbols:
            for name in first[symbol]:
                np.testing.assert_allclose(first[symbol][name][:100],
                                           changed[symbol][name][:100], equal_nan=True)
        one_second = future_change(mids["HPPO"], 2)
        self.assertAlmostEqual(one_second[99], 2.0)
        self.assertTrue(np.isnan(one_second[-1]))
        sixty_seconds = future_change(mids["HPPO"], 120)
        self.assertAlmostEqual(sixty_seconds[0], 120.0)
        self.assertTrue(np.isnan(sixty_seconds[10]))
        delayed = future_change(mids["HPPO"], 120, decision_delay=1)
        self.assertAlmostEqual(delayed[0], 120.0)
        self.assertTrue(np.isnan(delayed[9]))


if __name__ == "__main__":
    unittest.main()
