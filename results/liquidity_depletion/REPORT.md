# Level 1 liquidity depletion and replenishment study

**Decision:** Keep the frozen balanced competition strategy. The depletion feature adds modest 60-second predictive information, but no tested defensive variant has a reliable validation gain under live-style own-touch abstention. The independent fair-value IOC screen produced no qualifying validation opportunity. No experimental variant is deployed or wired into the competition entry point.

## Scope and measurement

The study uses s01–s16 for training and s17–s20 for validation. The validation sessions were used in earlier project work, so they are not a pristine holdout. Previously examined s21–s24 were excluded from every fit, parameter choice, and replay here. All prices are integer cents; snapshots arrive at the end of each 500 ms batch. A strategy order placed after batch *b* is processed in *b+1*. The top CSV is a public best-level aggregate, and the trade CSV gives executions during the batch with aggressor BUY or SELL. A same-price quantity decline is observed depletion, not a verified cancellation or exact trade-consumption amount. Full timing and observability findings are in [AUDIT.md](AUDIT.md).

The primary trading measure is the total maker/taker fee-adjusted public-mid markout 60 seconds after each fill, excluding fills in the final 120 batches. It is expressed in USD by multiplying cents by filled shares. SDK estimated P&L is reported separately. Neither metric is the organizer's hidden true-value score. The replay does not account for our market impact, competitor reactions, or true queue position.

## Feature and prediction findings

`strategies/liquidity_depletion.py` computes per-symbol, price-level-aware same-price declines and recoveries over 2, 5, 10, 20, and 40 batches. It exposes absolute shares, bounded relative rates, streaks, cycles, replenishment frequency/size/speed, a causal five-batch persistence proxy, and trades coincident with the prior touch. Price-level transitions, gaps, invalid books, halts, and own-touch contamination reset the stream. Future observations appear only in research labels. `strategies/liquidity_predictor.py` serves the trained model and returns quality flags; its probability outputs are explicitly **not** marked calibrated.

The strongest practical relationship was the 40-batch normalized ask-minus-bid depletion pressure. On the four validation sessions, mean per-session correlations with the next 60-second mid return were HPPO **0.307** (session t interval 0.188–0.426), BNKY **0.276** (0.227–0.325), CRRT **0.223** (0.001–0.444), and BRRY **0.141** (−0.035–0.318). The existing normalized OFI 40-batch correlations were 0.379, 0.312, 0.249, and 0.103, respectively. The 40-batch depletion signal was stronger than the 5-batch signal for every symbol: 5-batch correlations were 0.116, 0.101, 0.098, and 0.043 in the same order. Raw ask-minus-bid depleted shares over 40 batches were weaker: 0.159 HPPO, 0.064 BNKY, 0.120 CRRT, and 0.073 BRRY. Raw depletion **increased** OFI-controlled validation 60-second MSE for HPPO by 0.637 cents², while normalized depletion reduced it by 1.179 cents². This supports normalizing by prior displayed depth rather than using raw shares. These overlapping labels are summarized by **session**, not treated as independent observations.

In HPPO, moving from the training-defined bottom to top depletion quintile raised the validation next-batch ask-up frequency from **17.7% to 24.8%** and lowered next-batch bid-down frequency from **25.6% to 17.5%**. The top-minus-bottom 60-second mid return was **11.40 cents**. Corresponding return spreads were 3.15 cents BNKY, 3.48 BRRY, and 3.15 CRRT. The probability of *either* best price changing was essentially flat in HPPO (65.0% vs 64.6%), so the signal is mainly directional. Realized 10-second volatility was also similar across HPPO extremes (3.78 vs 3.70 cents).

Adding normalized depletion to OFI, microprice deviation, static imbalance, and 10-second momentum reduced validation 60-second return MSE by **1.179 cents²** for HPPO (135.251→134.072), **0.283** BNKY, **0.618** BRRY, and **0.156** CRRT. This is incremental but small. For next-batch ask/bid events, logistic Brier gains were negligible or negative; HPPO ask-up went from 0.15549 to 0.15550, for example. The simple rule and empirical probability table were also evaluated in `model_comparison.csv`; none justified a more complex model. Five-bin validation calibration errors were measured in `probability_calibration.csv`, but four reused validation sessions are too little to label the probabilities calibrated.

