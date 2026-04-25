"""
Combined backtest runner — Nifty (Mondays) + Sensex (Wednesdays).

Edit the CONFIG block below or set environment variables to tune parameters.
GitHub Actions sets env vars automatically from the workflow form inputs.
"""

import os
import pandas as pd
from config import StrategyConfig
from instruments import ALL_INSTRUMENTS
from backtest_engine import run_backtest
from report import compute_metrics, print_summary, print_trade_log, plot_results

# ===========================================================================
#  CONFIGURATION  — edit here or override via env variables
# ===========================================================================
cfg = StrategyConfig(
    # Signal: "orb" | "supertrend" | "both"
    signal_type                = os.getenv("SIGNAL_TYPE",      "orb"),

    # Trade structure: "straddle" | "strangle"
    trade_type                 = os.getenv("TRADE_TYPE",       "straddle"),

    # Position size (applies to each instrument separately)
    num_lots                   = int(os.getenv("NUM_LOTS",       "1")),

    # Risk — calibrated for 1DTE high-gamma environment
    sl_pct                     = float(os.getenv("SL_PCT",       "80")),
    target_pct                 = float(os.getenv("TARGET_PCT",   "50")),

    # ORB: 10 min window for 1DTE
    orb_duration_minutes       = int(os.getenv("ORB_MINUTES",    "10")),

    # Supertrend
    supertrend_period          = int(os.getenv("ST_PERIOD",      "7")),
    supertrend_multiplier      = float(os.getenv("ST_MULT",      "3.0")),

    # VIX filter (India VIX used as proxy for both indices)
    use_vix_filter             = os.getenv("USE_VIX", "true").lower() == "true",
    vix_max_entry              = float(os.getenv("VIX_MAX",      "20.0")),
    vix_exit_rise_pct          = float(os.getenv("VIX_EXIT",     "10.0")),

    # Early profit exit (lock in gains before final-hour gamma spike)
    early_exit_time            = os.getenv("EARLY_EXIT_TIME",   "14:00"),
    early_exit_min_profit_pct  = float(os.getenv("EARLY_EXIT_PCT", "25")),

    # Timing
    entry_after                = "09:25",
    square_off                 = "15:15",

    # Data paths (auto-generated if missing)
    nifty_data_path            = "data/nifty_options_data.csv",
    sensex_data_path           = "data/sensex_options_data.csv",
)
# ===========================================================================


def save_trade_log(trades, path="trade_log.csv"):
    if not trades:
        return
    rows = [{
        "entry_time":      t.entry_time,
        "exit_time":       t.exit_time,
        "date":            t.entry_date,
        "symbol":          t.symbol,
        "strike":          t.strike,
        "trade_type":      t.trade_type,
        "entry_straddle":  t.entry_straddle,
        "exit_straddle":   t.exit_straddle,
        "pnl_per_lot":     round(t.pnl_per_lot, 2),
        "total_pnl":       round(t.total_pnl, 2),
        "exit_reason":     t.exit_reason,
        "entry_vix":       t.entry_vix,
        "lots":            t.lots,
        "lot_size":        t.lot_size,
    } for t in trades]
    pd.DataFrame(rows).to_csv(path, index=False)
    print(f"Trade log saved  → {path}")


def save_metrics(metrics, path="metrics_summary.csv"):
    rows = [(k, v) for k, v in metrics.items() if k != "exit_reasons"]
    pd.DataFrame(rows, columns=["metric", "value"]).to_csv(path, index=False)
    print(f"Metrics saved    → {path}")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  1DTE Straddle Strategy Backtest")
    print("  Nifty (Mondays) + Sensex (Wednesdays)")
    print(f"  Signal: {cfg.signal_type.upper()}  |  "
          f"SL: {cfg.sl_pct}%  Target: {cfg.target_pct}%")
    print(f"  VIX filter: {'ON' if cfg.use_vix_filter else 'OFF'}  |  "
          f"Early exit: {cfg.early_exit_time} if >{cfg.early_exit_min_profit_pct}%")
    print("=" * 60 + "\n")

    results = run_backtest(cfg, instruments=ALL_INSTRUMENTS)
    metrics = compute_metrics(results["trades"], results["daily_pnl"])

    print_summary(metrics, per_instrument=results.get("per_instrument"))
    print_trade_log(results["trades"])

    save_trade_log(results["trades"])
    save_metrics(metrics)
    plot_results(results, save_path="backtest_results.png")
