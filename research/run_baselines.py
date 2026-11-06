"""Run unmodified official v2 examples and save their full logs and summaries.

Run: .venv/bin/python research/run_baselines.py
"""

from __future__ import annotations

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys


def parse_summary(log: str) -> dict:
    patterns = {
        "header": r"(?m)^(\w+) on (s\d{2}): (\d+) batches \((\d+) min\)$",
        "money": r"(?m)^  estimated PnL \$(-?\d+\.\d{2}) .*?, realized \$(-?\d+\.\d{2}), fees \$(-?\d+\.\d{2})$",
        "fills": r"(?m)^  fills (\d+) \(([\d,]+) shares\), rejects: (.+)$",
        "positions": r"(?m)^  final positions (.+)$",
    }
    matches = {key: re.search(pattern, log) for key, pattern in patterns.items()}
    if any(match is None for match in matches.values()):
        raise ValueError("SDK summary format changed; inspect the saved log")
    header, money, fills, positions = (matches[key] for key in patterns)
    assert header and money and fills and positions
    return {
        "strategy_class": header.group(1), "session": header.group(2),
        "batches": int(header.group(3)), "minutes": int(header.group(4)),
        "estimated_pnl_usd": money.group(1), "realized_pnl_usd": money.group(2),
        "net_fees_usd": money.group(3),
        "fills": int(fills.group(1)), "filled_shares": int(fills.group(2).replace(",", "")),
        "rejects": fills.group(3), "final_positions": positions.group(1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-sessions", nargs="+", default=["s01", "s08", "s16"])
    parser.add_argument("--output", type=Path, default=Path("results/baseline"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = (root / args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    jobs = [("hello_world", "s01")] + [("market_maker", s) for s in args.market_sessions]
    records = []
    for strategy, session in jobs:
        command = [".venv/bin/pqt", "backtest", f"strategies/{strategy}.py",
                   "--data", "data", "--name", session]
        run = subprocess.run(command, cwd=root, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, check=False)
        log_path = output / f"{strategy}_{session}.log"
        log_path.write_text(run.stdout, encoding="utf-8")
        record = {"command": shlex.join(command), "exit_code": run.returncode,
                  "log": str(log_path.relative_to(root))}
        if run.returncode == 0:
            record.update(parse_summary(run.stdout))
        records.append(record)
        print(f"{strategy} {session}: exit {run.returncode}; log {log_path.relative_to(root)}")
    metadata = {
        "python_version": sys.version.split()[0],
        "pqt_client_version": version("pqt-client"),
        "pqt_protocol_version": version("pqt-protocol"),
        "repository_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "official_score_available": False,
        "runs": records,
    }
    (output / "summary.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    if any(record["exit_code"] != 0 for record in records):
        raise SystemExit("One or more baseline runs failed; see saved logs")
    print(f"Wrote {output.relative_to(root) / 'summary.json'}")


if __name__ == "__main__":
    main()
