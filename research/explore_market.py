"""Describe every PQT session and render market microstructure plots.

Run: .venv/bin/python research/explore_market.py --data data --output results/eda
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "pqt-mpl-cache"))
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from data_loader import add_market_variables, aggregate_trade_flow, load_manifest, load_session

SYMBOL_COLORS = {"HPPO": "#3366cc", "BNKY": "#1b9e77", "CRRT": "#d95f02", "BRRY": "#7570b3"}


def describe(frame: pd.DataFrame) -> dict:
    mid = frame["mid_px"].dropna()
    return {
        "book_rows": len(frame),
        "two_sided_fraction": float(frame["mid_px"].notna().mean()),
        "first_mid_cents": float(mid.iloc[0]) if len(mid) else np.nan,
        "last_mid_cents": float(mid.iloc[-1]) if len(mid) else np.nan,
        "net_mid_change_cents": float(mid.iloc[-1] - mid.iloc[0]) if len(mid) else np.nan,
        "median_mid_cents": float(mid.median()) if len(mid) else np.nan,
        "median_microprice_cents": float(frame["microprice"].median()),
        "median_micro_minus_mid_cents": float((frame["microprice"] - frame["mid_px"]).median()),
        "median_spread_cents": float(frame["spread"].median()),
        "p90_spread_cents": float(frame["spread"].quantile(0.9)),
        "median_bid_depth_shares": float(frame["bid_qty"].median()),
        "median_ask_depth_shares": float(frame["ask_qty"].median()),
        "p10_bid_depth_shares": float(frame["bid_qty"].quantile(0.1)),
        "p10_ask_depth_shares": float(frame["ask_qty"].quantile(0.1)),
        "mean_imbalance": float(frame["imbalance"].mean()),
        "median_abs_imbalance": float(frame["imbalance"].abs().median()),
        "traded_shares": int(frame["trade_qty"].sum()),
        "trade_count": int(frame["trade_count"].sum()),
        "active_trade_batch_fraction": float(frame["trade_count"].gt(0).mean()),
        "signed_aggressive_shares": int(frame["signed_qty"].sum()),
        "median_rv10_cents": float(frame["rv10_cents"].median()),
        "p90_rv10_cents": float(frame["rv10_cents"].quantile(0.9)),
    }


def build_frame(top: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    market = add_market_variables(top)
    flow = aggregate_trade_flow(trades)
    counts = trades.groupby(["batch", "symbol"], sort=False).size().rename("trade_count").reset_index()
    market = market.merge(flow, on=["batch", "symbol"], how="left", validate="one_to_one")
    market = market.merge(counts, on=["batch", "symbol"], how="left", validate="one_to_one")
    for col in ("trade_qty", "signed_qty", "trade_count"):
        market[col] = market[col].fillna(0).astype("int64")
    market["interval_5min"] = market["batch"] // 600
    segments = []
    for _, segment in market.groupby("symbol", sort=False):
        segment = segment.sort_values("batch").copy()
        mid = segment["mid_px"].astype(float)
        # Rolling realized volatility in cents, using only the last 10 seconds.
        segment["rv10_cents"] = np.sqrt(mid.diff().pow(2).rolling(20, min_periods=10).sum())
        segments.append(segment)
    return pd.concat(segments, ignore_index=True)


def render_plots(output: Path, overall: pd.DataFrame, intervals: pd.DataFrame,
                 spreads: dict[str, list[np.ndarray]], sample: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(10, 5))
    bins = np.arange(0.5, 40.6, 1)
    for symbol, pieces in spreads.items():
        values = np.concatenate(pieces)
        ax.hist(values, bins=bins, density=True, histtype="step", linewidth=1.8,
                label=symbol, color=SYMBOL_COLORS[symbol])
    ax.set(xlabel="Best ask minus best bid (cents)", ylabel="Density",
           title="Spread distributions across all 24 sessions (display clipped at 40 cents)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(output / "spread_distributions.png", dpi=160)
    plt.close(fig)

    by_time = intervals.groupby(["symbol", "interval_5min"], as_index=False).agg(
        spread=("median_spread_cents", "median"),
        depth=("median_bid_depth_shares", "median"),
        volume=("traded_shares", "median"),
        trade_count=("trade_count", "median"),
        rv10=("median_rv10_cents", "median"),
        imbalance=("mean_imbalance", "median"),
        signed_flow=("signed_aggressive_shares", "median"),
    )
    panels = [
        ("spread", "Median spread (cents)"), ("depth", "Median best-bid depth (shares)"),
        ("volume", "Traded shares / 5 min"), ("trade_count", "Trades / 5 min"),
        ("rv10", "Median trailing 10 s volatility (cents)"),
        ("imbalance", "Mean book imbalance"),
    ]
    fig, axs = plt.subplots(3, 2, figsize=(13, 11), sharex=True)
    for ax, (column, label) in zip(axs.flat, panels):
        for symbol, part in by_time.groupby("symbol", sort=False):
            ax.plot(part["interval_5min"] * 5 + 2.5, part[column], marker="o", markersize=3,
                    label=symbol, color=SYMBOL_COLORS[symbol])
        ax.set_ylabel(label)
        ax.grid(alpha=0.25)
    axs[0, 0].legend(ncol=4, fontsize=8)
    for ax in axs[-1]:
        ax.set_xlabel("Minutes into session (5-minute interval midpoint)")
    fig.suptitle("Intraday market behavior: median across 24 sessions")
    fig.tight_layout()
    fig.savefig(output / "intraday_microstructure.png", dpi=160)
    plt.close(fig)

    fig, axs = plt.subplots(4, 1, figsize=(13, 11), sharex=True)
    for ax, (symbol, part) in zip(axs, sample.groupby("symbol", sort=False)):
        minutes = (part["batch"] + 1) / 120
        ax.plot(minutes, part["mid_px"].astype(float) / 100, linewidth=0.9,
                color=SYMBOL_COLORS[symbol], label="mid")
        ax.plot(minutes, part["microprice"].astype(float) / 100, linewidth=0.55,
                color="black", alpha=0.55, label="microprice")
        ax.set(ylabel=f"{symbol} ($)")
        ax.grid(alpha=0.2)
        ax.legend(loc="upper right", fontsize=8)
    axs[-1].set_xlabel("Minutes into s01")
    fig.suptitle("Example session s01: public midprice and microprice")
    fig.tight_layout()
    fig.savefig(output / "s01_mid_microprice.png", dpi=160)
    plt.close(fig)

    fig, axs = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    for symbol, part in by_time.groupby("symbol", sort=False):
        x = part["interval_5min"] * 5 + 2.5
        axs[0].plot(x, part["imbalance"], marker="o", label=symbol, color=SYMBOL_COLORS[symbol])
        axs[1].plot(x, part["signed_flow"], marker="o", label=symbol, color=SYMBOL_COLORS[symbol])
    axs[0].set_ylabel("Mean book imbalance")
    axs[1].set_ylabel("Median signed aggressive shares / 5 min")
    axs[1].set_xlabel("Minutes into session")
    for ax in axs:
        ax.axhline(0, color="gray", linewidth=0.6)
        ax.grid(alpha=0.2)
    axs[0].legend(ncol=4, fontsize=8)
    fig.suptitle("Order-book pressure and aggressive flow across the session")
    fig.tight_layout()
    fig.savefig(output / "intraday_pressure.png", dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("results/eda"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(args.data)
    symbols = [entry["symbol"] for entry in manifest["symbols"]]
    spreads = {symbol: [] for symbol in symbols}
    session_rows, interval_rows = [], []
    sample = None
    for session in manifest["sessions"]:
        top, trades = load_session(args.data, session)
        frame = build_frame(top, trades)
        if session == "s01":
            sample = frame.copy()
        for symbol, part in frame.groupby("symbol", sort=False):
            session_rows.append({"session": session, "symbol": symbol, **describe(part)})
            spreads[symbol].append(part["spread"].dropna().to_numpy(dtype=float))
            for interval, within in part.groupby("interval_5min", sort=True):
                interval_rows.append({"session": session, "symbol": symbol,
                                      "interval_5min": int(interval),
                                      "start_minute": int(interval) * 5,
                                      **describe(within)})
    assert sample is not None
    sessions = pd.DataFrame(session_rows)
    intervals = pd.DataFrame(interval_rows)
    assert len(sessions) == 24 * 4 and len(intervals) == 24 * 4 * 9
    sessions.to_csv(args.output / "session_metrics.csv", index=False, float_format="%.6f")
    intervals.to_csv(args.output / "interval_metrics.csv", index=False, float_format="%.6f")
    overall = sessions.groupby("symbol", as_index=False).median(numeric_only=True)
    overall.to_csv(args.output / "overall_metrics.csv", index=False, float_format="%.6f")
    render_plots(args.output, overall, intervals, spreads, sample)
    print(f"Wrote {len(sessions)} session rows, {len(intervals)} interval rows, and 4 plots to {args.output}")


if __name__ == "__main__":
    main()