Replenishment generally appears soon after an observed decline, conditional on a cycle completing: validation mean recovery speed ranged from **1.04–1.13 seconds** for BRRY/HPPO to **1.49–1.90 seconds** for CRRT/BNKY, depending on side. Only **2.1–2.9%** of HPPO/BRRY same-price replenishment events held at least half the added depth through five further batches; BNKY was 8.5–9.0% and CRRT 5.4–5.5%. The offline event study found a side-consistent mean 60-second move after replenishment (for example, HPPO bid replenishment +2.24 cents, ask replenishment −0.89 cents), compatible with support/resistance or mean reversion. It does **not** isolate replenishment from concurrent OFI, momentum, or selection into a still-active price level. The 20-batch replenishment feature added 0.685 cents² of validation 60-second MSE improvement for HPPO, but −0.069 BNKY, approximately zero BRRY, and +0.036 CRRT. It is not a general reliable filter. Around 45% of HPPO same-price replenishment events coincided with opposite-side aggressive trades in the same batch; the batch order cannot reveal whether depth was added before or after those trades.

Baseline fill attribution gives a more direct economic test. HPPO fills in the training-defined adverse depletion category had net 60-second markout **−34.38 mills/share** in training and **−42.37 mills/share** in validation. Even after excluding strong adverse OFI, the HPPO category was −12.36/−33.71 mills/share. The same depletion category was still profitable in BRRY (+38.96/+33.65 mills/share), so applying a uniform four-symbol cancellation rule would discard valuable spread capture. Full session and symbol results are in `signal_analysis.csv`, `event_analysis.csv`, `incremental_vs_ofi.csv`, and `baseline_fill_categories.csv`.

## Strategy replay

Only HPPO was enabled for quote interventions by training-only thresholds. The variants inherit the frozen balanced strategy and use its order lifecycle, risk checks, exposure ledger, message reserve, self-trade protection, halt/news behavior, and fail-closed latch. The protection variant suppresses an at-risk quote only if its training conditional 60-second edge is negative. Asymmetric quoting widens/reduces size with hysteresis; replenishment-aware quoting retains a quote when same-side replenishment is strong; the combined variant also requires adverse OFI. No IOC orders were sent.

| Variant | Training total 60s net markout | Training delta | Validation total | Validation delta | Validation positive sessions |
|---|---:|---:|---:|---:|---:|
| Frozen balanced | $6,059.55 | — | $1,612.69 | — | — |
| Protection | $6,099.40 | +$39.85 | $1,600.34 | −$12.35 | 2/4 |
| Asymmetric | $5,980.27 | −$79.28 | $1,574.60 | −$38.09 | 2/4 |
| Replenishment-aware | $6,125.25 | +$65.70 | $1,639.28 | +$26.59 | 2/4 |
| Depletion + OFI | $6,259.14 | +$199.60 | $1,602.99 | −$9.70 | 2/4 |

The apparent replenishment gain is small and uncertain: the validation mean paired session gain was **$6.65**, with a session t interval of **−$18.88 to +$32.17**. Its validation SDK estimated P&L was $1,544.86 versus baseline $1,529.91. Validation replenishment retained 50,635 filled shares versus baseline 52,553; it avoided 1,028 adverse maker shares but also lost 890 profitable maker shares. Asymmetric quoting lost 3,061 profitable maker shares and its total markout fell $38.09 despite avoiding 3,274 adverse shares. Thus reducing bad fills alone is not enough. All five primary variants had zero rejected orders, zero taker fills, at most 8 messages in a batch versus the SDK limit of 20, and worst-case weighted exposure at most 132.14 versus the 600 limit. The largest absolute validation positions across variants were 85 HPPO, 76 BNKY, 110 CRRT, and 61 BRRY shares, all within SDK symbol limits. Validation cancellation counts rose from 10,177 baseline to 10,547 replenishment, 10,593 protection, and 12,195 asymmetric; mean order-to-trade ratios were 4.18, 4.41, 4.52, and 5.27 respectively. Per-session, per-symbol, fee, fill, inventory, and message details are in `strategy_comparison.csv` and `strategy_by_symbol.csv`.

The historical public book omits our simulated orders, so those replays do not experience our own best-quote contamination. In live operation the experimental stream abstains when our order may be at the touch. Replaying that strict rule on validation recorded **18,108** own-touch symbol-batch abstentions in the replenishment variant, only **51** quote interventions, and changed its total markout to **$1,611.47**, **−$1.21** versus baseline. Strict protection gained $3.20 and strict depletion + OFI lost $27.33. These strict results are more representative of feature availability in live conditions and remove the case for promotion.

## Aggressive execution and stress

