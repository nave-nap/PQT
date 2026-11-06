# PQT trading strategy

## Frozen competition build (Stage 6)

Run `strategies/competition_strategy.py` with `PQT_PRESET=balanced` (also the default). This is the frozen Stage 5 **microprice market maker** wrapped with minute-level monitoring, persistent-order startup cleanup, and a fail-closed exception latch. The fallback is the separately backtested `strategies/final_strategy.py`. Both use the same microprice trading rules; the fallback lacks the Stage 6 monitoring and exception wrapper. `results/competition_freeze.json` records SHA-256 hashes of the strategy and configuration files. Verify them with `.venv/bin/python research/verify_competition_freeze.py` before each round. Operational commands and the sandbox status are in `COMPETITION_CHECKLIST.md`.

The final build observes HPPO, BNKY, CRRT, and BRRY. It quotes using the current best-level microprice, widened for spread and observed volatility and skewed for inventory and weighted exposure. It **does not use the Stage 3 learned fair-value models for trading**, does not apply the basket fair-value adjustment, and does not execute ETF arbitrage: those components lost the Stage 5 selection comparison. The code still loads the research model bundle at startup, but the selected fair-value branch reads the observed microprice. `prediction_threshold`, `arb_margin`, and `arb_enabled` are therefore inert controls in this build. Historical prices are public-price proxies, not the official hidden true values.

### Tested presets

The preset file is `results/competition_presets.json`. All three ran through the installed SDK v0.2.3 on full validation sessions s17–s20 with zero recorded rejects, self-trades, or exchange-limit breaches. The balanced preset exactly reproduces the Stage 5 selected microprice strategy on s17. Keep **balanced** as the competition default. The aggressive preset produced larger historical marks but also much more inventory and order activity; live queue and pickoff behavior could reverse that comparison.

| Setting | Conservative | Balanced (selected) | Aggressive diagnostic |
|---|---:|---:|---:|
| `half_width` (cents) | 4 | 3 | 2 |
| `quote_size` (shares) | 8 | 15 | 20 |
| `inventory_skew` (cents) | 6 | 4 | 4 |
| `min_quote_edge` (cents) | 2 | 1 | 0 |
| `news_cooldown_s` | 90 | 60 | 60 |
| HPPO/BNKY/CRRT/BRRY position caps | 100/150/150/50 | 150/250/250/80 | 150/250/250/80 |
| HPPO/BNKY/CRRT/BRRY quote size caps | 8/12/12/5 | 15/25/25/8 | 20/30/30/10 |
| Exposure buffer; message reserve | 200; 4 | 100; 2 | 100; 2 |
| `requote_ticks` | 4 | 3 | 2 |

Conservative also uses `volatility_width_weight=0.8` versus 0.5 otherwise. All static caps remain inside SDK exchange limits. Only SDK `MODEL_PARAMS` may be changed from the trader GUI during a session; static caps and preset choice require a process restart, preferably between rounds. A changed parameter schema is accepted only between sessions. Do not change the frozen files to tune after seeing the holdout.

### Operational behavior

- INFO monitoring prints every 120 exchange batches (one minute): actual positions, current and worst-case weighted exposure, each resting order and remaining quantity, remaining message budget, four observed microprices, last-minute fills/shares/fees, rejects, SDK estimated PnL, news cooldown, and failure latch. Event details for fill/ack/cancel/replace are DEBUG; news, rejects, reconnects, and safety failures are WARN/ERROR.
- On startup with pre-existing open orders or on reconnect, the strategy cancels persistent orders, clears stale market history, and waits for 241 fresh contiguous batches before quoting. At 500 ms/batch this is about 120.5 seconds. It does not guess its inventory; the SDK snapshot is authoritative.
- On an unexpected callback exception in test/live mode, new trading latches off and `cancel_all` is retried while orders remain. In backtest mode the exception is re-raised so validation fails. A disconnected client cannot guarantee remote order cleanup; `--cancel-on-disconnect` and trader-GUI inspection remain required.
- Halts, empty book sides, relevant news, a kill switch, rejected orders, and self-trades trigger the defenses described below. News/halts/reconnects have synthetic SDK tests; they were absent from the historical CSVs. Sandbox behavior remains unverified without credentials/network access.

## Frozen Stage 5 selection

The competition candidate is now `strategies/final_strategy.py`, which loads the frozen `results/strategy_selected.json`. Validation favored the **microprice market maker**: it retains the Stage 4 coordinated quote sizing, inventory, news, halt, and risk controls, but disables Stage 3 prediction tilts, basket-price adjustment, and ETF conversion trades. It still observes all four securities. The original combined implementation remains in `our_strategy.py` for research. The final strategy matched the research microprice variant exactly on s17 through the official CLI (estimated PnL $308.92, 1,463 fills, no rejects), before the s21–s24 holdout was opened. See `results/final_evaluation.md` for the sealed test and limitations.

