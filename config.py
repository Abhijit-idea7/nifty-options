from dataclasses import dataclass
from typing import Literal


@dataclass
class StrategyConfig:
    # --- Instrument ---
    symbol: str = "NIFTY"
    lot_size: int = 75          # Current Nifty lot size; update if changed by NSE
    num_lots: int = 1
    strike_step: int = 50       # Nifty strikes are multiples of 50

    # --- Trade Structure ---
    trade_type: Literal["straddle", "strangle"] = "straddle"
    strangle_width: int = 100   # Points OTM for strangle legs (each side)

    # --- Signal ---
    # "orb"        -> use Opening Range Breakout only
    # "supertrend" -> use Supertrend only
    # "both"       -> require both signals to agree before entry
    signal_type: Literal["orb", "supertrend", "both"] = "orb"

    # --- ORB ---
    orb_duration_minutes: int = 15   # Length of opening range period

    # --- Supertrend ---
    supertrend_period: int = 7
    supertrend_multiplier: float = 3.0

    # --- VIX Filter ---
    use_vix_filter: bool = True
    vix_max_entry: float = 20.0       # Reject entry if VIX > this value
    vix_exit_rise_pct: float = 10.0   # Exit if VIX rises more than this % from entry

    # --- Risk Management ---
    sl_pct: float = 50.0        # Stop-loss: exit if straddle rises 50 % from entry
    target_pct: float = 30.0    # Target  : exit if straddle falls 30 % from entry

    # --- Session Timing ---
    session_start: str = "09:15"
    entry_after: str = "09:30"   # Earliest entry (after ORB period stabilises)
    square_off: str = "15:15"    # Hard EOD square-off
    session_end: str = "15:30"

    # --- Data ---
    data_path: str = "data/sample_nifty_options_data.csv"
