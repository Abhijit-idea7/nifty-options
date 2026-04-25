"""
Backtest engine — runs the straddle strategy across multiple instruments.

Monday    → Nifty  1DTE straddle
Wednesday → Sensex 1DTE straddle

Each instrument is backtested independently on its own data, then
results are merged into a single combined P&L / equity curve.
"""

import pandas as pd
from datetime import time as dtime
from typing import Optional

from config import StrategyConfig
from instruments import InstrumentConfig
from data_loader import load_instrument_data
from indicators import add_supertrend, add_orb
from strategy import Trade, should_enter, check_exit, build_trade, close_trade


# ---------------------------------------------------------------------------
# Single-instrument backtest
# ---------------------------------------------------------------------------

def _run_single(cfg: StrategyConfig,
                inst: InstrumentConfig,
                data_path: str) -> dict:
    """
    Run backtest for one instrument on its 1DTE trading days.
    Returns dict: trades, daily_pnl, bars.
    """
    df = load_instrument_data(inst, data_path)
    df = _add_indicators(df, cfg)

    trades: list[Trade]       = []
    open_trade: Optional[Trade] = None
    prev_date = None

    for ts, row in df.iterrows():
        today = ts.date()

        # New day reset
        if today != prev_date:
            if open_trade and open_trade.is_open:
                open_trade = close_trade(open_trade, row, ts, "forced_eod")
                trades.append(open_trade)
                open_trade = None
            prev_date = today

        # Manage open trade
        if open_trade and open_trade.is_open:
            reason = _early_exit(row, open_trade, cfg, ts)
            if reason is None:
                reason = check_exit(row, open_trade, cfg, ts)
            if reason:
                open_trade = close_trade(open_trade, row, ts, reason)
                trades.append(open_trade)
                open_trade = None
            continue   # don't look for entry on same bar as exit

        # Look for entry (one trade per day)
        already_traded = any(t.entry_date == today for t in trades)
        if not already_traded and should_enter(row, cfg, ts):
            open_trade = build_trade(row, ts, cfg, inst)

    # Close anything still open at end of data
    if open_trade and open_trade.is_open:
        last = df.iloc[-1]
        open_trade = close_trade(open_trade, last, df.index[-1], "forced_eod")
        trades.append(open_trade)

    return {
        "trades":    trades,
        "daily_pnl": _daily_pnl(trades),
        "bars":      df,
        "instrument": inst,
    }


# ---------------------------------------------------------------------------
# Multi-instrument combined backtest
# ---------------------------------------------------------------------------

def run_backtest(cfg: StrategyConfig,
                 instruments: list = None) -> dict:
    """
    Run combined backtest for all instruments (default: Nifty + Sensex).

    Returns dict:
      trades      – all trades across both instruments
      daily_pnl   – combined daily P&L series
      equity      – cumulative P&L
      per_instrument – {symbol: single-instrument result dict}
      config      – StrategyConfig used
    """
    if instruments is None:
        from instruments import ALL_INSTRUMENTS
        instruments = ALL_INSTRUMENTS

    data_paths = {
        "NIFTY":  cfg.nifty_data_path,
        "SENSEX": cfg.sensex_data_path,
    }

    all_trades: list[Trade] = []
    per_instrument: dict    = {}

    print(f"\n{'='*60}")
    print(f"  Running 1DTE Straddle Backtest")
    print(f"  Signal: {cfg.signal_type.upper()}  |  "
          f"SL: {cfg.sl_pct}%  Target: {cfg.target_pct}%")
    print(f"  Instruments: {', '.join(i.symbol for i in instruments)}")
    print(f"{'='*60}")

    for inst in instruments:
        path   = data_paths.get(inst.symbol,
                                f"data/{inst.symbol.lower()}_options_data.csv")
        result = _run_single(cfg, inst, path)
        per_instrument[inst.symbol] = result
        all_trades.extend(result["trades"])
        n = len(result["trades"])
        pnl = sum(t.total_pnl for t in result["trades"])
        print(f"  {inst.symbol:<8} ({inst.trade_day_name}s): "
              f"{n} trades | total P&L ₹{pnl:+,.0f}")

    # Merge daily P&L across instruments
    pnl_frames = [r["daily_pnl"] for r in per_instrument.values() if len(r["daily_pnl"]) > 0]
    if pnl_frames:
        combined = pd.concat(pnl_frames).groupby(level=0).sum().rename("daily_pnl")
    else:
        combined = pd.Series(dtype=float, name="daily_pnl")

    equity = combined.cumsum().rename("cumulative_pnl")

    print(f"\n  COMBINED: {len(all_trades)} trades | "
          f"total P&L ₹{sum(t.total_pnl for t in all_trades):+,.0f}")
    print(f"{'='*60}\n")

    return {
        "trades":         all_trades,
        "daily_pnl":      combined,
        "equity":         equity,
        "per_instrument": per_instrument,
        "config":         cfg,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _early_exit(row: pd.Series, trade: Trade,
                cfg: StrategyConfig, ts: pd.Timestamp) -> Optional[str]:
    """Take profit by early_exit_time if already up enough (avoids gamma spike)."""
    if cfg.early_exit_time >= cfg.square_off:
        return None
    early = dtime(*map(int, cfg.early_exit_time.split(":")))
    if ts.time() < early:
        return None
    profit_pct = (trade.entry_straddle - row["straddle_close"]) / trade.entry_straddle * 100
    return "early_profit_exit" if profit_pct >= cfg.early_exit_min_profit_pct else None


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
    return pd.DataFrame(rows).groupby("date")["pnl"].sum().rename("daily_pnl")
