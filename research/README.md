# PQT research and baseline runs

Run these commands from the repository root using the installed Python 3.12 environment:

```bash
source .venv/bin/activate
python research/validate_data.py --data data --output results/baseline/data_validation.json
python -m unittest discover -s research -p 'test_*.py' -v
python research/run_baselines.py
```

`validate_data.py` reads **all 24 sessions**, checks the manifest and CSV schemas, checks batch and symbol order, validates book prices, quantities, missing sides, tick sizes, trade directions, and trade fields, then writes a JSON audit. It raises an error and exits nonzero if a check fails.

`run_baselines.py` runs the **unmodified official** `hello_world.py` on `s01` and `market_maker.py` on `s01`, `s08`, and `s16`. It saves full stdout/stderr logs plus `results/baseline/summary.json`, including the exact CLI commands, SDK versions, Git commit, exit codes, fills, rejects, fees, and estimated PnL. To run different market-maker sessions, use `--market-sessions s02 s09 s15`. These are offline runs; no API key is needed.

## Data loader

`data_loader.py` provides `load_manifest(data_dir)`, `load_session(data_dir, session)`, `add_market_variables(top)`, `aggregate_trade_flow(trades)`, and `batch_end_seconds(batch, batch_ms)`.

```python
from research.data_loader import (
    add_market_variables, aggregate_trade_flow, load_manifest, load_session,
)

manifest = load_manifest("data")
top, trades = load_session("data", "s01")
market = add_market_variables(top)
flow = aggregate_trade_flow(trades)
```

Top-of-book prices and trade prices are **integer cents**; quantities are shares. `mid_px`, `spread`, and `microprice` are in cents. `imbalance` is unitless. Signed flow is positive for aggressive `BUY` orders and negative for aggressive `SELL` orders. An absent bid or ask stays missing (`pd.NA`), so market variables requiring both sides are also missing.

There are no wall-clock timestamps. Batch `0` is the first **500 ms** interval; its end is at 0.5 seconds. Batch `b` ends at `(b + 1) * batch_ms / 1000` elapsed seconds. Top rows are snapshots published at the **end** of their batch, and trade rows are the executions during that batch. A strategy sees batch `b` after it ends; an order it sends then is processed in batch `b+1`. Do not use future batches as live features.

## Exchange rules confirmed from the local strategy guide

Orders in a batch are processed by participant order, then message order, and crossing fills occur at the **resting** order's price. An unfilled regular limit order rests; IOC cancels its remainder. The SDK reports acknowledgments, fills, rejects, cancels, and replacements before the next `on_batch` callback. A strategy must also handle empty book sides and halts, although the historical data has no halts.

| Symbol | Position limit, shares | Largest order, shares | Exposure weight |
|---|---:|---:|---:|
| HPPO | ±1,000 | 200 | +1.000 |
| BNKY | ±2,500 | 500 | +0.150 |
| CRRT | ±2,500 | 500 | -0.290 |
| BRRY | ±600 | 120 | +0.425 |

The team-wide weighted exposure limit is ±600 HPPO-equivalent shares, checked against worst-case open-order fills. There are at most 40 resting orders per symbol and 20 trading messages per batch; order-to-trade ratio above 50:1 is flagged for review. Cash, fees, rebates, penalties, and PnL are measured by the SDK in **mills**, one-tenth of a cent. Per-share charges are $0.004 for a taker fill, a $0.002 rebate for a maker fill, $0.020 for HPPO conversion, and a $0.10 penalty for a self-trade, in addition to its fees.

## What the baseline means

The market maker quotes only BNKY with the starter defaults: 2-cent half-spread, 50 shares per quote, and 3-cent inventory skew at a 500-share position. The saved estimated PnL is **not the official competition score**. The score marks each fill to a hidden true value approximately 60 seconds later, removes later news jumps, includes fees, and combines the two competition rounds as `R1 + 2 × R2`. The history contains no hidden true values, news, or halts.

The v2 SDK backtest uses the recorded top of book without our orders changing the market. It cannot model other participants reacting to our quotes, adverse queue selection, or another participant taking the touch before an IOC order. Its `--runs-dir` and dashboard options apply only to v1 strategies; this script captures v2 terminal output directly. Positive estimated PnL is therefore a software baseline, not evidence of live or scored profitability.

The earlier exploratory script, `stage1_explore.py`, and its findings are supplemental research. It uses `s01`–`s16` for discovery and leaves `s17`–`s24` reserved. It is not part of the setup baseline or a validated strategy.

## Stage 2 market analysis

```bash
python research/explore_market.py --data data --output results/eda
python research/analyze_signals.py --data data --results results
```

The first command writes all-session descriptive CSVs and plots. The second writes `results/signal_analysis.csv`, `results/eda/signal_session_metrics.csv`, delayed timing checks, a 60-second ranking, and plots. See `results/signal_report.md` for methods and numerical findings. Predictive calculations use `s01`–`s16` for training and `s17`–`s20` for validation; `s21`–`s24` are reserved for final strategy evaluation. Descriptive plots use all 24 sessions.

## Stage 3 fair-value models

```bash
python research/train_models.py --data data --output models/fair_value_v1.json
python research/evaluate_models.py --data data --model models/fair_value_v1.json --results results --selected-output models/fair_value_v1_selected.json
python research/benchmark_inference.py --repeats 200
python -m unittest discover -s research -p 'test_*.py' -v
```

The first command fits transforms and coefficients using only `s01`–`s16`, with session-grouped cross-validation for ridge strength. The second compares identical eligible periods in `s17`–`s20`, writes `results/model_comparison.csv` and diagnostics, and records the selected methods in a separate JSON bundle. `results/model_report.md` explains the evaluation and model choice. The untouched final-test sessions remain `s21`–`s24`.

The pure-Python inference interface is `models/inference.py`:

```python
from models.inference import FairValuePredictor

predictor = FairValuePredictor("models/fair_value_v1_selected.json")
prediction = predictor.predict(history, "HPPO", 60)
```

`history` contains at least 241 consecutive observed batch records; each record has its integer `batch`, a `top` mapping for the four securities, and that batch's public `trades`. A record format example is in the inference module docstring. The response includes predicted future midprice and return, horizon, model ID, and a training-residual uncertainty scale. It uses no future observations.

## Stage 5 strategy comparison

`run_backtests.py` uses the installed v0.2.3 `backtest_strategy` with default rules and records SDK fills, conversions, rejects, order messages, positions, and worst-case limit snapshots. It replays the official BNKY market maker unchanged and five controlled variants of our four-symbol strategy. `results/optimization_protocol.md` fixes the small search and selection rule before validation comparison.

```bash
.venv/bin/python research/run_backtests.py --phase tune --workers 4
# Select a training-only parameter case in results/tuning_choice.json.
.venv/bin/python research/run_backtests.py --phase trainval --workers 4
.venv/bin/python research/compare_strategies.py
# Freeze results/strategy_selected.json before opening the holdout.
.venv/bin/python research/run_backtests.py --phase holdout --workers 4
.venv/bin/python research/compare_strategies.py --final
```

`--smoke-session s02` permits a single **training** session for a pipeline check and writes under `results/backtests/smoke/`. It cannot open holdout data. Full outputs are `results/backtest_comparison.csv`, raw summaries and plots in `results/backtests/`, and `results/final_evaluation.md`. Estimated PnL and public 60-second midprice markouts are separate proxies, not the hidden true-value competition score.
