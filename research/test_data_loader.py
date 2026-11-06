"""Check the two encodings most likely to distort subsequent research."""

from pathlib import Path
import tempfile
import unittest

import pandas as pd

from data_loader import add_market_variables, aggregate_trade_flow, load_session


class DataLoaderTest(unittest.TestCase):
    def test_missing_book_side_stays_missing_and_trade_side_is_arriving_side(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "s01.top.csv").write_text(
                "batch,symbol,bid_px,bid_qty,ask_px,ask_qty,status\n"
                "0,BNKY,100,10,102,30,TRADING\n"
                "1,BNKY,,,103,20,TRADING\n", encoding="utf-8")
            (root / "s01.trades.csv").write_text(
                "batch,symbol,price,qty,aggressor,self_trade,aux\n"
                "0,BNKY,102,7,BUY,false,{}\n"
                "0,BNKY,100,2,SELL,false,{}\n", encoding="utf-8")
            top, trades = load_session(root, "s01")
        market = add_market_variables(top)
        self.assertEqual(market.loc[0, "mid_px"], 101)
        self.assertEqual(market.loc[0, "spread"], 2)
        self.assertEqual(market.loc[0, "imbalance"], -0.5)
        self.assertEqual(market.loc[0, "microprice"], 100.5)
        self.assertTrue(pd.isna(market.loc[1, "mid_px"]))
        self.assertTrue(pd.isna(market.loc[1, "microprice"]))
        flow = aggregate_trade_flow(trades)
        self.assertEqual(flow.loc[0, "trade_qty"], 9)
        self.assertEqual(flow.loc[0, "signed_qty"], 5)
        self.assertFalse(trades["self_trade"].any())


if __name__ == "__main__":
    unittest.main()
