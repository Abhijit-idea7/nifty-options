"""
Performance reporting: metrics, trade log table, equity curve plot.
"""

import math
import pandas as pd
import matplotlib
matplotlib.use("Agg")   # headless / no display needed (required for GitHub Actions)
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
from tabulate import tabulate

from strategy import Trade


# ---------------------------------------------------------------------------
# Performance metrics
# ---------------------------------------------------------------------------

def compute_metrics(trades: list[Trade], daily_pnl: pd.Series) -> dict:
    if not trades:
        return {}

    pnls       = [t.total_pnl for t in trades]
    winners    = [p for p in pnls if p > 0]
    losers     = [p for p in pnls if p < 0]
    total_days = len(daily_pnl)

    avg_win  = sum(winners) / len(winners) if winners else 0
    avg_loss = sum(losers)  / len(losers)  if losers  else 0

    # Sharpe (annualised, using daily P&L; assumes 252 trading days)
    if len(daily_pnl) > 1 and daily_pnl.std() != 0:
        sharpe = (daily_pnl.mean() / daily_pnl.std()) * math.sqrt(252)
    else:
        sharpe = 0.0

    # Max drawdown
    equity  = daily_pnl.cumsum()
    peak    = equity.cummax()
    dd      = equity - peak
    max_dd  = dd.min()

    # Exit reason breakdown
    reason_counts: dict[str, int] = {}
    for t in trades:
        reason_counts[t.exit_reason] = reason_counts.get(t.exit_reason, 0) + 1

    return {
        "total_trades":    len(trades),
        "trading_days":    total_days,
        "total_pnl":       sum(pnls),
        "avg_pnl_trade":   sum(pnls) / len(pnls),
        "avg_pnl_day":     daily_pnl.mean(),
        "win_trades":      len(winners),
        "loss_trades":     len(losers),
        "win_rate_pct":    len(winners) / len(trades) * 100,
        "avg_winner":      avg_win,
        "avg_loser":       avg_loss,
        "profit_factor":   abs(sum(winners) / sum(losers)) if losers else float("inf"),
        "sharpe":          sharpe,
        "max_drawdown":    max_dd,
        "exit_reasons":    reason_counts,
    }


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def print_summary(metrics: dict) -> None:
    print("\n" + "=" * 55)
    print("  BACKTEST SUMMARY")
    print("=" * 55)

    rows = [
        ["Total Trades",         metrics["total_trades"]],
        ["Trading Days",         metrics["trading_days"]],
        ["Total P&L (₹)",        f"{metrics['total_pnl']:,.0f}"],
        ["Avg P&L / Trade (₹)",  f"{metrics['avg_pnl_trade']:,.0f}"],
        ["Avg P&L / Day (₹)",    f"{metrics['avg_pnl_day']:,.0f}"],
        ["Win Rate",             f"{metrics['win_rate_pct']:.1f}%"],
        ["Winners / Losers",     f"{metrics['win_trades']} / {metrics['loss_trades']}"],
        ["Avg Winner (₹)",       f"{metrics['avg_winner']:,.0f}"],
        ["Avg Loser (₹)",        f"{metrics['avg_loser']:,.0f}"],
        ["Profit Factor",        f"{metrics['profit_factor']:.2f}"],
        ["Sharpe Ratio",         f"{metrics['sharpe']:.2f}"],
        ["Max Drawdown (₹)",     f"{metrics['max_drawdown']:,.0f}"],
    ]
    print(tabulate(rows, tablefmt="simple"))

    print("\nExit reason breakdown:")
    for reason, count in sorted(metrics["exit_reasons"].items(),
                                 key=lambda x: -x[1]):
        print(f"  {reason:<20} {count:>4} trades")
    print()