The independent Stage 3 HPPO 60-second fair-value predictor was screened once every 10 seconds in extreme depletion states. A buy/sell needed predicted edge at the current opposite touch greater than **0.4-cent taker fee + 1-cent delay buffer + 11.845-cent training residual RMSE**. Among 1,554 training extreme-side screens, just **3** passed; none of **396** validation screens passed. Two of the three training passes had no price available at the original touch in the next batch. This is a read-only opportunity screen, not a replayed IOC fill probability, and it supplies no case for an IOC implementation. The IOC variant remains unimplemented and disabled.

`stress_tests.csv` records synthetic checks for no-trade depth loss, replenishment persistence, absorbed aggressive trades, a price jump/new queue, thin BRRY ratios, own-touch abstention, a pre-execution signal reversal, halt/reconnect reset, simultaneous ETF-basket shocks, and extended empty-side liquidity. The partial-IOC scenario is marked inapplicable because no IOC strategy was admitted. True best-price crowding and queue loss are unobservable in Level 1 data. A separate replay sensitivity charges every filled share an extra 1 or 2 cents. With a 2-cent haircut the validation baseline falls to about **$561.63** and replenishment to **$626.58**; its relative gain widens only because it has fewer filled shares. This postprocessing does not replay lost fills or inventory and should not be treated as evidence of real execution robustness. Historical news, halt, reconnect, own-book impact, and multi-leg partial-fill responses remain untested market behaviors; the synthetic checks verify software failure behavior only.

## Answers to the decision questions

1. **Predictive?** Yes, directionally; HPPO 40-batch depletion correlation is 0.307 with the validation 60-second return, but next-event probability gains are modest.
2. **Strongest instruments?** HPPO, then BNKY. CRRT is weaker and uncertain across four sessions; BRRY remains profitable even under adverse depletion categories.
3. **Recovery speed?** Observed cycles recover in about 1–2 seconds on average, but only 2–9% of same-price additions persist five batches under the strict half-depth definition.
4. **Replenishment continuation or reversal?** The unconditional event study is side-consistent with support/resistance and reversal. Incremental value is unstable, so no causal reversal claim is made.
5. **Beyond OFI?** Yes for 60-second returns, by 0.156–1.179 cents² MSE across symbols; little improvement for next-batch event probabilities.
6. **Predictive at 60 seconds?** The 40-batch normalized depletion pressure is most consistent; shorter windows weaken. Replenishment is less stable.
7. **Defensive filtering improves total markout?** No reliably. Three non-replenishment variants lose validation markout; the replenishment gain disappears under own-touch abstention.
8. **Profitable fills lost?** Yes. Even the highest primary validation result lost 890 profitable maker shares; asymmetric lost 3,061.
9. **Asymmetric better than cancellation?** No. Its validation delta was −$38.09 versus protection −$12.35.
10. **Reliable aggressive opportunity?** No. Zero validation screens survived the fee, delay, and uncertainty gate.
11. **Execution robustness?** Risk/message limits held in SDK replay and synthetic fault probes passed; actual queue competition and own-book impact remain unobserved. Live-style own-touch abstention removes the small apparent gain.
12. **Competition strategy change?** No. Preserve frozen balanced. Any future promotion needs new untouched data and authenticated sandbox/competition evidence; this study does not authorize deployment.

## Reproduce

Run from `pqt-student-kit` with the repository virtual environment. The analysis scripts overwrite only their own `results/liquidity_depletion/` outputs. Training and validation are hard-coded to s01–s20.

```bash
.venv/bin/python research/verify_competition_freeze.py
.venv/bin/python -m unittest discover -s research -p 'test_*.py'
.venv/bin/python research/run_liquidity_signals.py
.venv/bin/python research/study_replenishment.py
.venv/bin/python research/fit_liquidity_predictor.py
.venv/bin/python research/attribute_liquidity_fills.py
.venv/bin/python research/build_liquidity_policy.py
.venv/bin/python research/run_liquidity_backtests.py --workers 4
.venv/bin/python research/run_liquidity_backtests.py --workers 4 --strict-own-touch --validation-only
.venv/bin/python research/assess_liquidity_taker.py
.venv/bin/python research/run_liquidity_stress.py
.venv/bin/python research/plot_liquidity_results.py
```

`backtest_metadata.json` records the exact SDK version, rules, sessions, and command. `summary.png` plots the validation session deltas and signal correlations. The frozen strategy files and competition entry point pass the hash check and were not edited.
