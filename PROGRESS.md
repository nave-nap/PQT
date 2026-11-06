# PQT project progress

Project objective: build and validate a reliable strategy for the November 7, 2026 competition. The official score uses hidden true values about 60 seconds after each fill, adjusted for news and fees. Historical midprice markouts and SDK estimated PnL are proxies, not that score.

## Stage 1 — Environment setup and baseline validation

**Status: COMPLETE**

**Acceptance criteria:** All 24 sessions read through both the research loader and the SDK loader; columns, units, batch semantics, missing values, and trade direction checked; SDK CLI/imports work; `hello_world` and the official market maker complete one and three backtests respectively; full logs and summaries saved; no unresolved data-integrity errors.

**Implementation tasks:** Inspected `README.md`, `STRATEGY_GUIDE.md`, `DATA.md`, `FAQ.md`, `strategies/*.py`, the repository structure, manifest, and actual CSV rows. Added reusable `research/data_loader.py`, `research/validate_data.py`, `research/run_baselines.py`, `research/test_data_loader.py`, `research/README.md`, and `results/baseline/`. The official strategies remain untouched. Earlier exploratory files remain as supplemental research, separate from this baseline.

**Commands executed:**

```bash
git status --short --branch
.venv/bin/python -c 'import pqt_client,pqt_protocol; from importlib.metadata import version; print(version("pqt-client"),version("pqt-protocol"))'
.venv/bin/pqt --help
.venv/bin/pqt backtest --help
.venv/bin/python research/validate_data.py --data data --output results/baseline/data_validation.json
.venv/bin/python -m unittest discover -s research -p 'test_*.py' -v
.venv/bin/python research/run_baselines.py
```

The runner executed these official CLI commands and saved their full output:

```bash
.venv/bin/pqt backtest strategies/hello_world.py --data data --name s01
.venv/bin/pqt backtest strategies/market_maker.py --data data --name s01
.venv/bin/pqt backtest strategies/market_maker.py --data data --name s08
.venv/bin/pqt backtest strategies/market_maker.py --data data --name s16
```

An additional SDK loader check ran `load_manifest('data')` and `load_session('data', session_name)` for every `s01`–`s24`, asserting 5,400 batches and four symbols each. Inspected actual headers and first/last CSV rows with `head`/`tail`.

**Tests performed:** Research validator checked every file's schema, full batch/symbol sequence, positive and uncrossed prices, positive sizes, paired missing prices/sizes, trade direction, trade order, tick sizes, and historical status/aux fields. SDK loader independently read all 24 sessions. Two unit tests passed: missing-side and BUY/SELL decoding, and absence of future-feature leakage in the earlier exploratory utility. All four CLI backtests exited 0 and completed 5,400 batches.

**Actual results:** Python 3.12.14, `pqt-client` 0.2.3, and `pqt-protocol` 0.2.3, matching the README. The data audit passed: 24 sessions, 518,400 top rows, 1,271,552 trades, 663 absent bids, 824 absent asks, no crossed books, and no unresolved integrity errors. The dataset has four symbols (`HPPO`, `BNKY`, `CRRT`, `BRRY`), 500 ms batches numbered 0–5,399, and no wall-clock timestamps. Prices are cents; quantities are shares; `BUY` and `SELL` describe the arriving order. Results below are SDK **estimated PnL**, not competition score:

| Official example | Session | Estimated PnL | Realized PnL | Net fees | Fills | Filled shares | Rejects |
|---|---|---:|---:|---:|---:|---:|---|
| HelloWorld | s01 | $0.00 | $0.00 | $0.00 | 0 | 0 | none |
| MarketMaker | s01 | $470.05 | $466.43 | -$52.01 | 732 | 26,007 | none |
| MarketMaker | s08 | $105.62 | $103.41 | -$21.08 | 315 | 10,540 | none |
| MarketMaker | s16 | $185.30 | $186.24 | -$39.21 | 543 | 19,603 | none |

Negative net fees indicate maker rebates exceeded charges. The logs, exact commands, SDK versions, Git commit, and machine-readable results are in `results/baseline/summary.json` and adjacent `.log` files.

