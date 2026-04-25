"""
Signal generation and trade-decision logic.

Entry conditions (ALL must be true):
  1. Time >= cfg.entry_after
  2. No open position
  3. Signal (ORB / Supertrend / both) is bearish on straddle  → -1
  4. VIX <= cfg.vix_max_entry  (if cfg.use_vix_filter)

Exit conditions (first triggered wins, checked every bar):
  A. Stop-loss   : current straddle >= entry_straddle * (1 + sl_pct/100)
  B. Target      : current straddle <= entry_straddle * (1 - target_pct/100)
  C. Signal flip : signal turns bullish (+1) on straddle
  D. VIX spike   : VIX rose > cfg.vix_exit_rise_pct % from entry VIX
  E. EOD         : time >= cfg.square_off
"""

from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
from config import StrategyConfig
from indicators import vix_pct_change


# ---------------------------------------------------------------------------
# Data class for an open trade
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    entry_time:       pd.Timestamp
    entry_date:       object          # datetime.date
    strike:           int
    trade_type:       str             # "straddle" or "strangle"

    call_strike:      int
    put_strike:       int

    entry_call:       float           # call premium at entry
    entry_put:        float           # put premium at entry
    entry_straddle:   float           # call + put at entry (target reference)
    entry_vix:        float

    exit_time:        Optional[pd.Timestamp] = None
    exit_call:        float = 0.0
    exit_put:         float = 0.0
    exit_straddle:    float = 0.0
    exit_reason:      str = ""

    lots:             int = 1
    lot_size:         int = 75

    @property
    def pnl_per_lot(self) -> float:
        """P&L per lot: collected premium - buyback premium."""
        return (self.entry_straddle - self.exit_straddle) * self.lot_size

    @property
    def total_pnl(self) -> float:
        return self.pnl_per_lot * self.lots

    @property
    def is_open(self) -> bool:
        return self.exit_time is None


# ---------------------------------------------------------------------------
# Signal helpers
# ---------------------------------------------------------------------------

def _combined_signal(row: pd.Series, cfg: StrategyConfig) -> int:
    """
    Return -1 (short/entry), +1 (long/exit), or 0 (neutral) based on cfg.signal_type.
    """
    if cfg.signal_type == "orb":
        return int(row.get("orb_signal", 0))

    if cfg.signal_type == "supertrend":
        st = int(row.get("supertrend_signal", 0))
        # Supertrend convention: -1 = bearish straddle (good for short), +1 = bullish
        return st

    if cfg.signal_type == "both":
        orb = int(row.get("orb_signal", 0))
        st  = int(row.get("supertrend_signal", 0))
        if orb == -1 and st == -1:
            return -1
        if orb == +1 or st == +1:
            return +1
        return 0

    return 0


# ---------------------------------------------------------------------------
# Entry / exit decision functions
# ---------------------------------------------------------------------------

def should_enter(row: pd.Series, cfg: StrategyConfig, current_time: pd.Timestamp) -> bool:
    """True when all entry conditions are satisfied."""
    entry_after = pd.Timestamp(f"{current_time.date()} {cfg.entry_after}")
    square_off  = pd.Timestamp(f"{current_time.date()} {cfg.square_off}")

    if current_time < entry_after or current_time >= square_off:
        return False

    if _combined_signal(row, cfg) != -1:
        return False

    if cfg.use_vix_filter and row.get("vix", 0) > cfg.vix_max_entry:
        return False

    return True


def check_exit(row: pd.Series,
               trade: Trade,
               cfg: StrategyConfig,
               current_time: pd.Timestamp) -> Optional[str]:
    """
    Returns exit reason string if an exit condition is met, else None.
    Exit order: SL > Target > Signal flip > VIX > EOD
    """
    cur_straddle = row["straddle_close"]
    square_off   = pd.Timestamp(f"{current_time.date()} {cfg.square_off}")

    # A. Stop-loss
    sl_level = trade.entry_straddle * (1 + cfg.sl_pct / 100)
    if cur_straddle >= sl_level:
        return "stop_loss"

    # B. Target
    tgt_level = trade.entry_straddle * (1 - cfg.target_pct / 100)
    if cur_straddle <= tgt_level:
        return "target"

    # C. Signal reversal
    if _combined_signal(row, cfg) == +1:
        return "signal_reversal"

    # D. VIX spike
    if cfg.use_vix_filter:
        vix_chg = vix_pct_change(trade.entry_vix, row.get("vix", trade.entry_vix))
        if vix_chg > cfg.vix_exit_rise_pct:
            return "vix_spike"

    # E. EOD square-off
    if current_time >= square_off:
        return "eod_squareoff"

    return None


# ---------------------------------------------------------------------------
# Strike selection
# ---------------------------------------------------------------------------

def atm_strike(spot: float, step: int = 50) -> int:
    return round(spot / step) * step


def get_call_put_strikes(spot: float, cfg: StrategyConfig) -> tuple[int, int]:
    """Return (call_strike, put_strike) for the chosen trade structure."""
    atm = atm_strike(spot, cfg.strike_step)
    if cfg.trade_type == "straddle":
        return atm, atm
    # Strangle: sell OTM call above ATM, OTM put below ATM
    call_k = atm + cfg.strangle_width
    put_k  = atm - cfg.strangle_width
    return call_k, put_k


# ---------------------------------------------------------------------------
# Build a Trade object at entry bar
# ---------------------------------------------------------------------------

def build_trade(row: pd.Series,
                current_time: pd.Timestamp,
                cfg: StrategyConfig) -> Trade:
    spot = row["nifty_spot"]
    call_k, put_k = get_call_put_strikes(spot, cfg)

    # For straddle both legs same strike → use straddle columns directly.
    # For strangle we would need OTM option prices; with current data format
    # we approximate using ATM columns (full strangle support needs per-strike data).
    entry_call     = row["call_close"]
    entry_put      = row["put_close"]
    entry_straddle = entry_call + entry_put

    return Trade(
        entry_time     = current_time,
        entry_date     = current_time.date(),
        strike         = atm_strike(spot, cfg.strike_step),
        trade_type     = cfg.trade_type,
        call_strike    = call_k,
        put_strike     = put_k,
        entry_call     = entry_call,
        entry_put      = entry_put,
        entry_straddle = entry_straddle,
        entry_vix      = row.get("vix", 0.0),
        lots           = cfg.num_lots,
        lot_size       = cfg.lot_size,
    )


def close_trade(trade: Trade,
                row: pd.Series,
                current_time: pd.Timestamp,
                reason: str) -> Trade:
    trade.exit_time     = current_time
    trade.exit_call     = row["call_close"]
    trade.exit_put      = row["put_close"]
    trade.exit_straddle = trade.exit_call + trade.exit_put
    trade.exit_reason   = reason
    return trade
