# Execution-risk diagnostic: frozen strategy versus live-safety candidate

The frozen wrapper is loaded directly from commit `ab85993`; the candidate is
`e3d5dd2`. Both use the balanced microprice strategy with ETF conversion disabled.
Only the 16 training sessions s01–s16 and four validation sessions s17–s20 were
replayed. The installed `pqt-client` v0.2.3 `backtest_strategy` and its default
rules were unchanged. Both revisions produced identical results in every
symbol-session quote and stress cell, so tables below show their common values.

## What the diagnostics measure

**Own-quote overlay (synthetic).** At each end-of-batch SDK state, compare our
resting orders with the recorded public top. An inside-spread order would strictly
improve the displayed best bid or ask if it remained resting. The rate denominator
is active, two-sided, symbol-side observations, so one order may contribute across
several batches. The “sent inside” rate counts new quote messages priced inside the
public spread at decision time. For the displayed microprice diagnostic, add our
resting quantity at the new best or at a tied touch to the recorded best quantities,
then recompute microprice. Locked/crossed overlays are counted separately and
excluded from that calculation because a live order might execute instead.
This overlay does not change subsequent quotes, fills, or the recorded market.

**Maker-fill stress (synthetic).** Independently retain each historical maker fill
at deterministic hash rates of 100%, 75%, 50%, or 25%; retain any taker fills.
There were no taker fills in these 20 sessions. For each retained fill, subtract an
additional directional adverse move of 0, 1, 2, or 3 cents per share from its net
60-second public-mid markout. Fees remain attached to retained fills. Positions,
future prices, order decisions, and replacement fills are not rerun. The
break-even adverse move is the continuous number of cents per retained maker
share that makes this net markout zero; a negative value means the baseline
markout was already negative. It is an **edge proxy**, not estimated cash PnL
or the official hidden-value score.

The complete results by revision, session, and symbol are in
[`session_symbol_quote_overlay.csv`](session_symbol_quote_overlay.csv) and
[`session_symbol_stress.csv`](session_symbol_stress.csv). Split summaries are in
[`split_symbol_quote_overlay.csv`](split_symbol_quote_overlay.csv) and
[`split_symbol_stress.csv`](split_symbol_stress.csv). The stress CSV includes
all four retention rates and all four adverse moves for every symbol-session.

## Quote exposure and microprice overlay

| Split | Symbol | Inside-spread active side observations | Rate | New quotes sent inside | Rate | Mean absolute microprice change, cents | Locked/crossed observations |
|---|---|---:|---:|---:|---:|---:|---:|
| Training | HPPO | 4,648 / 151,814 | 3.06% | 2,229 / 13,125 | 16.98% | 0.1643 | 42 |
| Training | BNKY | 769 / 161,679 | 0.48% | 299 / 3,409 | 8.77% | 0.0248 | 8 |
| Training | CRRT | 2,282 / 157,334 | 1.45% | 1,373 / 7,387 | 18.59% | 0.0872 | 5 |
| Training | BRRY | 18,565 / 128,782 | 14.42% | 9,022 / 34,802 | 25.92% | 0.9164 | 40 |
| Validation | HPPO | 1,038 / 37,957 | 2.73% | 452 / 3,291 | 13.73% | 0.1379 | 6 |
| Validation | BNKY | 231 / 40,469 | 0.57% | 103 / 803 | 12.83% | 0.0298 | 0 |
| Validation | CRRT | 720 / 39,295 | 1.83% | 404 / 1,881 | 21.48% | 0.1093 | 2 |
| Validation | BRRY | 4,989 / 30,967 | 16.11% | 2,471 / 9,677 | 25.53% | 0.9856 | 17 |

BRRY has the largest potential feedback: about one quarter of new BRRY quotes
are inside the recorded spread, and the static overlay changes its microprice
by about 0.92 cents on training and 0.99 cents on validation on average in
absolute value when our orders are active and the overlay is valid. These are
repeated batch observations, not independent market events.

## Fill stress: total net 60-second public-mid markout, dollars

