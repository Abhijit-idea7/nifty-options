"""
Data loading utilities.

Supported formats
-----------------
1. Standard CSV  (broker exports, Zerodha, Upstox, etc.)
   Required columns (case-insensitive):
     datetime, nifty_spot, strike,
     call_open, call_high, call_low, call_close,
     put_open,  put_high,  put_low,  put_close,
     vix

2. Separate CSVs for call and put (common when downloading per-instrument)
   Each file must have: datetime, open, high, low, close
   Spot + VIX supplied separately.

3. Synthetic sample data (auto-generated when no file is found).
"""

import os
import pandas as pd
from config import StrategyConfig


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_data(cfg: StrategyConfig) -> pd.DataFrame:
    """
    Load options data as specified in cfg.data_path.
    Falls back to synthetic data if the file does not exist.

    Returns a DataFrame indexed by datetime with straddle OHLC columns added:
      straddle_open, straddle_high, straddle_low, straddle_close
    """
    if os.path.exists(cfg.data_path):
        df = _load_csv(cfg.data_path)
        print(f"Loaded {len(df):,} rows from {cfg.data_path}")
    else:
        print(f"[data_loader] {cfg.data_path!r} not found — generating synthetic data …")
        from data.sample_data_generator import generate_sample_data
        df = generate_sample_data(n_days=30)
        os.makedirs(os.path.dirname(cfg.data_path) or ".", exist_ok=True)
        df.to_csv(cfg.data_path)
        print(f"  Synthetic data saved to {cfg.data_path}")

    df = _add_straddle_columns(df)
    _validate(df)
    return df


def load_separate_csvs(call_path: str,
                       put_path: str,
                       spot_vix_path: str) -> pd.DataFrame:
    """
    Merge separately-downloaded call / put / spot files into the standard format.
    Each options file needs: datetime, open, high, low, close
    spot_vix_path needs  : datetime, nifty_spot, vix
    """
    call = pd.read_csv(call_path, parse_dates=["datetime"], index_col="datetime")
    put  = pd.read_csv(put_path,  parse_dates=["datetime"], index_col="datetime")
    sv   = pd.read_csv(spot_vix_path, parse_dates=["datetime"], index_col="datetime")

    call.columns = [f"call_{c}" for c in call.columns]
    put.columns  = [f"put_{c}"  for c in put.columns]

    df = call.join(put, how="inner").join(sv, how="inner")
    df.index.name = "datetime"
    df = _add_straddle_columns(df)
    _validate(df)
    return df


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["datetime"], index_col="datetime")
    df.columns = df.columns.str.lower().str.strip()
    df.sort_index(inplace=True)
    return df


def _add_straddle_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Compute straddle (call + put) OHLC as a synthetic instrument."""
    df = df.copy()
    df["straddle_open"]  = df["call_open"]  + df["put_open"]
    df["straddle_high"]  = df["call_high"]  + df["put_high"]
    df["straddle_low"]   = df["call_low"]   + df["put_low"]
    df["straddle_close"] = df["call_close"] + df["put_close"]
    return df


def _validate(df: pd.DataFrame) -> None:
    required = [
        "nifty_spot", "strike", "vix",
        "call_open", "call_high", "call_low", "call_close",
        "put_open",  "put_high",  "put_low",  "put_close",
        "straddle_open", "straddle_high", "straddle_low", "straddle_close",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Data is missing columns: {missing}")
    if df.index.duplicated().any():
        raise ValueError("Duplicate timestamps found in data.")
    print(f"  Data spans {df.index.min()} → {df.index.max()}  ({df.index.normalize().nunique()} days)")