**Problems and limitations:** Historical data contains no hidden true values, news, or halts. The score marks each fill against hidden true value about 60 seconds later, after news adjustments and fees; SDK estimated PnL cannot reproduce it. The v2 backtest does not let our orders change the recorded market, so resting-quote fills and IOC executions are optimistic and cannot test live adverse selection or leg risk. CLI `--runs-dir`/dashboard options are v1-only; the runner captures v2 logs directly. No API key was needed for offline validation.

**Next steps:** Stop and await the user's Stage 2 prompt. Earlier exploratory signal results have not been validated on `s17`–`s24` or against official scoring.

## Stage 2 — Historical data analysis and predictive signal discovery

**Status: COMPLETE**

**Acceptance criteria:** Describe all 24 sessions and four symbols; evaluate seven signal families at 1, 5, 10, 30, and 60 seconds using causal features; summarize session-level uncertainty, quantile outcomes, direction, and effect versus spreads/fees; keep `s21`–`s24` sealed for final testing; deliver reproducible scripts, plots, CSV results, and a report.

**Implementation tasks:** Added `research/explore_market.py`, `research/analyze_signals.py`, and a timing test in `research/test_analyze_signals.py`, all using the Stage 1 loader. Created all-session EDA CSVs and six plots in `results/eda/`, `results/signal_analysis.csv`, `results/signal_report.md`, and session-level/one-batch-delayed signal tables. Updated `research/README.md`. Official examples remain untouched.

**Commands executed:** Inspected Stage 1 loaders, prior exploratory script, manifest, installed scientific packages, and current progress record. Ran:

```bash
.venv/bin/python research/explore_market.py --data data --output results/eda
.venv/bin/python research/analyze_signals.py --data data --results results
.venv/bin/python -m unittest discover -s research -p 'test_*.py' -v
.venv/bin/python -m py_compile research/explore_market.py research/analyze_signals.py research/test_analyze_signals.py
git diff --check
```

Used pandas summaries of the generated CSVs to inspect the rankings, confidence intervals, quintile effects, 500 ms delay checks, CRRT relationships, and all-horizon correlations. Visually inspected the spread, intraday microstructure, signal correlation, and signal quintile plots.

**Tests performed:** EDA output shape checks passed: 96 session-symbol rows and 864 five-minute interval rows. Signal analysis produced 710 split-level rows covering 355 symbol-feature-horizon combinations, seven feature families, all four symbols, and all five horizons. Every validation combination has four session correlations. A synthetic timing test changed all future prices after a decision batch and confirmed every current/past feature was unchanged; it also checked targets become missing before they could cross the session end. All three research unit tests passed. One-batch-delayed results were computed for every horizon.

**Actual results:** All 24 sessions were described; predictive analysis used 16 training and four validation sessions. `s21`–`s24` future labels remain untouched. BRRY raw ETF gap had mean 60-second correlation 0.436 (95% session-level CI 0.371–0.500) in training and 0.498 in validation, with consistent sign in 16/16 and 4/4 sessions. Its one-batch-delayed balanced validation mid move was 5.72 cents versus a crude 6.15-cent half-spread-plus-taker-fee hurdle. HPPO trailing 30-second signed flow had mean 60-second correlation 0.264 in training and 0.244 in validation; its delayed balanced move was 4.58 cents versus a 2.90-cent crude hurdle. HPPO past 10-second momentum correlated 0.229/0.305 with the 60-second change in training/validation. CRRT's 10-second return and HPPO's were negatively related, at mean contemporaneous correlations -0.231/-0.265 in training/validation. Complete numerical evidence and plots are in `results/signal_report.md`; the reproducible ranking is `results/eda/signal_ranking_60s.csv`.

**Problems and limitations:** Historical data contains no official score labels, news, or halts. Overlapping 500 ms horizons were handled by session-level correlation summaries, but the 16/4-session confidence intervals are unadjusted for multiple feature screening. Conditional mid moves and spread/fee hurdles are not fill simulations or profitability estimates. BRRY's strong statistical signal is offset by its wide spread and shallow depth; live response and hidden true-value scoring remain unknown.

**Next steps:** Stop and await the user's Stage 3 prompt. Preserve `s21`–`s24` for final strategy evaluation. HPPO flow and momentum deserve execution-aware testing; BRRY ETF-gap prediction deserves further cost and fill-risk scrutiny.

