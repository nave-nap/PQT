"""Check the Stage 6 code/configuration hashes and installed SDK versions."""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import version
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    freeze = json.loads((ROOT / "results/competition_freeze.json").read_text())
    for relative, expected in freeze["file_sha256"].items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"FAIL: {relative} differs from the frozen version")
    for package, expected in freeze["sdk"].items():
        actual = version(package)
        if actual != expected:
            raise SystemExit(f"FAIL: {package} is {actual}; expected {expected}")
    print(f"PASS: {len(freeze['file_sha256'])} frozen files and SDK versions match")
    print(f"Selected preset: {freeze['selected_preset']}")


if __name__ == "__main__":
    main()