```bash
.venv/bin/pqt backtest strategies/final_strategy.py --data data --name s17
```

The sections below describe the original Stage 4 research implementation in `strategies/our_strategy.py`. It uses the selected Stage 3 fair-value bundle and four symbols. The frozen competition build above disables the learned prediction and ETF branches. The official example strategies are unchanged.

## Decision sequence

1. `on_batch` appends the just-published 500 ms book and trade batch. A skipped batch clears the 241-batch model window. Quotes wait until the window is full; every model input is at or before the decision batch.
2. The saved 60-second selected model supplies each symbol's predicted future midprice. The forecast is clipped and blended with current mid. HPPO also receives a small, clipped observable BNKY + 2 × BRRY adjustment. Model residual RMSE is treated as a width scale, not a calibrated confidence interval.
3. Quote half-width rises with spread, recent mid changes, prediction magnitude, and model uncertainty. New quote size shrinks as volatility, uncertainty, or forecast magnitude rises, and as available position headroom falls. Inventory and weighted portfolio exposure lean the quote center toward reducing risk. Existing quotes within `requote_ticks` of the new target stay in place for queue priority; stale quotes are cancelled and replaced on a later batch.
4. A single risk ledger includes actual positions, all current resting orders (even orders pending cancellation), and messages already planned this batch. It checks both possible position extremes and both weighted exposure extremes, including CRRT's negative exposure weight. Market-making caps and a 100 ETF-equivalent-share exposure buffer are tighter than the exchange limits. Orders are limited to one per side per symbol and a 2-message reserve remains for urgent actions.
5. ETF trades require an executable HPPO-versus-basket discrepancy exceeding all four taker fees, the conversion fee, and `arb_margin`. The strategy cancels ETF-leg quotes and waits for acknowledgments before sending a three-leg IOC clip. It then converts only filled complete units. Any residual leg is unwound from the actual fill-derived position change relative to the pre-trade baseline. ETF-leg market making stays off until settlement finishes. No simultaneous fill is assumed.
6. Named news cools that ticker; news involving any ETF leg cools HPPO, BNKY, and BRRY; unrecognized news cools all four. Quotes are cancelled through the normal batch decision path. A halted symbol receives no new orders, and its quotes are cancelled. Pending ETF exposure is unwound when a valid book returns. After reconnect, history is reset and persistent orders are reconciled and cancelled before fresh quoting; run live with SDK `--cancel-on-disconnect` as an additional safeguard.

## Order lifecycle

The SDK's next `BatchState.open_orders` and portfolio positions are authoritative. `on_fill`, `on_reject`, `on_cancelled`, and `on_converted` record events and trigger defensive behavior; they do not optimistically alter inventory or order quantities. A rejection pauses new quoting for five batches. A self-trade fill stops fresh trading and cancels resting orders. Prices are integer cents and fees are mills. The strategy never posts at or through the observed opposite touch; it also checks its own opposite resting orders before each send. A cancel remains in the risk ledger until acknowledged.

## Configuration

Static settings live in `results/strategy_config.json`. Runtime controls use SDK `MODEL_PARAMS` and can be changed from the model-control panel. Backtests use these defaults:

| Parameter | Default | Meaning |
|---|---:|---|
| `half_width` | 3 cents | Minimum quote offset from adjusted center |
| `quote_size` | 15 shares | Maximum base size, also capped by symbol |
| `inventory_skew` | 4 cents | Lean at the symbol's market-making position cap |
| `prediction_threshold` | 1 cent | Ignore smaller model-return adjustments |
| `min_quote_edge` | 1 cent | Minimum clipped forecast edge, including maker rebate, for each quote |
| `news_cooldown_s` | 60 seconds | Quote pause after relevant news |
| `arb_margin` | 8 cents | Extra executable ETF edge beyond fees |
| `arb_enabled` | 1 | Enable guarded ETF sequence; set 0 to disable |

Static market-making position caps are ±150 HPPO, ±250 BNKY/CRRT, and ±80 BRRY. Quote sizes cap at 15, 25, 25, and 8 shares. ETF clips cap at 10 HPPO units, with at least 3 available at all three touches. The exact exchange limits and fees are read from the SDK at startup rather than hardcoded into live checks.

## Reproduce

From the repository root:

```bash
.venv/bin/python -m unittest research.test_our_strategy -v
.venv/bin/pqt backtest strategies/our_strategy.py --data data --name s17
.venv/bin/pqt backtest strategies/our_strategy.py --data data --name s18
```

The captured outputs are `results/strategy_s17.log` and `results/strategy_s18.log`. The backtester replays top-of-book history without market response to our orders, cannot model live queue competition or partial ETF fills, and cannot calculate the hidden true-value markout score. Historical sessions contain no news or halts, so those paths are covered by local tests and SDK contract review rather than observed historical events. The final-test sessions s21–s24 were opened only after the Stage 5 selection was frozen.