## Stage 3 — Fair-value prediction models

**Status: COMPLETE**

**Acceptance criteria:** Train and compare current-mid, current-microprice, cross-asset adjustment, linear, and ridge models for all four symbols at 10, 30, and 60 seconds on identical validation periods. Save and reload artifacts; verify causal inference and batch-time performance; select a model for later strategy integration. Keep final-test sessions sealed.

**Implementation tasks:** Added shared, session-bounded model rows in `research/model_data.py`; training, evaluation, and timing scripts in `research/train_models.py`, `research/evaluate_models.py`, and `research/benchmark_inference.py`; pure-Python inference in `models/inference.py`; training-only and validation-selected JSON bundles in `models/`; 60-row `results/model_comparison.csv`, detailed diagnostics and two plots, and `results/model_report.md`. Updated `research/README.md`. No trading strategy was changed or created.

**Commands executed:**

```bash
.venv/bin/python research/train_models.py --data data --output models/fair_value_v1.json
.venv/bin/python research/evaluate_models.py --data data --model models/fair_value_v1.json --results results --selected-output models/fair_value_v1_selected.json
.venv/bin/python research/benchmark_inference.py --repeats 200
.venv/bin/python -m unittest discover -s research -p 'test_*.py' -v
```

Used pandas to inspect all 10/30/60-second comparison rows, four-session stability, prediction bins, and high/low spread and volatility regimes; visually inspected the MAE and calibration plots.

**Tests performed:** Compared mid, microprice, cross-asset, linear, and ridge on identical eligible validation rows for each symbol/horizon. Ridge alpha was chosen by grouped training-session cross-validation. Verified the selected artifact has exactly the training artifact's model coefficients and scales; validated the saved inference features and predictions against the offline pipeline for all four symbols, including the `b + 121` target at 60 seconds. Confirmed an empty BRRY side does not block BNKY inference. All four research unit tests passed. Benchmarked 200 four-symbol calls from a real 241-batch history. The training and selection artifacts list only `s01`–`s16` and `s17`–`s20`; `s21`–`s24` were not read.

**Actual results:** At 60 seconds, validation mean session-level MAE (cents) improved from mid to linear as follows: HPPO 9.566 → 9.054 (5.35%), BNKY 3.395 → 3.232 (4.80%), CRRT 4.060 → 3.844 (5.32%), BRRY 6.551 → 5.717 (12.73%). Linear and ridge were nearly tied; microprice alone was worse than mid on all four. Linear directional accuracy was 62.5%, 60.7%, 61.5%, and 68.7% respectively. HPPO linear 60-second MAE beat the best public-price baseline in 4/4 validation sessions. The conservative one-batch proxy attempted 397 HPPO signals, counted 262 hypothetical fills, and yielded a mean of +2.19 cents/share across the four session-level proxy means, with one session near zero. The selected primary integration candidate is `pqt-fair-value-v1/HPPO/60s/linear`; the selected bundle records a method for every symbol/horizon. Four-symbol inference took 4.37 ms median and 4.50 ms p95, below the 500 ms batch. Full MAE, RMSE, calibration, regime, and proxy results are in `results/model_report.md` and diagnostic CSVs.

**Problems and limitations:** Future midprice is a proxy for the hidden true-value score. Validation has only four whole sessions; HPPO's 95% interval for the absolute 60-second MAE gain versus mid is -0.155 to +1.180 cents, so the gain is uncertain. Adjacent validation rows overlap; session-level stability is emphasized. The proxy ignores live market response, inventory, weighted exposure, and news, so its positive markouts are not profitability evidence. Ridge was not materially better than linear; gradient-boosted trees were deferred for lack of evidence justifying added complexity. Inference's uncertainty field is a training-residual scale, not a calibrated prediction interval.

**Next steps:** Stop and await the user's next stage prompt. A later strategy should start with HPPO 60-second linear fair value, then evaluate actual order rules, fees, inventory, limits, adverse selection, news, and the sealed `s21`–`s24` sessions under a fixed protocol.

## Stage 4 — Implement the PQT trading algorithm

**Status: COMPLETE**

