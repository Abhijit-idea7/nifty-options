"""
Entry point for the Nifty Straddle/Strangle backtest.

Usage
-----
  python main.py                    # run with defaults (ORB, straddle, VIX on)
  python main.py --signal supertrend
  python main.py --type strangle --sl 40 --target 25
  python main.py --no-vix
  python main.py --plot-day 0       # show chart for first trading day

Run  python main.py --help  for all options.
"""

import argparse
from config import StrategyConfig
from backtest_engine import run_backtest
from report import compute_metrics, print_summary, print_trade_log, plot_results, plot_sample_day


def parse_args() -> StrategyConfig:
    p = argparse.ArgumentParser(description="Nifty Straddle Backtest Engine")

    p.add_argument("--signal",   choices=["orb", "supertrend", "both"], default="orb")
    p.add_argument("--type",     choices=["straddle", "strangle"],       default="straddle")
    p.add_argument("--lots",     type=int,   default=1)
    p.add_argument("--lot-size", type=int,   default=75)
    p.add_argument("--sl",       type=float, default=50.0,  help="Stop-loss %%")
    p.add_argument("--target",   type=float, default=30.0,  help="Target %%")
    p.add_argument("--orb-min",  type=int,   default=15,    help="ORB duration in minutes")
    p.add_argument("--st-period",type=int,   default=7,     help="Supertrend ATR period")
    p.add_argument("--st-mult",  type=float, default=3.0,   help="Supertrend multiplier")
    p.add_argument("--vix-max",  type=float, default=20.0,  help="Max VIX for entry")
    p.add_argument("--vix-exit", type=float, default=10.0,  help="Exit on VIX rise %%")
    p.add_argument("--no-vix",   action="store_true",       help="Disable VIX filter")
    p.add_argument("--data",     type=str,   default="data/sample_nifty_options_data.csv")
    p.add_argument("--no-plot",  action="store_true",       help="Skip chart generation")
    p.add_argument("--plot-day", type=int,   default=None,  help="Plot intraday view for day N")
    p.add_argument("--log",      action="store_true",       help="Print full trade log")

    args = p.parse_args()

    return StrategyConfig(
        signal_type            = args.signal,
        trade_type             = args.type,
        num_lots               = args.lots,
        lot_size               = args.lot_size,
        sl_pct                 = args.sl,
        target_pct             = args.target,
        orb_duration_minutes   = args.orb_min,
        supertrend_period      = args.st_period,
        supertrend_multiplier  = args.st_mult,
        vix_max_entry          = args.vix_max,
        vix_exit_rise_pct      = args.vix_exit,
        use_vix_filter         = not args.no_vix,
        data_path              = args.data,
    )


def main() -> None:
    cfg     = parse_args()
    results = run_backtest(cfg)
    metrics = compute_metrics(results["trades"], results["daily_pnl"])

    print_summary(metrics)

    if hasattr(cfg, "_log") or True:       # always show trade log for now
        pass

    from report import print_trade_log
    import sys
    if "--log" in sys.argv:
        print_trade_log(results["trades"])

    if "--no-plot" not in sys.argv:
        plot_results(results)

    if hasattr(cfg, "_plot_day"):
        pass

    import sys
    for i, arg in enumerate(sys.argv):
        if arg == "--plot-day" and i + 1 < len(sys.argv):
            plot_sample_day(results, int(sys.argv[i + 1]))
            break


if __name__ == "__main__":
    main()
