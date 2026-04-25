"""
Signal generation and trade-decision logic.

Entry conditions (ALL must be true):
  1. Time >= cfg.entry_after
  2. No open position today
  3. Signal (ORB / Supertrend / both) is bearish on straddle  → -1
  4. VIX <= cfg.vix_max_entry  (if cfg.use_vix_filter)

Exit conditions (first triggered wins):
  A. Stop-loss        : straddle >= entry * (1 + sl_pct/100)
  B. Target           : straddle <= entry * (1 - target_pct/100)
  C. Signal reversal  : signal flips to +1
  D. VIX spike        : VIX rose > vix_exit_rise_pct % from entry
  E. Early profit exit: already at min_profit by early_exit_time  (in backtest_engine)
  F. EOD square-off   : time >= square_off
"""

from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

from config import StrategyConfig
from indicators import vix_pct_change


# ---------------------------------------------------------------------------
# Trade dataclass
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    entry_time:       pd.Timestamp
    entry_date:       object          # datetime.date
    symbol:           str             # "NIFTY" or "SENSEX"
    strike:           int
    trade_type:       str

    call_strike:      int
    put_strike:       int

    entry_call:       float
    entry_put:        float
    entry_straddle:   float
    entry_vix:        float

    exit_time:        Optional[pd.Timestamp] = None
    exit_call:        float = 0.0
    exit_put:         float = 0.0
    exit_straddle:    float = 0.0
    exit_reason:      str   = ""

    lots:             int   = 1
    lot_size:         int   = 75

    @property
    def pnl_per_lot(self) -> float:
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
    """Return -1 (short/entry), +1 (exit), 0 (neutral)."""
    if cfg.signal_type == "orb":
        return int(row.get("orb_signal", 0))

    if cfg.signal_type == "supertrend":
        return int(row.get("supertrend_signal", 0))

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
# Entry / exit
# ---------------------------------------------------------------------------

def should_enter(row: pd.Series,
                 cfg: StrategyConfig,
                 current_time: pd.Timestamp) -> bool:
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
    cur = row["straddle_close"]
    sq  = pd.Timestamp(f"{current_time.date()} {cfg.square_off}")

    if cur >= trade.entry_straddle * (1 + cfg.sl_pct / 100):
        return "stop_loss"

    if cur <= trade.entry_straddle * (1 - cfg.target_pct / 100):
        return "target"

    if _combined_signal(row, cfg) == +1:
        return "signal_reversal"

    if cfg.use_vix_filter:
        if vix_pct_change(trade.entry_vix, row.get("vix", trade.entry_vix)) > cfg.vix_exit_rise_pct:
            return "vix_spike"

    if current_time >= sq:
        return "eod_squareoff"

    return None


# ---------------------------------------------------------------------------
# Strike selection & trade building
# ---------------------------------------------------------------------------

def atm_strike(spot: float, step: int) -> int:
    return round(spot / step) * step


def build_trade(row: pd.Series,
                current_time: pd.Timestamp,
                cfg: StrategyConfig,
                inst=None) -> Trade:           # inst: InstrumentConfig | None
    """
    Build a Trade from the current bar.
    Uses InstrumentConfig for lot_size / strike_step when provided.
    """
    from instruments import InstrumentConfig  # local import avoids circular

    spot     = row.get("spot", row.get("nifty_spot", 0.0))
    step     = inst.strike_step if inst else 50
    lot_size = inst.lot_size    if inst else 75
    symbol   = inst.symbol      if inst else "NIFTY"

    atm = atm_strike(spot, step)

    if cfg.trade_type == "straddle":
        call_k = put_k = atm
    else:
        # Strangle: OTM legs using % of spot as distance
        offset = round(spot * cfg.strangle_width_pct / 100 / step) * step
        call_k = atm + offset
        put_k  = atm - offset

    return Trade(
        entry_time     = current_time,
        entry_date     = current_time.date(),
        symbol         = symbol,
        strike         = atm,
        trade_type     = cfg.trade_type,
        call_strike    = call_k,
        put_strike     = put_k,
        entry_call     = row["call_close"],
        entry_put      = row["put_close"],
        entry_straddle = row["call_close"] + row["put_close"],
        entry_vix      = row.get("vix", 0.0),
        lots           = cfg.num_lots,
        lot_size       = lot_size,
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