**Acceptance criteria:** One SDK v0.2.3 strategy imports and completes a historical session; model features are causal; worst-case limits include open and pending orders; ETF fee, conversion, and partial-fill rules are implemented; news, halt, reconnect, self-trade, and rejection defenses exist; unit tests pass. Stop before Stage 5.

**Implementation tasks:** Inspected the installed SDK's `Strategy`, `BatchState`, `ClientCore`, and protocol message classes, plus the official market-maker and ETF examples and Stage 3 model artifact. Added `strategies/our_strategy.py`, pure pricing/risk/basket rules in `strategies/our_components.py`, `results/strategy_config.json`, eleven new unit tests in `research/test_our_strategy.py`, and `STRATEGY.md`. The official examples remain unchanged. The strategy uses all four selected 60-second Stage 3 models with 241 observed batches; adaptive quote widths reflect forecast, spread, volatility, model residual scale, inventory, and exposure, while new quote sizes shrink under volatile or uncertain conditions. ETF IOC legs are followed by fill-aware conversion and residual unwind. The SDK's actual state is the authority after fills, cancels, and rejects.

**Commands executed:** Verified installed `pqt-client` and `pqt-protocol` versions 0.2.3 with `pip show`; inspected the SDK and guide with `sed`/`rg`. Then ran:

```bash
.venv/bin/python -m py_compile strategies/our_strategy.py strategies/our_components.py research/test_our_strategy.py
.venv/bin/python -m unittest research.test_our_strategy -v
.venv/bin/python -m unittest discover -s research -p 'test_*.py' -v
.venv/bin/pqt backtest strategies/our_strategy.py --data data --name s17 > results/strategy_s17.log 2>&1
.venv/bin/pqt backtest strategies/our_strategy.py --data data --name s18 > results/strategy_s18.log 2>&1
git diff --check
```

The first s17 attempt failed at the model adapter: a `deque` was passed where Stage 3 inference required a sliceable sequence. The adapter now passes a tuple. Both sessions were rerun after an empty-book cancellation fix and dynamic quote sizing; the results below and saved logs are from the final code.

**Tests performed:** All 15 research tests passed, including 11 new Stage 4 checks for weighted worst-case exposure (including negative CRRT weight), position/order limits, ETF fees and depth, conversion limits, rich/cheap partial-fill settlement, price constraints, queue-preserving keep/cancel decisions, news propagation, halt/news batch cancellation, empty-book cancellation, reconnect history reset, opposite-order self-trade prevention, rejection pause, and self-trade shutdown. The final strategy compiled. Both full-session official CLI backtests exited 0 over 5,400 batches, with no unhandled exception, rejects, or self-trades. Each session exercised one ETF three-leg trade and one successful conversion; historical data did not exercise partial fills, news, halts, or reconnects.

**Actual results:** Historical SDK estimated PnL is a display mark and **not** the official hidden true-value score. The output files contain the exact CLI transcript.

| Validation session | Estimated PnL | Realized PnL | Net fees | Fills | Shares | Rejects | ETF attempts/conversions |
|---|---:|---:|---:|---:|---:|---:|---:|
| s17 | $395.26 | $388.95 | -$13.57 | 1,065 | 7,005 | 0 | 1 / 1 |
| s18 | $484.10 | $476.41 | -$16.46 | 1,185 | 8,385 | 0 | 1 / 1 |

The s17 run submitted 2,140 quotes and 1,200 cancels; s18 submitted 2,782 quotes and 1,747 cancels. These message counts include normal quote refreshes and defensive cancellations. Neither session had a self-trade. Dynamic size reduction lowered historical filled shares and estimated PnL versus the earlier fixed-size runs; it was retained as a risk control, not as a validated profit improvement. `s21`–`s24` remain sealed.

**Problems and limitations:** The SDK backtest leaves historical prices and other participants unaffected by our orders. It gives resting quotes optimistic fills and executes IOC legs against a fixed recorded touch, so it cannot demonstrate live queue priority, adverse selection, or multi-leg fill risk. Its estimated and realized PnL are not the official 60-second hidden true-value score. Historical files contain no news or halts; local tests cover the defensive logic, but a sandbox or live exercise is still required to observe those events and reconnect behavior. Unit tests establish the partial-fill state transitions; no historical partial-fill event occurred in these two sessions. No API credentials or live sandbox run were used.