def print_trade_log(trades: list[Trade], max_rows: int = 50) -> None:
    if not trades:
        print("No trades to display.")
        return

    print(f"\n  TRADE LOG  (showing last {min(max_rows, len(trades))} trades)")
    print("-" * 85)
    rows = []
    for t in trades[-max_rows:]:
        pnl_str = f"{t.total_pnl:+,.0f}"
        rows.append([
            str(t.entry_time)[:16],
            str(t.exit_time)[:16] if t.exit_time else "—",
            t.strike,
            f"{t.entry_straddle:.1f}",
            f"{t.exit_straddle:.1f}",
            pnl_str,
            t.exit_reason,
        ])

    headers = ["Entry", "Exit", "Strike", "Entry₹", "Exit₹", "P&L(₹)", "Reason"]
    print(tabulate(rows, headers=headers, tablefmt="simple"))


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def plot_results(results: dict, save_path: str = "backtest_results.png") -> None:
    trades    = results["trades"]
    daily_pnl = results["daily_pnl"]
    equity    = results["equity"]
    cfg       = results["config"]

    sns.set_style("darkgrid")
    fig, axes = plt.subplots(3, 1, figsize=(14, 12))
    fig.suptitle(
        f"Nifty {cfg.trade_type.capitalize()} Strategy  |  "
        f"Signal: {cfg.signal_type.upper()}  |  "
        f"VIX filter: {'ON' if cfg.use_vix_filter else 'OFF'}",
        fontsize=14, fontweight="bold",
    )

    # --- 1. Equity curve ---
    ax1 = axes[0]
    equity.plot(ax=ax1, color="#2196F3", linewidth=2)
    ax1.axhline(0, color="black", linewidth=0.8, linestyle="--")
    ax1.fill_between(equity.index, equity, 0,
                     where=(equity >= 0), alpha=0.15, color="green")
    ax1.fill_between(equity.index, equity, 0,
                     where=(equity < 0),  alpha=0.15, color="red")
    ax1.set_title("Cumulative P&L (₹)")
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d-%b"))

    # --- 2. Daily P&L bars ---
    ax2 = axes[1]
    colors = ["#4CAF50" if v >= 0 else "#F44336" for v in daily_pnl]
    ax2.bar(daily_pnl.index.astype(str), daily_pnl.values, color=colors)
    ax2.axhline(0, color="black", linewidth=0.8)
    ax2.set_title("Daily P&L (₹)")
    ax2.tick_params(axis="x", rotation=45)

    # --- 3. Exit reason pie ---
    ax3 = axes[2]
    if trades:
        metrics = compute_metrics(trades, daily_pnl)
        reasons = metrics["exit_reasons"]
        colors_pie = ["#4CAF50", "#FF9800", "#F44336", "#9C27B0", "#2196F3"]
        ax3.pie(
            reasons.values(),
            labels=reasons.keys(),
            autopct="%1.1f%%",
            colors=colors_pie[:len(reasons)],
            startangle=90,
        )
        ax3.set_title("Exit Reason Distribution")

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    print(f"Chart saved → {save_path}")
    plt.show()


def plot_sample_day(results: dict, day_index: int = 0) -> None:
    """
    Plot straddle premium + indicators + trade markers for one trading day.
    Useful for visual validation of signal logic.
    """
    bars   = results["bars"]
    trades = results["trades"]

    trading_days = sorted(set(bars.index.date))
    if day_index >= len(trading_days):
        print("day_index out of range")
        return

    day   = trading_days[day_index]
    day_df = bars[bars.index.date == day]

    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    fig.suptitle(f"Straddle Premium & Signals — {day}", fontsize=13)

    # Top: straddle close + supertrend / ORB levels
    ax1 = axes[0]
    ax1.plot(day_df.index, day_df["straddle_close"], label="Straddle Premium", color="#2196F3")

    if "supertrend" in day_df.columns:
        ax1.plot(day_df.index, day_df["supertrend"], label="Supertrend",
                 color="orange", linewidth=1.5, linestyle="--")

    if "orb_high" in day_df.columns:
        ax1.axhline(day_df["orb_high"].iloc[0], color="green",
                    linestyle=":", linewidth=1.2, label="ORB High")
        ax1.axhline(day_df["orb_low"].iloc[0],  color="red",
                    linestyle=":", linewidth=1.2, label="ORB Low")

    # Trade markers
    for t in trades:
        if t.entry_date == day:
            ax1.axvline(t.entry_time, color="blue",  alpha=0.7, linewidth=1.5, linestyle="-",
                        label="Entry")
            if t.exit_time:
                ax1.axvline(t.exit_time, color="purple", alpha=0.7, linewidth=1.5,
                            linestyle="-", label=f"Exit ({t.exit_reason})")

    ax1.set_ylabel("Premium (₹)")
    ax1.legend(fontsize=8)

    # Bottom: VIX
    ax2 = axes[1]
    if "vix" in day_df.columns:
        ax2.plot(day_df.index, day_df["vix"], color="coral", linewidth=1.2)
        ax2.set_ylabel("India VIX")
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    plt.tight_layout()
    plt.show()
