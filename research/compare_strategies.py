"""Summarize PQT Stage 5 replays without changing the frozen selection.

Usage: .venv/bin/python research/compare_strategies.py [--final]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
BACKTESTS = RESULTS / "backtests"
ORDER = ["official", "microprice", "cross_asset", "predictive",
         "predictive_basket", "combined"]


def table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |",
                      "|" + "|".join(["---"] * len(headers)) + "|"]
                     + ["| " + " | ".join(map(str, row)) + " |" for row in rows])


def fmt(value, digits=2):
    return f"{value:,.{digits}f}"


def split_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (split, variant), group in df.groupby(["split", "variant"], sort=False):
        rows.append({
            "split": split, "variant": variant, "sessions": len(group),
            "mean_pnl_usd": group.estimated_pnl_usd.mean(),
            "median_pnl_usd": group.estimated_pnl_usd.median(),
            "min_pnl_usd": group.estimated_pnl_usd.min(),
            "mean_markout_usd": group.net_markout_60_usd.mean(),
            "markout_cents_per_share": 100 * group.net_markout_60_usd.sum()
            / max(1, group.shares.sum()),
            "min_markout_usd": group.net_markout_60_usd.min(),
            "positive_markout_sessions": int((group.net_markout_60_usd > 0).sum()),
            "mean_stress_markout_usd": group.stress_markout_usd.mean(),
            "total_fills": int(group.fills.sum()),
            "total_shares": int(group.shares.sum()),
            "total_fees_usd": group.net_fees_usd.sum(),
            "maker_fill_pct": 100 * group.maker_fills.sum() / max(1, group.fills.sum()),
            "mean_spread_capture_cents": (
                (group.avg_spread_capture_cents * group.maker_shares).sum()
                / max(1, group.maker_shares.sum())),
            "max_worst_exposure": group.max_abs_worst_exposure.max(),
            "total_rejects": int(group.reject_count.sum()),
            "total_messages": int(group.messages.sum()),
            "message_to_fill_ratio": group.messages.sum() / max(1, group.fills.sum()),
        })
    summary = pd.DataFrame(rows)
    summary["variant_order"] = summary.variant.map({v: i for i, v in enumerate(ORDER)})
    return summary.sort_values(["split", "variant_order"]).drop(columns="variant_order")


def plots(df: pd.DataFrame, symbol_df: pd.DataFrame) -> None:
    for metric, title, filename in (
        ("estimated_pnl_usd", "Historical estimated PnL after fees", "session_pnl.png"),
        ("net_markout_60_usd", "Net 60-second future-mid markout proxy", "session_markout.png"),
    ):
        fig, ax = plt.subplots(figsize=(12, 5))
        for variant in ORDER:
            group = df[df.variant == variant].sort_values("session")
            ax.plot(range(1, len(group) + 1), group[metric], marker="o", ms=3,
                    linewidth=1.4, label=variant)
        ax.axvline(16.5, color="gray", linestyle="--", linewidth=1)
        ax.axhline(0, color="black", linewidth=0.7)
        ax.set_xticks(range(1, 21), [f"s{i:02d}" for i in range(1, 21)], rotation=45)
        ax.set_ylabel("USD per session")
        ax.set_title(title + " · s01–s16 train, s17–s20 validation")
        ax.legend(ncol=3, fontsize=8)
        fig.tight_layout()
        fig.savefig(BACKTESTS / filename, dpi=160)
        plt.close(fig)
    validation = symbol_df[symbol_df.split == "validation"]
    pivot = validation.groupby(["variant", "symbol"]).estimated_pnl_usd.mean().unstack()
    pivot = pivot.reindex(ORDER).fillna(0)
    fig, ax = plt.subplots(figsize=(11, 5))
    pivot.plot.bar(ax=ax, width=0.8)
    ax.set_ylabel("Mean estimated PnL, USD/session")
    ax.set_title("Validation attribution by security; conversion transfer at basket mid")
    ax.tick_params(axis="x", rotation=25)
    ax.axhline(0, color="black", linewidth=0.7)
    fig.tight_layout()
    fig.savefig(BACKTESTS / "symbol_pnl_validation.png", dpi=160)
    plt.close(fig)


def tuning_text() -> str:
    path = BACKTESTS / "tuning.csv"
    if not path.exists():
        return "Tuning results are unavailable."
    df = pd.read_csv(path)
    rows = []
    for case, group in df.groupby("phase", sort=False):
        rows.append([case, len(group), fmt(group.estimated_pnl_usd.mean()),
                     fmt(group.net_markout_60_usd.mean()),
                     fmt(group.stress_markout_usd.mean()),
                     int((group.net_markout_60_usd > 0).sum()),
                     int(group.reject_count.sum())])
    rows.sort(key=lambda row: row[0])
    return table(["Case", "Train sessions", "Mean PnL $", "Mean 60s markout $",
                  "Mean stress $", "Positive markout", "Rejects"], rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()
    df = pd.read_csv(BACKTESTS / "trainval.csv")
    symbol_df = pd.read_csv(BACKTESTS / "trainval_symbol.csv")
    summary = split_summary(df)
    summary.to_csv(BACKTESTS / "summary.csv", index=False)
    plots(df, symbol_df)
    selected_path = RESULTS / "strategy_selected.json"
    selected = json.loads(selected_path.read_text(encoding="utf-8")) if selected_path.exists() else None
    validation_symbols = (symbol_df[symbol_df.split == "validation"]
                          .groupby(["variant", "symbol"], as_index=False)
                          .agg(mean_pnl_usd=("estimated_pnl_usd", "mean"),
                               mean_markout_usd=("net_markout_60_usd", "mean"),
                               fills=("fills", "sum")))
    validation_symbols["variant_order"] = validation_symbols.variant.map(
        {v: i for i, v in enumerate(ORDER)})
    validation_symbols = validation_symbols.sort_values(["variant_order", "symbol"])
    risk = (df.groupby(["split", "variant"], as_index=False)
            .agg(max_hppo=("max_pos_HPPO", "max"), max_bnky=("max_pos_BNKY", "max"),
                 max_crrt=("max_pos_CRRT", "max"), max_brry=("max_pos_BRRY", "max"),
                 max_worst_exposure=("max_abs_worst_exposure", "max"),
                 max_batch_messages=("max_messages_batch", "max"),
                 max_open_hppo=("max_open_HPPO", "max"),
                 max_open_bnky=("max_open_BNKY", "max"),
                 max_open_crrt=("max_open_CRRT", "max"),
                 max_open_brry=("max_open_BRRY", "max"),
                 rejects=("reject_count", "sum")))
    lines = [
        "# Stage 5: historical backtest comparison",
        "",
        "The official backtester replays recorded top-of-book data with default SDK rules. "
        "Its estimated PnL and future-mid markouts **are not the competition's hidden true-value score**. "
        "Historical orders do not move the recorded market; maker fills and IOC legs are optimistic.",
        "",
        "## Protocol and candidates",
        "",
        "Training sessions are s01–s16; validation is s17–s20. The final test s21–s24 is "
        "opened only after selection is frozen. Every row replays all 5,400 batches under "
        "`pqt_client.v2.backtest_strategy` with default rules. The official example quotes BNKY "
        "only from batch zero, with its original size and parameters; our variants share a "
        "241-batch causal warmup. The official run is an external reference rather than "
        "a four-security controlled ablation. All our variants share the same quote, risk, "
        "warmup, and execution settings. `microprice` uses current microprice; `cross_asset` "
        "uses the saved one-feature cross-asset model; `predictive` uses the Stage 3 selected "
        "60-second model; `predictive_basket` adds the observable HPPO basket adjustment; "
        "`combined` additionally enables executable ETF conversion trading.",
        "",
        "## Training and validation",
        "",
        table(["Split", "Variant", "Mean PnL $", "Mean 60s markout $",
               "Markout c/share", "Worst markout $",
               "Positive sessions", "Mean stress $", "Fills", "Shares", "Rejects"],
              [[r.split, r.variant, fmt(r.mean_pnl_usd), fmt(r.mean_markout_usd),
                fmt(r.markout_cents_per_share), fmt(r.min_markout_usd),
                f"{r.positive_markout_sessions}/{r.sessions}",
                fmt(r.mean_stress_markout_usd), f"{r.total_fills:,}",
                f"{r.total_shares:,}", int(r.total_rejects)]
               for r in summary.itertuples()]),
        "",
        "![Session estimated PnL](backtests/session_pnl.png)",
        "",
        "![Session 60-second markout](backtests/session_markout.png)",
        "",
        "## Fees, execution, and risk",
        "",
        table(["Split", "Variant", "Fees $", "Maker fills %", "Spread captured c/share",
               "Max worst exposure", "Messages", "Msg/fill"],
              [[r.split, r.variant, fmt(r.total_fees_usd), fmt(r.maker_fill_pct, 1),
                fmt(r.mean_spread_capture_cents), fmt(r.max_worst_exposure),
                f"{r.total_messages:,}", fmt(r.message_to_fill_ratio)]
               for r in summary.itertuples()]),
        "",
        "Per-security fill, share, maker/taker, fee, markout, and estimated PnL attribution "
        "are in `backtests/trainval_symbol.csv`; per-session maxima and order counts are in "
        "`backtest_comparison.csv`. Conversion is allocated among securities at the observed "
        "basket mid, with its fee charged to HPPO. That attribution adds to SDK total PnL "
        "but is an accounting convention, not an official score breakdown. Spread captured "
        "is maker-fill signed distance from the public mid at the execution batch, weighted "
        "by maker shares; it is not a realized cash profit measure.",
        "",
        "![Validation PnL attribution by security](backtests/symbol_pnl_validation.png)",
        "",
        table(["Variant", "Symbol", "Mean validation PnL $", "Mean 60s markout $", "Fills"],
              [[r.variant, r.symbol, fmt(r.mean_pnl_usd), fmt(r.mean_markout_usd),
                f"{r.fills:,}"] for r in validation_symbols.itertuples()]),
        "",
        "## Limit audit",
        "",
        "Each row shows the maximum observed absolute position by symbol, maximum SDK "
        "worst-case weighted exposure, maximum messages in one batch, and maximum resting "
        "orders on any symbol. Exchange limits are ±1,000 HPPO, ±2,500 BNKY/CRRT, ±600 "
        "BRRY; weighted exposure ±600; 20 messages per batch; 40 resting orders per symbol.",
        "",
        table(["Split", "Variant", "HPPO", "BNKY", "CRRT", "BRRY",
               "Worst exposure", "Max msgs/batch", "Max open/symbol", "Rejects"],
              [[r.split, r.variant, int(r.max_hppo), int(r.max_bnky), int(r.max_crrt),
                int(r.max_brry), fmt(r.max_worst_exposure), int(r.max_batch_messages),
                int(max(r.max_open_hppo, r.max_open_bnky, r.max_open_crrt, r.max_open_brry)),
                int(r.rejects)] for r in risk.itertuples()]),
        "",
        "## Limited parameter search",
        "",
        "One factor at a time on training sessions s02, s06, s10, and s14: default; "
        "half-width 4 instead of 3; quote size 10 instead of 15; inventory skew 6 instead of 4; "
        "minimum quote edge 2 instead of 1 cent; volatility width weight 0.8 instead of 0.5; "
        "ETF margin 12 instead of 8 cents. No combinatorial sweep was run. "
        "News cooldown is held at 60 seconds because historical sessions contain no news. "
        "The stress metric deterministically retains 75% of maker fills, retains all taker "
        "fills, and charges one extra adverse cent per retained share. It is a deliberately "
        "pessimistic **future-mid markout sensitivity**, not a rerun of cash/inventory PnL.",
        "",
        tuning_text(),
        "",
    ]
    if selected:
        lines.extend(["## Frozen selection", "", f"Selected variant: **{selected['selected_variant']}**. "
                      f"Parameter case: **{selected['tuning_case']}**. "
                      f"{selected['selection_reason']}", "",
                      "`results/strategy_selected.json` records the exact overrides and was "
                      "marked frozen before any holdout replay.", ""])
        val = df[df.split == "validation"].pivot(
            index="session", columns="variant", values="net_markout_60_usd")
        chosen = selected["selected_variant"]
        paired = []
        for benchmark in ORDER:
            if benchmark == chosen:
                continue
            difference = val[chosen] - val[benchmark]
            paired.append([benchmark, fmt(difference.mean()),
                           f"{int((difference > 0).sum())}/4",
                           fmt(difference.min()), fmt(difference.max())])
        lines.extend(["Paired validation 60-second markout differences for the frozen choice "
                      "(selected minus comparison; four whole sessions):", "",
                      table(["Comparison", "Mean difference $", "Selected wins",
                             "Worst difference $", "Best difference $"], paired), ""])
    if args.final:
        if selected is None or not selected.get("frozen"):
            raise SystemExit("freeze selection before final report")
        holdout = pd.read_csv(BACKTESTS / "holdout.csv")
        holdout_symbols = pd.read_csv(BACKTESTS / "holdout_symbol.csv")
        hsum = split_summary(holdout)
        chosen_rows = holdout[holdout.variant == selected["selected_variant"]]
        chosen_symbols = (holdout_symbols[holdout_symbols.variant == selected["selected_variant"]]
                          .groupby("symbol", as_index=False)
                          .agg(mean_pnl_usd=("estimated_pnl_usd", "mean"),
                               mean_markout_usd=("net_markout_60_usd", "mean"),
                               fills=("fills", "sum"), shares=("shares", "sum")))
        lines.extend([
            "## Sealed final test: s21–s24", "",
            "The frozen selection was evaluated once on these sessions. Results did not alter "
            "the model, parameters, or selected variant.", "",
            table(["Variant", "Mean PnL $", "Mean 60s markout $", "Worst markout $",
                   "Mean stress $", "Fills", "Rejects"],
                  [[r.variant, fmt(r.mean_pnl_usd), fmt(r.mean_markout_usd),
                    fmt(r.min_markout_usd), fmt(r.mean_stress_markout_usd),
                    f"{r.total_fills:,}", int(r.total_rejects)] for r in hsum.itertuples()]),
            "",
            table(["Session", "Variant", "Estimated PnL $", "60s markout $",
                   "Stress markout $", "Fills", "Worst exposure"],
                  [[r.session, r.variant, fmt(r.estimated_pnl_usd),
                    fmt(r.net_markout_60_usd), fmt(r.stress_markout_usd),
                    int(r.fills), fmt(r.max_abs_worst_exposure)]
                   for r in holdout.sort_values(["session", "variant"]).itertuples()]),
            "",
            "Frozen strategy holdout results by security:", "",
            table(["Symbol", "Mean PnL $", "Mean 60s markout $", "Fills", "Shares"],
                  [[r.symbol, fmt(r.mean_pnl_usd), fmt(r.mean_markout_usd),
                    f"{r.fills:,}", f"{r.shares:,}"] for r in chosen_symbols.itertuples()]),
            "",
            "Frozen strategy holdout risk: maximum absolute positions "
            + ", ".join(f"{symbol} {int(chosen_rows[f'max_pos_{symbol}'].max())}"
                        for symbol in ("HPPO", "BNKY", "CRRT", "BRRY"))
            + f"; maximum worst-case weighted exposure {chosen_rows.max_abs_worst_exposure.max():.2f} "
            + f"of 600; maximum messages in one batch {int(chosen_rows.max_messages_batch.max())} "
            + f"of 20; total rejected orders {int(chosen_rows.reject_count.sum())}.",
            "",
        ])
    lines.extend([
        "## Limits of interpretation", "",
        "The official score marks each fill to hidden true value about 60 seconds later with "
        "news adjustment; neither that path nor live queue/adverse selection is in this data. "
        "The midprice markout is an observable proxy. Overlapping fills and sessions sharing "
        "a simulator distribution do not create independent trade-level evidence. The "
        "conservative fill test is a postprocessing sensitivity, not a live fill model. "
        "News, halts, and reconnects cannot be measured from these historical files.", "",
    ])
    (RESULTS / "final_evaluation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Wrote results/final_evaluation.md and results/backtests/{summary,plots}")


if __name__ == "__main__":
    main()
