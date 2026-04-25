"""
Technical indicators applied to the straddle premium time-series.

Both indicators are computed on straddle OHLC (open/high/low/close of call+put sum).

Signal convention (used throughout the project):
  -1  bearish / sell  →  enter short straddle
   0  neutral / no signal
  +1  bullish / buy   →  exit short straddle
"""

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Supertrend
# ---------------------------------------------------------------------------

def add_supertrend(df: pd.DataFrame,
                   period: int = 7,
                   multiplier: float = 3.0,
                   prefix: str = "straddle") -> pd.DataFrame:
    """
    Compute Supertrend on straddle OHLC and append columns:
      supertrend        – the Supertrend line value
      supertrend_signal – +1 (bullish/up) or -1 (bearish/down)

    For a short-straddle strategy:
      -1 (bearish on straddle) = straddle price likely to fall = GOOD entry
      +1 (bullish on straddle) = straddle price rising = EXIT signal
    """
    df = df.copy()
    high  = df[f"{prefix}_high"]
    low   = df[f"{prefix}_low"]
    close = df[f"{prefix}_close"]

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Smoothed ATR (Wilder / EMA)
    atr = tr.ewm(span=period, adjust=False).mean()

    hl2 = (high + low) / 2.0
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    n = len(df)
    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    st_line     = pd.Series(np.nan, index=df.index)
    st_signal   = pd.Series(0,      index=df.index, dtype=int)

    for i in range(1, n):
        # Final upper band
        if basic_upper.iloc[i] < final_upper.iloc[i - 1] or close.iloc[i - 1] > final_upper.iloc[i - 1]:
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i - 1]

        # Final lower band
        if basic_lower.iloc[i] > final_lower.iloc[i - 1] or close.iloc[i - 1] < final_lower.iloc[i - 1]:
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i - 1]

        # Supertrend line & direction
        prev_st = st_line.iloc[i - 1]
        if pd.isna(prev_st):
            prev_st = final_upper.iloc[i - 1]

        if prev_st == final_upper.iloc[i - 1]:
            if close.iloc[i] <= final_upper.iloc[i]:
                st_line.iloc[i]   = final_upper.iloc[i]
                st_signal.iloc[i] = -1   # bearish (straddle trending up → bad for shorts)
            else:
                st_line.iloc[i]   = final_lower.iloc[i]
                st_signal.iloc[i] = +1   # bullish → exit short
        else:
            if close.iloc[i] >= final_lower.iloc[i]:
                st_line.iloc[i]   = final_lower.iloc[i]
                st_signal.iloc[i] = +1
            else:
                st_line.iloc[i]   = final_upper.iloc[i]
                st_signal.iloc[i] = -1

    # Initialise first bar
    st_line.iloc[0]   = final_upper.iloc[0]
    st_signal.iloc[0] = -1

    df["supertrend"]        = st_line
    df["supertrend_signal"] = st_signal
    return df


# ---------------------------------------------------------------------------
# Opening Range Breakout (ORB)
# ---------------------------------------------------------------------------

def add_orb(df: pd.DataFrame,
            orb_minutes: int = 15,
            session_start: str = "09:15",
            prefix: str = "straddle") -> pd.DataFrame:
    """
    Compute Opening Range Breakout levels per trading day, then append:
      orb_high    – highest straddle price in opening range
      orb_low     – lowest  straddle price in opening range
      orb_signal  – -1 (below OR low), +1 (above OR high), 0 (inside range)

    ORB signal for short-straddle:
      -1 = straddle broke below OR low   → premium contracting → ENTRY signal
      +1 = straddle broke above OR high  → premium expanding  → EXIT  signal
    """
    df = df.copy()
    df["orb_high"]   = np.nan
    df["orb_low"]    = np.nan
    df["orb_signal"] = 0

    close = df[f"{prefix}_close"]
    high  = df[f"{prefix}_high"]
    low   = df[f"{prefix}_low"]

    for date, grp in df.groupby(df.index.date):
        t0 = pd.Timestamp(f"{date} {session_start}")
        t1 = t0 + pd.Timedelta(minutes=orb_minutes)

        or_bars = grp.loc[grp.index <= t1]
        if len(or_bars) == 0:
            continue

        or_high = high.loc[or_bars.index].max()
        or_low  = low.loc[or_bars.index].min()

        idx = grp.index
        df.loc[idx, "orb_high"] = or_high
        df.loc[idx, "orb_low"]  = or_low

        # Signal only after the ORB window closes
        post_or = grp.loc[grp.index > t1]
        for ts in post_or.index:
            c = close.loc[ts]
            if c < or_low:
                df.loc[ts, "orb_signal"] = -1
            elif c > or_high:
                df.loc[ts, "orb_signal"] = +1
            # else remains 0 (inside range)

    return df


# ---------------------------------------------------------------------------
# VIX change tracker (computed at trade-level, not bar-level)
# ---------------------------------------------------------------------------

def vix_pct_change(entry_vix: float, current_vix: float) -> float:
    """Returns % change in VIX since entry (positive = VIX rose)."""
    if entry_vix == 0:
        return 0.0
    return (current_vix - entry_vix) / entry_vix * 100.0
