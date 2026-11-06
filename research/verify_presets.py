"""Replay three frozen candidate presets on s17–s20 without opening holdout data."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRESETS = ("conservative", "balanced", "aggressive")
SESSIONS = tuple(f"s{i:02d}" for i in range(17, 21))


def one(preset: str, session: str) -> dict:
    env = os.environ.copy()
    env["PQT_PRESET"] = preset
    command = [str(ROOT / ".venv/bin/python"), "research/preset_replay_once.py",
               "--session", session]
    run = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    if run.returncode:
        raise RuntimeError(f"{preset} {session} failed: {run.stderr[-1800:]}")
    record = json.loads(run.stdout)
    if (record["batches"] != 5400 or record["reject_count"] or record["self_trades"]
            or record["failure_latched"] or record["max_abs_worst_exposure"] > 600
            or record["max_messages_batch"] > 20
            or max(record["max_open_per_symbol"].values()) > 40):
        raise AssertionError(f"{preset} {session}: safety gate failed: {record}")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    outdir = ROOT / "results" / "readiness" / "presets"
    outdir.mkdir(parents=True, exist_ok=True)
    jobs = [(p, s) for p in PRESETS for s in SESSIONS]
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(one, p, s): (p, s) for p, s in jobs}
        for future in as_completed(futures):
            record = future.result()
            p, s = futures[future]
            (outdir / f"{p}_{s}.json").write_text(
                json.dumps(record, indent=2) + "\n", encoding="utf-8")
            rows.append(record)
            print(f"{p} {s}: PnL=${record['estimated_pnl_usd']:.2f} "
                  f"markout=${record['net_markout_60_usd']:.2f} "
                  f"fills={record['fills']} rejects={record['reject_count']}", flush=True)
    rows.sort(key=lambda r: (r["preset"], r["session"]))
    fields = ["preset", "session", "estimated_pnl_usd", "net_markout_60_usd",
              "stress_markout_usd", "fills", "shares", "net_fees_usd", "messages",
              "order_to_trade_ratio", "max_abs_worst_exposure", "max_messages_batch",
              "max_open_orders", "reject_count", "self_trades", "failure_latched"]
    with (outdir.parent / "preset_validation.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{key: record[key] for key in fields} for record in rows])
    balanced = next(r for r in rows if r["preset"] == "balanced" and r["session"] == "s17")
    original = json.loads((ROOT / "results" / "backtests" / "raw" / "trainval"
                           / "trainval_microprice_s17.json").read_text(encoding="utf-8"))
    assert (balanced["estimated_pnl_usd"], balanced["fills"], balanced["shares"]) == (
        original["estimated_pnl_usd"], original["fills"], original["shares"])
    print("PASS: 12 full-session preset replays; balanced reproduces Stage 5 s17 exactly")


if __name__ == "__main__":
    main()
