"""
Data loading for one instrument at a time.

Expected CSV columns (case-insensitive):
  datetime, symbol, spot, strike, dte,
  call_open, call_high, call_low, call_close,
  put_open,  put_high,  put_low,  put_close,
  vix
"""

import os
import pandas as pd
from instruments import InstrumentConfig


def load_instrument_data(inst: InstrumentConfig,
                         data_path: str) -> pd.DataFrame:
    """
    Load minute-bar data for one instrument.
    Auto-generates synthetic data if the file doesn't exist.
    Returns DataFrame with straddle OHLC columns appended.
    """
    if os.path.exists(data_path):
        df = _load_csv(data_path)
        print(f"[{inst.symbol}] Loaded {len(df):,} rows from {data_path}")
    else:
        print(f"[{inst.symbol}] {data_path!r} not found — generating synthetic data …")
        from data.sample_data_generator import generate_instrument_data
        df = generate_instrument_data(inst, n_weeks=30)
        os.makedirs(os.path.dirname(data_path) or ".", exist_ok=True)
        df.to_csv(data_path)
        print(f"  Saved to {data_path}")

    df = _add_straddle(df)
    _validate(df, inst.symbol)
    return df


def _load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["datetime"], index_col="datetime")
    df.columns = df.columns.str.lower().str.strip()
    df.sort_index(inplace=True)
    return df


def _add_straddle(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["straddle_open"]  = df["call_open"]  + df["put_open"]
    df["straddle_high"]  = df["call_high"]  + df["put_high"]
    df["straddle_low"]   = df["call_low"]   + df["put_low"]
    df["straddle_close"] = df["call_close"] + df["put_close"]
    return df


def _validate(df: pd.DataFrame, symbol: str) -> None:
    required = [
        "spot", "strike", "vix",
        "call_open", "call_high", "call_low", "call_close",
        "put_open",  "put_high",  "put_low",  "put_close",
        "straddle_open", "straddle_high", "straddle_low", "straddle_close",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"[{symbol}] Missing columns: {missing}")
    print(f"  [{symbol}] {df.index.min().date()} → {df.index.max().date()}"
          f"  ({df.index.normalize().nunique()} days)")