| Split | Symbol | Maker retention | 0¢ | +1¢ | +2¢ | +3¢ | Break-even adverse move |
|---|---|---:|---:|---:|---:|---:|---:|
| Training | HPPO | 100% | 1,332.16 | 617.29 | -97.58 | -812.45 | 1.86¢ |
| Training | HPPO | 75% | 1,016.38 | 480.87 | -54.64 | -590.15 | 1.90¢ |
| Training | HPPO | 50% | 634.64 | 275.67 | -83.30 | -442.27 | 1.77¢ |
| Training | HPPO | 25% | 379.52 | 197.17 | 14.82 | -167.53 | 2.08¢ |
| Training | BNKY | 100% | 327.86 | 111.62 | -104.62 | -320.86 | 1.52¢ |
| Training | BNKY | 75% | 248.29 | 82.69 | -82.91 | -248.50 | 1.50¢ |
| Training | BNKY | 50% | 162.23 | 54.27 | -53.69 | -161.65 | 1.50¢ |
| Training | BNKY | 25% | 83.48 | 30.55 | -22.38 | -75.31 | 1.58¢ |
| Training | CRRT | 100% | 1,119.52 | 645.49 | 171.46 | -302.57 | 2.36¢ |
| Training | CRRT | 75% | 850.21 | 495.25 | 140.29 | -214.67 | 2.40¢ |
| Training | CRRT | 50% | 553.44 | 313.78 | 74.12 | -165.54 | 2.31¢ |
| Training | CRRT | 25% | 281.18 | 163.50 | 45.82 | -71.86 | 2.39¢ |
| Training | BRRY | 100% | 3,469.62 | 2,760.58 | 2,051.54 | 1,342.50 | 4.89¢ |
| Training | BRRY | 75% | 2,604.64 | 2,070.08 | 1,535.52 | 1,000.96 | 4.87¢ |
| Training | BRRY | 50% | 1,725.74 | 1,367.43 | 1,009.12 | 650.81 | 4.82¢ |
| Training | BRRY | 25% | 840.86 | 661.55 | 482.24 | 302.93 | 4.69¢ |
| Validation | HPPO | 100% | 340.17 | 155.85 | -28.47 | -212.79 | 1.85¢ |
| Validation | HPPO | 75% | 219.79 | 84.37 | -51.05 | -186.47 | 1.62¢ |
| Validation | HPPO | 50% | 120.67 | 31.23 | -58.21 | -147.65 | 1.35¢ |
| Validation | HPPO | 25% | 102.30 | 60.98 | 19.66 | -21.66 | 2.48¢ |
| Validation | BNKY | 100% | 67.16 | 17.35 | -32.46 | -82.27 | 1.35¢ |
| Validation | BNKY | 75% | 57.90 | 20.64 | -16.61 | -53.85 | 1.55¢ |
| Validation | BNKY | 50% | 35.53 | 10.19 | -15.15 | -40.49 | 1.40¢ |
| Validation | BNKY | 25% | 18.91 | 5.46 | -7.99 | -21.43 | 1.41¢ |
| Validation | CRRT | 100% | 270.48 | 150.55 | 30.62 | -89.31 | 2.26¢ |
| Validation | CRRT | 75% | 228.85 | 137.07 | 45.29 | -46.49 | 2.49¢ |
| Validation | CRRT | 50% | 149.56 | 88.66 | 27.75 | -33.15 | 2.46¢ |
| Validation | CRRT | 25% | 72.62 | 41.88 | 11.12 | -19.62 | 2.36¢ |
| Validation | BRRY | 100% | 975.51 | 790.29 | 605.07 | 419.85 | 5.27¢ |
| Validation | BRRY | 75% | 753.09 | 614.76 | 476.43 | 338.10 | 5.44¢ |
| Validation | BRRY | 50% | 503.62 | 412.92 | 322.22 | 231.52 | 5.55¢ |
| Validation | BRRY | 25% | 256.18 | 210.46 | 164.74 | 119.02 | 5.60¢ |