**Next steps:** Stop here. Stage 5 should use a fixed protocol on the still-reserved s21–s24 sessions, inspect official-score proxies and order churn, and exercise news/halts/reconnects in the sandbox if credentials are available. Do not treat the positive backtest PnL as proof of competition profitability.

## Stage 5 — Backtesting and strategy optimization

**Status: COMPLETE**

**Acceptance criteria:** Compare the official example and microprice, cross-asset, predictive, predictive-plus-basket, and combined variants on all 16 training and four validation sessions with unmodified SDK rules; tune a limited training-only grid; freeze the best validated strategy before one holdout evaluation on s21–s24; report after-fee PnL, security/session breakdowns, maker/taker fills, markouts, risk, messages, rejects, and fill sensitivity. All verified.

**Implementation tasks:** Added fair-value/ETF switches to our strategy for controlled research variants, plus a minimum quote-edge control. Added `research/run_backtests.py` for SDK replay with callback telemetry, `research/compare_strategies.py` for summaries and three plots, `research/test_run_backtests.py`, and `results/optimization_protocol.md`. The official market-maker file remains unchanged. Saved the training-only choice in `results/tuning_choice.json`, then froze `results/strategy_selected.json` before any Stage 5 holdout replay. Added `strategies/final_strategy.py`, which verifies the frozen configuration hash and implements the selected microprice market maker. Full outputs are `results/backtest_comparison.csv`, `results/backtests/`, and `results/final_evaluation.md`.

**Commands executed:** Inspected installed SDK v0.2.3 backtest, core, protocol, and official example. The instrumented official s01 run matched the Stage 1 CLI baseline exactly: $470.054 estimated PnL, 732 fills, 26,007 shares. Reproduced the selected variant via the official CLI on s17 before the holdout (estimated PnL $308.92, 1,463 fills, zero rejects). Main commands:

```bash
.venv/bin/python research/run_backtests.py --phase tune --workers 4
.venv/bin/python research/run_backtests.py --phase trainval --workers 6
.venv/bin/python research/compare_strategies.py
.venv/bin/pqt backtest strategies/final_strategy.py --data data --name s17 > results/final_strategy_s17.log 2>&1
.venv/bin/python research/run_backtests.py --phase holdout --workers 4
.venv/bin/python research/compare_strategies.py --final
.venv/bin/python -m unittest discover -s research -p 'test_*.py' -q
.venv/bin/python -m py_compile research/run_backtests.py research/compare_strategies.py strategies/our_strategy.py strategies/final_strategy.py
git diff --check
```

The comparison script was run with `MPLCONFIGDIR=/private/tmp/pqt-mpl` and `XDG_CACHE_HOME=/private/tmp/pqt-cache` because the usual matplotlib/font caches were not writable. The train/validation and holdout metadata files in `results/backtests/` record SDK versions, default exchange rules, commands, code commit, and the frozen-selection SHA-256; the tuning command and cases are recorded above and in `results/optimization_protocol.md`.

**Tests performed:** Seven parameter cases × four training sessions = 28 tuning replays; six candidates × 20 training/validation sessions = 120 comparison replays; frozen microprice and unmodified official example × four holdout sessions = eight final replays. Every replay completed 5,400 batches. All 17 unit tests passed; Python compilation and `git diff --check` passed. An audit verified the frozen-selection and base-config hashes, 120+8 full-session raw records, zero recorded rejects, worst-case exposure within ±600, messages at most 20 per batch, and at most 40 resting orders per symbol. The final CLI strategy matched its instrumented research variant on s17.

**Actual results:** The best changed tuning case (minimum quote edge 2 cents) improved four-session mean net 60-second public-mid markout only 0.851% over the default, below the predeclared 5% threshold; defaults were retained. Mean results per session below are **historical proxies**, not the official hidden true-value score:

