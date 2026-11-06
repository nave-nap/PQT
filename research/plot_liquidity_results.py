"""Plot session-paired strategy changes and 60-second signal correlations.

Run: .venv/bin/python research/plot_liquidity_results.py
"""

import os
from pathlib import Path
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "pqt-mpl-cache"))
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import t  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "liquidity_depletion"


def main():
    backtests = pd.read_csv(OUT / "strategy_comparison.csv")
    rows = []
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    colors = {"train": "#4679a8", "validation": "#e58a36"}
    for split, group in backtests.groupby("split"):
        pivot = group.pivot(index="session", columns="variant",
                            values="full_horizon_net_markout_usd")
        for variant in pivot.columns.drop("baseline"):
            difference = pivot[variant] - pivot.baseline
            n = len(difference)
            half = t.ppf(.975, n-1) * difference.std(ddof=1) / n**.5
            rows.append({"split": split, "variant": variant, "sessions": n,
                         "total_delta_usd": difference.sum(),
                         "mean_session_delta_usd": difference.mean(),
                         "ci95_low_usd": difference.mean()-half,
                         "ci95_high_usd": difference.mean()+half,
                         "positive_sessions": int((difference>0).sum())})
            if split == "validation":
                axes[0].scatter([variant]*n, difference, color=colors[split], alpha=.75)
                axes[0].plot([variant], [difference.mean()], marker="_", markersize=20,
                             color="black", linestyle="None")
    pd.DataFrame(rows).to_csv(OUT / "paired_strategy_deltas.csv", index=False)
    axes[0].axhline(0, color="gray", linewidth=.8)
    axes[0].tick_params(axis="x", rotation=30)
    axes[0].set_ylabel("Validation session delta vs frozen baseline, USD")
    axes[0].set_title("Quote variants, 60s net markout")
    signals = pd.read_csv(OUT / "signal_analysis.csv")
    view = signals[(signals.split == "validation") & (signals.horizon_s == 60) &
                   (signals.feature.isin(["net_dep_5", "net_dep_20", "net_dep_40",
                                            "net_replen_20", "ofi_norm40"]))]
    for symbol, group in view.groupby("symbol"):
        axes[1].plot(group.feature, group.mean_session_corr, marker="o", label=symbol)
    axes[1].axhline(0, color="gray", linewidth=.8)
    axes[1].tick_params(axis="x", rotation=30)
    axes[1].set_ylabel("Mean correlation with 60s mid return")
    axes[1].set_title("Predictive signal by instrument")
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(OUT / "summary.png", dpi=150)
    print(f"wrote paired_strategy_deltas.csv and summary.png")


if __name__ == "__main__":
    main()
