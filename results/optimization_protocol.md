# Stage 5 selection protocol (fixed before validation comparison)

## Data partitions

- Parameter search: s02, s06, s10, s14 only, all within training s01–s16.
- Candidate comparison and strategy selection: full training s01–s16 and validation s17–s20, reported separately.
- Final confirmation: s21–s24 only after `results/strategy_selected.json` is written with `"frozen": true`. No changes based on final-test results in this stage.

Stage 3 already used s17–s20 to choose saved model methods. Reusing those sessions for Stage 5 strategy choice is another development step, so validation results may be optimistic. The final test remains the independent check.

## One-factor parameter search

Seven combined-strategy cases: the Stage 4 defaults, half-width 4 (vs 3), quote size 10 (vs 15), inventory skew 6 (vs 4), minimum maker quote edge 2 cents (vs 1), volatility width multiplier 0.8 (vs 0.5), and ETF margin 12 cents (vs 8). All other settings remain fixed. No news-cooldown experiment is possible because the historical sessions have no news; its default remains 60 seconds. The search does not combine parameter changes.

Select a changed case only if its mean **net 60-second future-mid markout** across the four training sessions beats the default by at least 5%, it is positive in all four sessions, and it has no rejects or weighted exposure violation. Otherwise keep the default. Break ties with the smaller worst-session markout loss, then fewer messages. Estimated PnL after fees and stress markout are secondary checks; neither equals the official score.

## Strategy choice

All our variants use the selected parameter case, identical sessions, the same SDK default exchange rules, and the same quote/risk code. The ordered ablations are microprice → cross-asset → Stage 3 model → model plus observable basket adjustment → model plus basket adjustment and ETF execution. Promote to a more complex variant only when its mean validation net 60-second future-mid markout improves at least 5% over its simpler predecessor, it improves in at least three of four validation sessions, and it has no rejects. A deteriorating conservative stress markout is reason to keep the simpler variant. Compare the resulting variant's total validation markout and estimated PnL with the unmodified official BNKY market-maker reference, noting that the official example trades only one security and uses different quote sizes.

The sensitivity case deterministically retains three quarters of maker fills, retains all taker fills, and deducts one extra cent per retained share. This is a postprocessed stress on future-mid markout, not a replay of cash or inventory. It is used as a robustness check, not as a replacement official score.

## Interpretation

Historical PnL is microprice-marked cash plus inventory after fees. The 60-second markout uses public future midprice with fees, including conversion fees, and caps near-end horizons at the final batch. Both are proxies: hidden true values and news adjustments are unavailable, and the backtest does not model competitor response to our quotes. Session-level consistency matters more than fill-level statistical significance because many fills have overlapping 60-second outcomes.
