"""
Instrument definitions for the 1DTE straddle strategy.

Each InstrumentConfig captures everything that differs between
Nifty and Sensex: exchange, lot size, strike grid, and which
weekday is the 1DTE trading day.

Weekly expiry schedule (as of 2024-25):
  Nifty  (NSE) → expires Tuesday  → trade Monday   (weekday 0)
  Sensex (BSE) → expires Thursday → trade Wednesday (weekday 2)
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentConfig:
    symbol: str            # "NIFTY" or "SENSEX"
    exchange: str          # "NSE"  or "BSE"
    lot_size: int          # contracts per lot
    strike_step: int       # option strike grid spacing in index points
    expiry_weekday: int    # weekday of weekly expiry  (0=Mon … 4=Fri)
    trade_weekday: int     # weekday we enter the 1DTE trade
    base_spot: float       # approximate spot for synthetic data generation
    base_iv: float         # approximate implied vol for synthetic data

    @property
    def trade_day_name(self) -> str:
        return ["Monday", "Tuesday", "Wednesday",
                "Thursday", "Friday"][self.trade_weekday]

    @property
    def expiry_day_name(self) -> str:
        return ["Monday", "Tuesday", "Wednesday",
                "Thursday", "Friday"][self.expiry_weekday]

    def dte_for_weekday(self, weekday: int) -> int:
        """Calendar days to next expiry from this weekday."""
        delta = (self.expiry_weekday - weekday) % 7
        return delta if delta > 0 else 7


# ---------------------------------------------------------------------------
# Instrument definitions — update lot_size if NSE/BSE changes them
# ---------------------------------------------------------------------------

NIFTY = InstrumentConfig(
    symbol         = "NIFTY",
    exchange       = "NSE",
    lot_size       = 75,       # verify with NSE before live trading
    strike_step    = 50,       # Nifty strikes in multiples of 50
    expiry_weekday = 1,        # Tuesday
    trade_weekday  = 0,        # Monday  (1DTE before Tuesday expiry)
    base_spot      = 22500.0,
    base_iv        = 0.13,
)

SENSEX = InstrumentConfig(
    symbol         = "SENSEX",
    exchange       = "BSE",
    lot_size       = 20,       # verify with BSE before live trading
    strike_step    = 100,      # Sensex strikes in multiples of 100
    expiry_weekday = 3,        # Thursday
    trade_weekday  = 2,        # Wednesday (1DTE before Thursday expiry)
    base_spot      = 75000.0,
    base_iv        = 0.14,
)

# Both instruments active in the strategy (2 trades per week)
ALL_INSTRUMENTS: list[InstrumentConfig] = [NIFTY, SENSEX]

# Weekday → which instrument to trade that day
WEEKDAY_TO_INSTRUMENT: dict[int, InstrumentConfig] = {
    inst.trade_weekday: inst for inst in ALL_INSTRUMENTS
}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
