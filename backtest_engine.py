"""
Backtest engine: iterates over minute bars, applies strategy logic,
records trades, and returns a results bundle.
"""

import pandas as pd
from typing import Optional

from config import StrategyConfig
from data_loader import load_data
from indicators import add_supertrend, add_orb
from strategy import (
    Trade, should_enter, check_exit, build_trade, close_trade
)


# ---------------------------------------------------------------------------
# Main backtest runner
# ---------------------------------------------------------------------------

def run_backtest(cfg: StrategyConfig) -> dict:
    """
    Run full backtest for the configured period.

    Returns
    -------
    dict with keys:
      trades      – list[Trade]
      daily_pnl   – pd.Series (date → total P&L)
      equity      – pd.Series (cumulative P&L over time)
      bars        – processed DataFrame (includes indicator columns)
    """
    # 1. Load & prepare data
    df = load_data(cfg)
    df = _add_indicators(df, cfg)

    trades: list[Trade]   = []
    open_trade: Optional[Trade] = None

    # 2. Bar-by-bar simulation
    prev_date = None
    for ts, row in df.iterrows():
        current_date = ts.date()

        # Reset open trade at new day start (safety check — should be closed by EOD)
        if current_date != prev_date:
            if open_trade is not None and open_trade.is_open:
                # Force-close any trade that wasn't squared off (data gap etc.)
                open_trade = close_trade(open_trade, row, ts, "forced_eod")
                trades.append(open_trade)
                open_trade = None
            prev_date = current_date

        # --- Manage open trade ---
        if open_trade is not None and open_trade.is_open:
            reason = check_exit(row, open_trade, cfg, ts)
            if reason:
                open_trade = close_trade(open_trade, row, ts, reason)
                trades.append(open_trade)
                open_trade = None
            continue   # Don't enter a new trade on the same bar we exited

        # --- Look for entry (only one trade per day) ---
        if open_trade is None:
            already_traded_today = any(t.entry_date == current_date for t in trades)
            if not already_traded_today and should_enter(row, cfg, ts):
                open_trade = build_trade(row, ts, cfg)

    # Close any remaining open trade at last bar
    if open_trade is not None and open_trade.is_open:
        last_row = df.iloc[-1]
        open_trade = close_trade(open_trade, last_row, df.index[-1], "forced_eod")
        trades.append(open_trade)

    # 3. Build result series
    daily_pnl = _daily_pnl(trades)
    equity    = daily_pnl.cumsum().rename("cumulative_pnl")

    print(f"\n{'='*55}")
    print(f"  Backtest complete: {len(trades)} trades over "
          f"{df.index.normalize().nunique()} days")
    print(f"{'='*55}\n")

    return {
        "trades":    trades,
        "daily_pnl": daily_pnl,
        "equity":    equity,
        "bars":      df,
        "config":    cfg,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_indicators(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    if cfg.signal_type in ("supertrend", "both"):
        df = add_supertrend(df,
                            period=cfg.supertrend_period,
                            multiplier=cfg.supertrend_multiplier)

    if cfg.signal_type in ("orb", "both"):
        df = add_orb(df,
                     orb_minutes=cfg.orb_duration_minutes,
                     session_start=cfg.session_start)
    return df


def _daily_pnl(trades: list[Trade]) -> pd.Series:
    if not trades:
        return pd.Series(dtype=float, name="daily_pnl")
    rows = [{"date": t.entry_date, "pnl": t.total_pnl} for t in trades]
    return (
        pd.DataFrame(rows)
          .groupby("date")["pnl"]
          .sum()
          .rename("daily_pnl")
    )
