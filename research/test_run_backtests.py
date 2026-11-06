"""Checks that Stage 5 ablations change only their declared components."""

from __future__ import annotations

import unittest

from research.run_backtests import _stress_keep, strategy_for


class VariantTest(unittest.TestCase):
    def test_fair_value_and_etf_switches(self):
        micro = strategy_for("microprice", {})
        cross = strategy_for("cross_asset", {})
        predictive = strategy_for("predictive", {})
        basket = strategy_for("predictive_basket", {})
        combined = strategy_for("combined", {})
        self.assertEqual([s.FAIR_MODE for s in (micro, cross, predictive, basket, combined)],
                         ["microprice", "cross_asset", "selected", "selected", "selected"])
        self.assertEqual([s.ETF_ENABLED for s in (micro, cross, predictive, basket, combined)],
                         [False, False, False, False, True])
        self.assertEqual([s.CONFIG_OVERRIDES.get("basket_fair_weight")
                          for s in (micro, cross, predictive)], [0.0, 0.0, 0.0])
        self.assertNotIn("basket_fair_weight", basket.CONFIG_OVERRIDES)
        self.assertNotIn("basket_fair_weight", combined.CONFIG_OVERRIDES)

    def test_parameter_override_and_stress_repeatability(self):
        variant = strategy_for("combined", {"params": {"half_width": 4},
                                            "config": {"volatility_width_weight": 0.8}})
        self.assertEqual(next(p.default for p in variant.MODEL_PARAMS
                              if p.name == "half_width"), 4)
        self.assertEqual(variant.CONFIG_OVERRIDES["volatility_width_weight"], 0.8)
        fill = {"order_id": 5, "batch": 123, "symbol": "HPPO"}
        self.assertEqual(_stress_keep("combined", "s01", fill),
                         _stress_keep("combined", "s01", fill))


if __name__ == "__main__":
    unittest.main()