At full observed maker fills, the pooled training markout becomes nonpositive
at +2¢ for HPPO and BNKY, +3¢ for CRRT, and beyond +3¢ for BRRY. Validation
has the same per-symbol thresholds. Across all symbols, the full-fill training
markout falls from $6,249.16 to -$93.38 at +3¢; validation falls from
$1,653.32 to $35.48. The pooled break-even moves are 2.96¢ training and
3.07¢ validation. Changing retention alone mostly scales the dollars because
the dropped fills are selected without reference to future returns; it does
not model adverse fill selection.

## Session-level break-even adverse move, full observed maker fills

Values are cents per retained maker share. BRRY's inside-spread rate and mean
absolute microprice overlay are included because its historical markout is
most exposed to our own quotes changing the displayed book. Full session-level
counts and all rate/adverse combinations are in the CSVs above.

| Session | HPPO | BNKY | CRRT | BRRY | BRRY inside side rate | BRRY mean absolute microprice change |
|---|---:|---:|---:|---:|---:|---:|
| s01 | 1.09 | 2.01 | 1.20 | 3.79 | 10.4% | 0.56¢ |
| s02 | 1.00 | 0.40 | 2.62 | 5.18 | 12.8% | 0.92¢ |
| s03 | 1.60 | 1.62 | 2.27 | 4.36 | 10.3% | 0.50¢ |
| s04 | 2.45 | 1.22 | 2.08 | 5.95 | 15.9% | 1.25¢ |
| s05 | 0.51 | 0.78 | 1.09 | 4.77 | 16.9% | 1.00¢ |
| s06 | 2.24 | 1.57 | 2.01 | 5.31 | 18.0% | 1.19¢ |
| s07 | 2.72 | 2.53 | 1.82 | 4.16 | 12.1% | 0.65¢ |
| s08 | 0.84 | 1.54 | 1.20 | 4.75 | 15.2% | 1.05¢ |
| s09 | 1.74 | 1.54 | 2.52 | 5.32 | 16.7% | 1.29¢ |
| s10 | 2.01 | 0.44 | 2.71 | 4.73 | 14.9% | 0.96¢ |
| s11 | 2.65 | 3.01 | 2.77 | 5.60 | 16.5% | 0.96¢ |
| s12 | 2.25 | 0.12 | 2.88 | 4.66 | 15.5% | 0.87¢ |
| s13 | 2.04 | 0.48 | 2.19 | 5.07 | 15.8% | 1.03¢ |
| s14 | 0.99 | 0.68 | 3.18 | 4.34 | 15.7% | 0.93¢ |
| s15 | 2.31 | 1.44 | 2.29 | 3.99 | 7.7% | 0.42¢ |
| s16 | -0.46 | 0.71 | 2.68 | 6.04 | 18.1% | 1.17¢ |
| s17 | 1.03 | 0.84 | 1.30 | 4.82 | 13.1% | 0.73¢ |
| s18 | 1.88 | 1.68 | 2.78 | 5.72 | 17.4% | 1.03¢ |
| s19 | 2.36 | 0.95 | 2.33 | 5.47 | 16.4% | 1.13¢ |
| s20 | 1.98 | 1.46 | 2.00 | 5.15 | 17.8% | 1.07¢ |

## Release judgment and limits

The safety candidate is worth advancing **as the operational replacement for
the original wrapper**, subject to an authenticated sandbox check of reconnect,
news reconciliation, halt/resume cooldown, and remote order cancellation. It
does not change the selected trading rules or worsen the 20 historical replays.
It does not solve BRRY's execution risk: its high inside-spread incidence means
the public book and microprice could differ materially when our orders are live.
Do not treat the static BRRY markout cushion as a live profitability estimate.

The historical top excludes our orders and does not react to them. The overlay
cannot model queue priority, other participants, fills created or lost by our
displayed liquidity, feedback from a changed microprice into future quote
decisions, or actual exchange order cleanup. The fill stress randomly removes
fills after the fact and does not recompute inventory or future quotes; live
adverse selection could be worse. The data have no news or halts. No s21–s24
session was used, and none of these diagnostics predicts the official score.
