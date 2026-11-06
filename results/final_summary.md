# PQT final readiness summary — Stage 6

**Decision:** Freeze `strategies/competition_strategy.py` with `PQT_PRESET=balanced` for the competition. This wraps the Stage 5 selected four-symbol microprice market maker with monitoring and fail-closed cleanup. `strategies/final_strategy.py` is the known-working fallback. The exact files and SDK 0.2.3 versions are recorded in `results/competition_freeze.json`; run `research/verify_competition_freeze.py` before each round. No Stage 3 learned model or ETF conversion trade is active in the selected build.

## Evidence

Three presets ran on the same four validation sessions s17–s20, each a full 5,400 exchange batches. Values below are **per-session means** from the unmodified SDK historical replay. Fees are included in the public 60-second midprice markout, but that markout is **not** the official hidden true-value and news-adjusted score. The fill-stress metric retains 75% of maker fills and charges one extra adverse cent per retained share; it is a sensitivity calculation, not an inventory-aware replay.

| Preset | SDK estimated PnL | Net public 60 s markout | Fill-stress markout | Fills | Messages | Mean of session-max worst exposure |
|---|---:|---:|---:|---:|---:|---:|
| Conservative | $155.03 | $171.16 | $96.30 | 1,017 | 4,280 | 54.22 |
| **Balanced (frozen default)** | **$382.48** | **$413.33** | **$228.56** | **1,545** | **6,457** | **99.92** |
| Aggressive diagnostic | $636.79 | $676.85 | $312.73 | 1,953 | 10,535 | 111.70 |

All 12 runs recorded zero rejects, self-trades, or unexpected-exception latches. Maximum messages in any batch were 8 of 20; maximum total open orders were 8, with the exchange permitting 40 per symbol; maximum worst-case weighted exposure was 121.95 of 600 across the runs. The aggressive variant has greater historical proxy results and materially more message/fill activity. It was **not** selected after seeing these results: balanced remains the predeclared Stage 5 choice because live queue position, pickoff risk, and the hidden scoring metric are unavailable. Conservative is a tested lower-risk response if live evidence warrants it.

The frozen balanced build was rerun via `pqt backtest strategies/competition_strategy.py --data data --name s17` after the last startup change: 5,400 batches, $308.92 SDK estimated PnL, 1,463 fills, 12,183 shares, zero rejects. This matches the Stage 5 selected microprice result. The Stage 5 one-time reserved holdout s21–s24 remains unchanged: balanced microprice averaged $392.64 SDK estimated PnL and $418.43 net public 60-second markout per session, positive markout in 4/4 sessions. Stage 6 did **not** tune on or rerun the holdout.

Local lifecycle verification used SDK replay to observe two ACKs, one replacement, one fill, and one cancellation; tracked actual BNKY position and open-order worst cases correctly, with zero rejects. Synthetic defense replay confirmed halted HPPO quotes cancelled and an empty BRRY side caused its quotes to clear. Twenty-one unit tests passed, including restart/reconnect, exception latch/cleanup, pricing and risk. See `results/readiness/lifecycle.json`, `defensive_market.json`, `preset_validation.csv`, and `frozen_balanced_s17.log` for observed outputs. These are local tests and cannot prove real sandbox behavior.

## Unresolved live risks and status

**Sandbox connectivity: BLOCKED.** This environment has no `PQT_API_KEY` or server variable; the official `pqt check` command exits 2 with “no API key,” and sandbox DNS failed from this environment. Therefore authenticated connectivity, live market updates, order ACK/fill/cancel behavior, remote order cleanup, and real reconnect/news/halt handling have **not** been verified. The local tests cover those paths only in replay or synthetic SDK state. Use `COMPETITION_CHECKLIST.md` when credentials and network are available, and record real results before claiming sandbox readiness.

Historical maker fills remain optimistic because the replay does not move the recorded market or model other traders picking off quotes. BRRY contributed much of Stage 5 holdout PnL, making thin-book adverse selection a material live risk. A disconnected process may be unable to cancel orders; use `--cancel-on-disconnect` and verify the trader GUI. The official score uses hidden future true values with news adjustments; neither SDK estimated PnL nor the public-mid markout can reproduce it.

**Readiness assessment:** Locally verified and frozen for a cautious competition launch under the balanced preset; **not fully live-verified** until the team completes the authenticated sandbox check and GUI order-lifecycle exercise. If that cannot be completed, the remaining risk should be accepted explicitly by the team before live deployment. The Round 1 report should guide only limited, documented parameter changes before the twice-weighted Round 2.
