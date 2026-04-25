from dataclasses import dataclass, field
from typing import Literal


@dataclass
class StrategyConfig:
    """
    Strategy parameters — identical logic applied to both Nifty and Sensex.
    Instrument-specific settings (lot size, strike step, expiry day) live
    in InstrumentConfig inside instruments.py.
    """

    # --- Trade Structure ---
    trade_type: Literal["straddle", "strangle"] = "straddle"
    strangle_width_pct: float = 0.5   # OTM distance as % of spot (0.5% each side)
                                      # avoids hardcoding points that differ across indices

    # --- Signal ---
    # "orb"        -> Opening Range Breakout on straddle premium
    # "supertrend" -> Supertrend on straddle premium
    # "both"       -> require BOTH signals to agree (fewer but higher-quality entries)
    signal_type: Literal["orb", "supertrend", "both"] = "orb"

    # --- ORB ---
    # Shorter window for 1DTE: less time to establish range, faster signals
    orb_duration_minutes: int = 10    # 10 min for 1DTE (use 15 for all-day trading)

    # --- Supertrend ---
    supertrend_period: int = 7
    supertrend_multiplier: float = 3.0

    # --- VIX Filter (uses India VIX as proxy for both Nifty and Sensex) ---
    use_vix_filter: bool = True
    vix_max_entry: float = 20.0       # skip entry if VIX > this
    vix_exit_rise_pct: float = 10.0   # exit if VIX rises more than this % from entry

    # --- Risk Management (calibrated for 1DTE high-gamma environment) ---
    sl_pct: float = 80.0              # wider SL needed: 1DTE gamma moves premium fast
    target_pct: float = 50.0          # achievable: rapid theta decay on 1DTE

    # --- Early Exit (avoid final-hour gamma spike on 1DTE eve) ---
    # Take profits by this time if already above early_exit_min_profit_pct
    # Set early_exit_time = square_off to disable
    early_exit_time: str = "14:00"
    early_exit_min_profit_pct: float = 25.0

    # --- Session Timing ---
    session_start: str = "09:15"
    entry_after: str = "09:25"        # 10-min ORB → allow entry at 9:25
    square_off: str = "15:15"         # hard EOD exit
    session_end: str = "15:30"

    # --- Data paths (auto-generated if missing) ---
    nifty_data_path: str  = "data/nifty_options_data.csv"
    sensex_data_path: str = "data/sensex_options_data.csv"
