# PQT competition checklist — frozen Stage 6 build

**Default:** `strategies/competition_strategy.py`, `PQT_PRESET=balanced`, SDK `pqt-client` and `pqt-protocol` 0.2.3. **Fallback:** `strategies/final_strategy.py` with the same Stage 5 microprice logic. The live server address must come from the organizers. The historical backtest is a public-price proxy and cannot calculate the official hidden true-value score.

## Verification status (November 5, 2026)

| Check | Status | Evidence / action |
|---|---|---|
| Local imports, CLI and 21 research tests | PASS | `.venv/bin/python -m unittest discover -s research -p 'test_*.py' -q` |
| Three presets on four full validation sessions each | PASS | `results/readiness/preset_validation.csv`; 12 × 5,400 batches, zero rejects or limit breaches |
| Frozen selected strategy, full-session official CLI replay | PASS | `results/readiness/frozen_balanced_s17.log`; 5,400 batches, 1,463 fills, zero rejects |
| Synthetic SDK ACK, replace, fill, cancel and exposure tracking | PASS | `results/readiness/lifecycle.json` |
| Synthetic halt, empty-side, reconnect, startup cleanup and exception tests | PASS | `results/readiness/defensive_market.json` and unit tests |
| Authenticated `pqt check` and sandbox connection | **BLOCKED** | `PQT_API_KEY` and `PQT_SERVER` absent here; `pqt check` exits 2 with “no API key”; sandbox DNS did not resolve from this environment |
| Real sandbox market, orders, fills, reconnect, halt and news | **BLOCKED** | Requires a working team key and network; do not infer success from local replay |

## Before each competition round

1. In a terminal at the repository root, check the frozen files and run the local suite:

   ```bash
   cd /Users/evanpan/PQT/pqt-student-kit
   .venv/bin/python research/verify_competition_freeze.py
   .venv/bin/python -m unittest discover -s research -p 'test_*.py' -q
   PQT_PRESET=balanced .venv/bin/pqt backtest strategies/competition_strategy.py --data data --name s17
   ```

2. Confirm exactly one algorithm process/key is active. Inspect the trader GUI for existing positions, open orders, limit usage, kill-switch state, and current session status. Other team members share the same account and limits. Ensure the correct server address and computer clock.
3. Keep the key out of source, `.env` files shared with others, command history, and screenshots. Enter it silently in the terminal. `.gitignore` excludes `.env`, but the supported procedure uses only an environment variable.
4. If the sandbox is reachable, perform the authenticated test below **before** live trading. Observe the GUI for ACKs, fills, cancels, positions, and any rejects; deliberately stop/restart once to validate order cleanup. Record actual observations. Local synthetic tests do not replace this check.

## Sandbox test: start, stop, restart

These commands match `pqt run --help` and `pqt check --help` from SDK v0.2.3. Use a team **algorithm key**, not a GUI login code. The key prompt does not echo the secret.

```bash
cd /Users/evanpan/PQT/pqt-student-kit
printf 'Team algorithm key: '
read -rs PQT_API_KEY
printf '\n'
export PQT_API_KEY
export PQT_SERVER=https://sandbox.princeton-quant.com
.venv/bin/pqt check --server "$PQT_SERVER" --protocol 2
PQT_PRESET=balanced .venv/bin/pqt run strategies/competition_strategy.py --mode test --server "$PQT_SERVER" --cancel-on-disconnect
```

Leave the terminal running and use `https://sandbox.princeton-quant.com/trade/` to inspect the actual account. If `pqt check` fails, resolve that first. The sandbox has the official API and limits but placeholder prices/participants, so it is for execution and safety verification, not performance calibration.

To stop, press **Ctrl-C once** in the running terminal. `--cancel-on-disconnect` requests exchange cancellation of that key's resting orders; verify the GUI shows no remaining orders before starting another process. Cancel any stragglers in the GUI. Keep watching for late fills and updated positions. Do not launch two copies with the same key: the newest connection supersedes the old one. A transient network drop is handled by SDK automatic reconnect; allow it to recover and inspect orders/positions before manually restarting. If the process exits, run the same `PQT_PRESET=balanced .venv/bin/pqt run ... --mode test ... --cancel-on-disconnect` command again from the prepared terminal. Startup reconciliation cancels orders in its SDK snapshot before new quoting; the strategy then waits 241 fresh 500 ms batches (about 120.5 seconds).

## Competition live launch

Use the organizer-provided **live** URL and the team key intended for that server. Do not assume the sandbox URL is the competition server. Run only after the official session is ready and the GUI shows the expected account.