| Split | Strategy | SDK estimated PnL | Net 60 s future-mid markout | Conservative fill stress markout |
|---|---|---:|---:|---:|
| Training, s01–s16 | Official BNKY example | $263.50 | $242.43 | -$36.94 |
| Training, s01–s16 | Microprice | $378.19 | $390.57 | $193.91 |
| Training, s01–s16 | Combined model + ETF | $412.80 | $362.27 | $215.67 |
| Validation, s17–s20 | Official BNKY example | $356.58 | $477.36 | $92.77 |
| Validation, s17–s20 | Microprice | $382.48 | $413.33 | $224.04 |
| Validation, s17–s20 | Combined model + ETF | $422.89 | $387.84 | $236.57 |
| Frozen holdout, s21–s24 | Official BNKY example | -$48.87 | -$18.09 | -$195.71 |
| Frozen holdout, s21–s24 | **Microprice** | **$392.64** | **$418.43** | **$222.94** |

The microprice variant beat combined on 60-second validation markout in **4/4** sessions and the predictive-only variant in **3/4**. The unmodified official example's higher mean validation markout was driven by one large s18 result; it had four negative training markout sessions and much weaker stressed markout. The simpler microprice variant was selected under the predeclared consistency rule. Its holdout net markout was positive in **4/4** sessions, versus **2/4** for the official example. Its 5,954 holdout fills traded 53,876 shares; net fees were -$107.75 (maker rebates exceeded charges). Maximum holdout worst-case weighted exposure was 109.97 of 600, maximum batch messages 8 of 20, and rejected orders zero. The highest mean holdout PnL attribution was BRRY at $234.17/session; see the full security/session and maker/taker breakdowns in `results/final_evaluation.md` and CSVs. No strategy or parameter selection changed after holdout results were seen.

**Problems and limitations:** The official score uses hidden true value about 60 seconds after fills, with news adjustment; the available data supplies only public future midprice. SDK historical fills remain optimistic because our orders do not alter the market and other participants cannot pick off our displayed quotes. The sensitivity metric retains 75% of maker fills and charges one extra adverse cent per retained share, but it is postprocessing, not an inventory-aware replay. BRRY supplies much of the selected strategy's historical result, so live adverse selection and thin-book behavior are material unresolved risks. News, halts, reconnects, and real multi-leg partial fills are absent from historical data. The official starter trades only BNKY and begins at batch zero, while our four-symbol variants wait 241 batches; it is an external reference rather than a controlled four-symbol ablation. Stage 3 also used s17–s20 for model selection, so validation may be optimistic; the holdout was kept separate until the final choice was frozen.

**Next steps:** Stop after Stage 5. The selected file is `strategies/final_strategy.py`; it is ready for a separate sandbox connectivity and safety exercise if requested, but historical PnL is not evidence of the official competition score or live profitability.

## Stage 6 — Competition readiness and final verification

**Status: COMPLETE (local work); authenticated sandbox verification: BLOCKED.** The Stage 6 acceptance criterion explicitly permits live/sandbox connectivity to be marked BLOCKED when credentials or network are unavailable. No sandbox success is claimed.

**Acceptance criteria:** Local SDK v0.2.3 tests and one full-session final backtest pass; three preset configurations and a known-working fallback are available; risk, order lifecycle, reconnect, halt, empty-book, and exception paths are tested locally; monitoring and round-break procedure are documented; selected strategy and configuration are frozen. Authenticated sandbox market/order tests remain blocked because no key is available here and sandbox DNS did not resolve.

**Implementation tasks:** Added `strategies/competition_strategy.py` around the frozen Stage 5 `FinalStrategy`, with balanced/conservative/aggressive presets in `results/competition_presets.json`, one-minute monitoring, startup persistent-order cleanup, and a fail-closed exception latch. Added `research/verify_lifecycle.py`, `research/verify_defensive_market.py`, `research/preset_replay_once.py`, `research/verify_presets.py`, `research/verify_competition_freeze.py`, and four Stage 6 unit tests in `research/test_competition_strategy.py`. Saved exact file hashes and SDK versions in `results/competition_freeze.json`, plus replay records under `results/readiness/`. Created `COMPETITION_CHECKLIST.md` and `results/final_summary.md`; updated `STRATEGY.md`. The Stage 5 selection, base configuration, official examples, and holdout results were not changed.

**Commands executed:**

