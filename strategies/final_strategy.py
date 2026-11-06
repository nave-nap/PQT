"""Frozen Stage 5 selection for the PQT SDK. Run with `pqt backtest` or `pqt run`."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategies.our_strategy import OurStrategy  # noqa: E402

selection = json.loads((ROOT / "results" / "strategy_selected.json").read_text(encoding="utf-8"))
if not selection.get("frozen") or selection["selected_variant"] != "microprice":
    raise RuntimeError("this final strategy requires the frozen microprice selection")
config_path = ROOT / selection["base_config"]
if hashlib.sha256(config_path.read_bytes()).hexdigest() != selection["base_config_sha256"]:
    raise RuntimeError("frozen strategy configuration changed")
overrides = selection["overrides"]


class FinalStrategy(OurStrategy):
    FAIR_MODE = "microprice"
    ETF_ENABLED = False
    CONFIG_OVERRIDES = {"basket_fair_weight": 0.0, **overrides.get("config", {})}
    MODEL_PARAMS = tuple(replace(spec, default=overrides.get("params", {}).get(spec.name,
                                                                                spec.default))
                         for spec in OurStrategy.MODEL_PARAMS)