```bash
cd /Users/evanpan/PQT/pqt-student-kit
export PQT_SERVER='REPLACE_WITH_ORGANIZER_LIVE_URL'
.venv/bin/python research/verify_competition_freeze.py
.venv/bin/pqt check --server "$PQT_SERVER" --protocol 2
PQT_PRESET=balanced .venv/bin/pqt run strategies/competition_strategy.py --mode live --server "$PQT_SERVER" --cancel-on-disconnect
```

If this is a new terminal, set `PQT_API_KEY` silently as shown in the sandbox block first. Ctrl-C, GUI order verification/cancellation, and restart follow the same procedure as test mode. For a smaller-risk response after poor execution, start the tested conservative preset with `PQT_PRESET=conservative` **between rounds**. The higher-activity aggressive preset is documented and tested but is not the default. If the Stage 6 wrapper itself fails and the frozen Stage 5 logic is needed, stop it, clear/verify resting orders in the GUI, and run:

```bash
.venv/bin/pqt run strategies/final_strategy.py --mode live --server "$PQT_SERVER" --cancel-on-disconnect
```

This fallback lacks the Stage 6 monitoring and exception latch. If errors persist, stop trading and investigate the account rather than repeatedly restarting into an unknown state. The SDK has no `pqt cancel` CLI command; use the trader GUI for manual order cleanup.

## What to watch during a session

The strategy logs one `monitor` line per minute. Check positions and worst-case exposure, open orders, `msgs_left`, observed microprices, fills/shares/fees in the last minute, rejects, estimated PnL, cooldown and failure latch. Read WARN/ERROR lines immediately. The exchange GUI is authoritative for account positions, orders and limit usage; `pnl_est` is an SDK mark and not the official score. Trigger a defensive response when rejects recur, a symbol accumulates inventory near its cap, orders stay open unexpectedly, messages run low, a book is empty or halted, news hits a quoted symbol, or the failure latch appears. If the latch is set, verify that orders were actually cancelled in the GUI; an offline client cannot guarantee remote cleanup.

The SDK controls are `half_width` (1–30), `quote_size` (1–100), `inventory_skew` (0–25), `min_quote_edge` (0–20), and `news_cooldown_s` (0–300). Adjust only a few at a time, within tested preset values where possible. `prediction_threshold`, `arb_margin`, and `arb_enabled` are inert in the selected microprice build. Static position caps, quote caps, buffer, and requote distance are in the preset file and need a process restart to change. Keep the frozen files unchanged during competition unless starting an explicitly new, tested development cycle.

## Round 1 → Round 2, which counts twice

1. Save the official Round 1 report and session log. Read **official score and 1/10/30/60-second markout by security**, not SDK PnL alone. Compare fills/trade frequency, maker versus taker, adverse markouts after fills, position buildup, worst-case exposure, rejects, self-trades, disconnects, and stale orders. Compare observed spreads, depth and volatility with `results/eda/` and Stage 5 session results.
2. If any risk or execution failure occurred, cancel/verify orders, diagnose it, and use the conservative preset or the known-working fallback. Do not enlarge risk while the cause is unknown.
3. If execution is sound but negative markouts cluster in one security or volatile regime, widen `half_width` toward 4, reduce `quote_size` toward 8, raise `min_quote_edge` toward 2, increase `inventory_skew` toward 6, or use the conservative preset between rounds. The static per-symbol caps change only with preset restart. Confirm the GUI accepted parameter changes; its control token belongs to one team member at a time.
4. If Round 1 score and markouts are favorable across securities, keep balanced. Consider aggressive only if the observed book is liquid, adverse selection and inventory remain controlled, and the extra historical order activity is acceptable. Its larger backtest result alone is insufficient.
5. Record the chosen preset/parameter values, reason, and time before Round 2. Avoid new features, a model change, changed parameter schema, or an untested architectural rewrite during the break. After any restart, allow cleanup and the 241-batch warmup before assessing quote activity.

## Final sign-off

- [x] SDK v0.2.3, frozen file hashes, research tests, official full-session backtest, and three presets verified locally.
- [x] Frozen default is **balanced**; fallback is `strategies/final_strategy.py`.
- [x] Risk, halt, empty-book, news, reconnect and unexpected-exception paths have local tests.
- [x] Start/stop/restart and GUI cleanup procedure documented.
- [ ] **Blocked:** authenticated sandbox check and real order-lifecycle/reconnect exercise. Complete with team key and working network; log the observed result.
- [ ] Before live start: confirm organizer URL, active session, team account, key, GUI orders/positions, and one active process.
