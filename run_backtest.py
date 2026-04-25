"""
Main backtest runner.

Edit the CONFIG block below to change strategy settings, then run:
  python run_backtest.py

On GitHub Actions this file is called automatically — outputs are saved
as downloadable artifacts (backtest_results.png, trade_log.csv).
"""

import os
import pandas as pd
from config import StrategyConfig
from backtest_engine import run_backtest
from report import (
    compute_metrics, print_summary, print_trade_log,
    plot_results, plot_sample_day,
)

# ===========================================================================
#  STRATEGY CONFIGURATION  — change these to tune the strategy
# ===========================================================================
cfg = StrategyConfig(
    # Signal method: "orb" | "supertrend" | "both"
    signal_type           = os.getenv("SIGNAL_TYPE",  "orb"),

    # Trade type: "straddle" (same strike) | "strangle" (OTM legs)
    trade_type            = os.getenv("TRADE_TYPE",   "straddle"),

    # Nifty lot size (currently 75; update if NSE changes it)
    lot_size              = int(os.getenv("LOT_SIZE",  "75")),
    num_lots              = int(os.getenv("NUM_LOTS",   "1")),

    # Risk management
    sl_pct                = float(os.getenv("SL_PCT",      "50")),   # SL at +50% of entry premium
    target_pct            = float(os.getenv("TARGET_PCT",  "30")),   # Target at -30% of entry premium

    # ORB window (in minutes from market open)
    orb_duration_minutes  = int(os.getenv("ORB_MINUTES",  "15")),

    # Supertrend parameters
    supertrend_period     = int(os.getenv("ST_PERIOD",    "7")),
    supertrend_multiplier = float(os.getenv("ST_MULT",    "3.0")),

    # India VIX filter
    use_vix_filter        = os.getenv("USE_VIX", "true").lower() == "true",
    vix_max_entry         = float(os.getenv("VIX_MAX",    "20.0")),
    vix_exit_rise_pct     = float(os.getenv("VIX_EXIT",   "10.0")),

    # Session timing
    entry_after           = "09:30",
    square_off            = "15:15",

    # Data: auto-generated if file is missing
    data_path             = "data/sample_nifty_options_data.csv",
)
# ===========================================================================


def save_trade_log(trades, path: str = "trade_log.csv") -> None:
    if not trades:
        print("No trades — trade_log.csv not created.")
        return
    rows = []
    for t in trades:
        rows.append({
            "entry_time":      t.entry_time,
            "exit_time":       t.exit_time,
            "date":            t.entry_date,
            "strike":          t.strike,
            "trade_type":      t.trade_type,
            "entry_call":      t.entry_call,
            "entry_put":       t.entry_put,
            "entry_straddle":  t.entry_straddle,
            "exit_call":       t.exit_call,
            "exit_put":        t.exit_put,
            "exit_straddle":   t.exit_straddle,
            "pnl_per_lot":     round(t.pnl_per_lot, 2),
            "total_pnl":       round(t.total_pnl, 2),
            "exit_reason":     t.exit_reason,
            "entry_vix":       t.entry_vix,
            "lots":            t.lots,
        })
    pd.DataFrame(rows).to_csv(path, index=False)
    print(f"Trade log saved → {path}")


def save_metrics_csv(metrics: dict, path: str = "metrics_summary.csv") -> None:
    rows = [(k, v) for k, v in metrics.items() if k != "exit_reasons"]
    pd.DataFrame(rows, columns=["metric", "value"]).to_csv(path, index=False)
    print(f"Metrics saved    → {path}")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print(f"  Nifty {cfg.trade_type.upper()} Backtest")
    print(f"  Signal: {cfg.signal_type.upper()}  |  VIX filter: {'ON' if cfg.use_vix_filter else 'OFF'}")
    print(f"  SL: {cfg.sl_pct}%  Target: {cfg.target_pct}%  Lots: {cfg.num_lots}")
    print("=" * 60 + "\n")

    results = run_backtest(cfg)
    metrics = compute_metrics(results["trades"], results["daily_pnl"])

    print_summary(metrics)
    print_trade_log(results["trades"])

    # Save artefacts (picked up by GitHub Actions as downloadable files)
    save_trade_log(results["trades"])
    save_metrics_csv(metrics)
    plot_results(results, save_path="backtest_results.png")
