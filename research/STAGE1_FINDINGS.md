# Stage 1: historical market-data findings

## Scope and timing

The 24 historical sessions have 5,400 half-second batches each. `s01`–`s16` are the **discovery** set for the predictive measurements below. `s17`–`s20` are reserved for model validation; `s21`–`s24` are reserved for final evaluation. Descriptive and integrity checks covered all 24 sessions. No predictive labels from the reserved sessions were inspected.

At the end of batch `b`, the strategy can see that batch's top of book and trades. Its orders are processed in batch `b+1`. Features use only batch `b` or earlier. A horizon label is `mid[b + 2h] - mid[b]`, in cents, for `h` seconds. Rows lacking either side of the book or a full future horizon are excluded. The additional delayed 60-second label is `mid[b + 121] - mid[b + 1]`, which accounts for the first possible execution batch but remains a market-price proxy, not a fill or official score.

The script reports Pearson correlation **averaged across sessions** and the mean difference in future price change between the top and bottom 20% of feature observations within each session. Overlapping 500 ms labels are highly dependent, so observation counts are not independent trials. We emphasize sign consistency across the 16 sessions rather than observation-level significance.

## Data integrity and liquidity

All 24 sessions passed schema and basic price/quantity checks: 518,400 top-of-book rows, 1,271,552 trade rows, 0 crossed two-sided books, 0 non-trading rows, and 0 marked self-trades. There are 663 missing bids and 824 missing asks; a blank side is treated as absent, never zero.

| Symbol | Median spread, cents | 90th-percentile spread, cents | Median best bid shares | Median best ask shares | Median traded shares/session |
|---|---:|---:|---:|---:|---:|
| BNKY | 3.0 | 5.0 | 304 | 299 | 1,040,463 |
| BRRY | 11.0 | 15.0 | 36 | 34 | 197,096 |
| CRRT | 5.0 | 7.0 | 235 | 253 | 1,045,779 |
| HPPO | 4.5 | 7.0 | 136 | 147 | 606,625 |

Each table value is the median of the 24 session-level statistics. BRRY is notably thinner and wider than the others. Its lowest two-sided availability in a single session is 93.72%, so any live strategy must check for missing sides.

## Predictive relationships in `s01`–`s16`

Bid/ask imbalance is `(bid_qty - ask_qty) / (bid_qty + ask_qty)`. The table gives its mean session-level correlation with future midprice change:

| Symbol | 1 s | 5 s | 10 s | 30 s | 60 s |
|---|---:|---:|---:|---:|---:|
| BNKY | 0.344 | 0.294 | 0.239 | 0.153 | 0.118 |
| BRRY | 0.144 | 0.118 | 0.099 | 0.057 | 0.038 |
| CRRT | 0.335 | 0.267 | 0.216 | 0.134 | 0.093 |
| HPPO | 0.301 | 0.210 | 0.162 | 0.105 | 0.082 |

Imbalance has a positive 60-second correlation in all 16 sessions for BNKY, CRRT, and HPPO; in 12/16 for BRRY. The short horizon effect is stronger, but the 1-second label begins at the observed batch and can overstate what is executable one batch later. At 60 seconds with that one-batch delay, mean correlations fall to 0.080 (BNKY), 0.052 (CRRT), 0.061 (HPPO), and 0.016 (BRRY).

Other delayed 60-second relationships, expressed as mean correlation, sessions with a positive correlation, and the mean top-minus-bottom future change in cents:

| Symbol | Feature | Correlation | Positive sessions | Top-bottom, cents |
|---|---|---:|---:|---:|
| HPPO | Past 10-second midprice change | 0.228 | 16/16 | 8.36 |
| HPPO | Trailing 10-second signed trade flow | 0.186 | 16/16 | 6.64 |
| BRRY | Trailing 10-second signed trade flow | 0.180 | 15/16 | 4.06 |
| CRRT | Past 10-second midprice change | 0.169 | 16/16 | 2.26 |
| BNKY | Past 10-second midprice change | 0.158 | 15/16 | 2.24 |

Signed flow is aggressive buy quantity minus aggressive sell quantity, divided by all traded quantity over the trailing 20 batches. Its 1-second correlation is negative for every symbol, while its 60-second correlation is positive. This horizon dependence argues against treating it as one universal directional signal.

For the ETF relationship, define `gap = HPPO mid - BNKY mid - 2 × BRRY mid`. The **feature** is this gap minus its trailing 120-second mean (minimum 30 seconds of history). A positive current gap predicts a later rise in the basket and a falling gap. Mean session-level correlations at 1, 5, 10, 30, and 60 seconds are:

| Future outcome | 1 s | 5 s | 10 s | 30 s | 60 s |
|---|---:|---:|---:|---:|---:|
| HPPO mid change | -0.009 | 0.031 | 0.044 | 0.044 | 0.038 |
| Basket mid change | 0.141 | 0.168 | 0.194 | 0.269 | 0.320 |
| Gap change | -0.136 | -0.146 | -0.167 | -0.264 | -0.360 |

The basket-change correlation is positive in 16/16 discovery sessions at every horizon; the gap-change correlation is negative in 16/16. The high-minus-low gap-residual groups differ by 14.69 cents in future basket change at 60 seconds. This is a midprice movement, not an arbitrage profit: both basket legs have spreads, BRRY is thin, orders arrive a batch later, and live fills can be partial.

## Decision for the next stage

Stage 2 should compare simple models using these candidate features: book imbalance, trailing price change, signed trade flow, and the trailing-centered ETF gap. It should assess delayed 60-second markouts on entire held-out sessions, then test whether execution price, fees, inventory, and the SDK's optimistic fill assumptions erase any apparent edge. The Stage 1 findings alone do **not** justify deploying a strategy.

Historical data excludes hidden true values, news, and halts, so no calculation here reproduces the official score or validates news protection. The SDK's backtest cannot capture the market response to our orders, queue selection, or competition for IOC fills.

## Reproduce

```bash
.venv/bin/python -m unittest discover -s research -p 'test_*.py' -v
.venv/bin/python research/stage1_explore.py --data data
```

Detailed session-level audit and summarized associations are in `research/stage1_results.json`.
