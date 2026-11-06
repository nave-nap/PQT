# Live-safety candidate audit

Base: `ab85993` on `main`. Candidate: `live-safety-candidate`. Installed packages:
`pqt-client==0.2.3`, `pqt-protocol==0.2.3`. The selected microprice branch,
balanced quote parameters, and `ETF_ENABLED=False` are unchanged. The competition
freeze file is unchanged and still describes the base, not this candidate.

## Contract findings

- `STRATEGY_GUIDE.md` sections 2, 4, and 8 specify that news and exchange events
  precede `on_batch`, halts cancel resting orders, a restart cancels all orders with
  reason `SYSTEM`, and live strategy exceptions are logged before later callbacks
  continue. Disconnects can leave orders resting; use `--cancel-on-disconnect`.
- The installed v2 SDK `core.py` `_on_snapshot` marks news in a late-start or
  reconnect snapshot as told, so it does not call `on_news` for those items.
  `_on_batch` supplies the cumulative `news_stack`. The candidate reconciles that
  stack using each item's original batch and the existing `news_cooldown_s`.
- The SDK's `_guard` continues in live mode after callback exceptions. The
  candidate latches off new trading and tries `cancel_all` on unexpected startup,
  news, reconnect, fill, parameter, batch, monitoring, cancellation, and round-state
  failures. Backtests raise the original exception.
- After a halt ends, a `SYSTEM` cancellation, or a session resume, the candidate
  clears observed history and cancels resting orders for 120 batches (60 seconds).
  The existing 241 consecutive batch warmup then applies before quoting.

## Verification

`python -m unittest discover -s research -p 'test_*.py' -q`: 27 tests passed.
Synthetic tests cover late and reconnect news, stale news, callback failures,
startup cleanup, halt/resume/system cancellation, and monitoring. An 800-batch
synthetic session runs through the installed SDK backtester with a halt and
checks that quotes stop and only resume after the quiet period and warmup.

`python -m research.run_live_safety_backtests`: 20 full 5,400-batch replays, s01–s16
training and s17–s20 validation only. `sessions.csv` and `sessions.json` contain
every session's detailed results. All 20 match the pre-existing microprice rows
in `results/backtests/trainval.csv` exactly for estimated PnL, 60-second net
public-mid markout, fills, shares, rejects, messages, and maximum worst-case
weighted exposure.

| Split | Sessions | Mean estimated PnL | Mean net markout | Mean stressed markout | Fills | Messages | Rejects | Maximum worst-case exposure | Peak messages/batch |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Training | 16 | $378.1911875 | $390.5725625 | $193.908875 | 23,953 | 96,313 | 0 | 115.245 | 8 |
| Validation | 4 | $382.4775 | $413.33025 | $224.04125 | 6,181 | 25,829 | 0 | 104.06 | 8 |

## Limits

Historical files have no news or halts and cannot validate event frequency,
production reconnect behavior, remote cancel acknowledgments, or the exchange's
response to queued orders during a failure. Backtest fills do not model market
reaction or live queue position, and public-mid markout is not the hidden-value
competition score. No authenticated sandbox run was performed. The candidate
has not been promoted to the competition freeze or pushed.