```bash
.venv/bin/pqt run --help
.venv/bin/pqt check --help
.venv/bin/pqt backtest --help
.venv/bin/python -m pip show pqt-client pqt-protocol
.venv/bin/python research/verify_lifecycle.py
.venv/bin/python research/verify_defensive_market.py
.venv/bin/python research/verify_presets.py --workers 3
PQT_PRESET=conservative .venv/bin/pqt backtest strategies/competition_strategy.py --data data --name s17
PQT_PRESET=balanced .venv/bin/pqt backtest strategies/competition_strategy.py --data data --name s17
PQT_PRESET=aggressive .venv/bin/pqt backtest strategies/competition_strategy.py --data data --name s17
.venv/bin/python -m unittest discover -s research -p 'test_*.py' -q
.venv/bin/python -m py_compile strategies/competition_strategy.py research/verify_lifecycle.py research/verify_defensive_market.py research/verify_presets.py research/preset_replay_once.py
.venv/bin/pqt check --server https://sandbox.princeton-quant.com --protocol 2
curl -I --max-time 8 https://sandbox.princeton-quant.com
PQT_PRESET=balanced .venv/bin/pqt backtest strategies/competition_strategy.py --data data --name s17 > results/readiness/frozen_balanced_s17.log 2>&1
.venv/bin/python research/verify_competition_freeze.py
git diff --check
```

The three preset CLI s17 logs are under `results/readiness/presets/`; the final balanced log is `results/readiness/frozen_balanced_s17.log`. SDK versions were `pqt-client` 0.2.3 and `pqt-protocol` 0.2.3. Credentials were checked only for **presence**, never printed or stored; `PQT_API_KEY`, `PQT_SERVER`, and `PQT_SANDBOX_URL` were absent.

**Tests performed and actual results:** All 21 research tests passed. The synthetic SDK lifecycle replay observed two ACKs, one replace, one fill, and one cancel with the expected position/open-order/worst-case exposure transitions and zero rejects. The 250-batch defense replay cancelled HPPO quotes during a halt and cleared BRRY quotes after an empty book side; zero rejects. Startup with pre-existing orders and reconnect both enter cancellation/re-warm mode; unexpected test-mode exceptions latch off trading and retry cancellation. Twelve full-session preset replays covered s17–s20 at 5,400 batches each; all had zero rejects, self-trades, exception latches, and recorded exchange-limit breaches. Max batch messages were 8/20, max aggregate open orders 8, and max worst-case weighted exposure 121.95/600. Balanced s17 exactly reproduced Stage 5 fills, shares, and estimated PnL. The frozen post-change balanced official CLI backtest completed 5,400 batches with $308.92 estimated PnL, 1,463 fills, 12,183 shares, zero rejects. This SDK PnL is not the official score.

| Preset | Mean validation SDK estimated PnL/session | Mean net public 60 s markout/session | Mean stressed markout/session | Mean fills/session |
|---|---:|---:|---:|---:|
| Conservative | $155.03 | $171.16 | $96.30 | 1,017 |
| **Balanced — frozen default** | **$382.48** | **$413.33** | **$228.56** | **1,545** |
| Aggressive diagnostic | $636.79 | $676.85 | $312.73 | 1,953 |

**Problems and limitations:** `pqt check` returned exit 2, “no API key”; sandbox DNS resolution also failed (curl exit 6). No authenticated connection, live order submission/ACK/fill/cancel/reconnect, or sandbox news/halt exercise was possible. Historical replay uses optimistic maker fills, no market response to our quotes, public mids rather than hidden true values, and no actual news or halts. Aggressive results may be overstated under live pickoff risk; it is not the selected preset. The selected balanced strategy remains the frozen Stage 5 validation choice, and s21–s24 were not used for Stage 6 tuning. `--cancel-on-disconnect` and GUI inspection are necessary because a disconnected process cannot itself confirm remote cleanup.

**Next steps:** Stop after Stage 6. When a team key and working network are available, follow `COMPETITION_CHECKLIST.md` to run authenticated `pqt check`, sandbox test mode, and GUI lifecycle/reconnect verification; record the actual observations. Then launch balanced on the organizer-provided live server, evaluate the official Round 1 report, and make only limited documented adjustments before Round 2.
