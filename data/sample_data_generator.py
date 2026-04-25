"""
Generates synthetic Nifty options minute-bar data for backtesting.

Pricing model: Simplified Black-Scholes with stochastic IV
(sufficient for strategy development; replace with real broker data for live use)
"""

import math
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, time


# ---------------------------------------------------------------------------
# Black-Scholes helpers (no external dependencies)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    """Abramowitz & Stegun approximation (max error 7.5e-8)."""
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937
            + t * (-1.821255978 + t * 1.330274429))))
    cdf = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x * x) * poly
    return cdf if x >= 0 else 1.0 - cdf


def bs_price(S: float, K: float, T: float, r: float, sigma: float, opt: str) -> float:
    """Black-Scholes price for European call/put."""
    if T <= 1e-6:
        return max(0.0, S - K) if opt == "call" else max(0.0, K - S)
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    if opt == "call":
        price = S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        price = K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
    return max(0.05, round(price, 2))


# ---------------------------------------------------------------------------
# Day generator
# ---------------------------------------------------------------------------

def _get_trading_days(n_days: int) -> list:
    """Return last n_days weekdays (Mon-Fri) ending yesterday."""
    days = []
    d = datetime.now().date() - timedelta(days=1)
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def _days_to_next_thursday(date) -> int:
    """Calendar days from date to the next/same Thursday (weekly expiry)."""
    delta = (3 - date.weekday()) % 7
    return delta if delta > 0 else 7


def generate_sample_data(n_days: int = 30,
                         base_spot: float = 22500.0,
                         base_iv: float = 0.14,
                         seed: int = 42) -> pd.DataFrame:
    """
    Returns a DataFrame indexed by datetime with columns:
      nifty_spot, strike,
      call_open, call_high, call_low, call_close,
      put_open,  put_high,  put_low,  put_close,
      vix
    """
    rng = np.random.default_rng(seed)
    trading_days = _get_trading_days(n_days)
    rows = []
    spot = base_spot
    r = 0.065  # risk-free rate

    for trade_date in trading_days:
        dte = _days_to_next_thursday(trade_date)          # calendar days to expiry
        T_open = dte / 365.0                              # time to expiry at open

        # Day-level IV with persistence (mean-reverting random walk)
        day_iv = max(0.09, min(0.40, base_iv + rng.normal(0, 0.015)))
        day_vix = day_iv * 100 * math.sqrt(12)            # rough annualised VIX proxy
        day_vix = max(10.0, min(45.0, day_vix))

        # ATM strike fixed at open (nearest 50)
        atm = round(spot / 50) * 50

        # Intraday spot path (375 one-minute bars: 09:15 – 15:30)
        n_bars = 375
        minute_vol = day_iv / math.sqrt(252 * 375)
        intraday_rets = rng.normal(0, minute_vol, n_bars)

        # Occasional intraday shocks
        shock_mask = rng.random(n_bars) < 0.005
        intraday_rets[shock_mask] += rng.choice([-1, 1], shock_mask.sum()) * rng.uniform(0.002, 0.006, shock_mask.sum())

        spot_series = np.empty(n_bars + 1)
        spot_series[0] = spot
        for i in range(n_bars):
            spot_series[i + 1] = spot_series[i] * (1.0 + intraday_rets[i])

        market_open = datetime.combine(trade_date, time(9, 15))

        for bar in range(n_bars):
            ts = market_open + timedelta(minutes=bar)

            # Fraction of day elapsed → remaining T
            T_bar = max(1e-5, T_open - (bar / n_bars) * (1.0 / 365.0))

            s_open  = spot_series[bar]
            s_close = spot_series[bar + 1]
            spread  = abs(rng.normal(0, minute_vol / 2)) * s_open
            s_high  = max(s_open, s_close) + spread
            s_low   = min(s_open, s_close) - spread

            # Intraday IV jitter
            iv = max(0.08, day_iv * (1 + rng.normal(0, 0.015)))
            vix = max(10.0, day_vix * (1 + rng.normal(0, 0.008)))

            # Option OHLC (call high ~ spot high; put high ~ spot low)
            c_open  = bs_price(s_open,  atm, T_bar, r, iv, "call")
            c_high  = bs_price(s_high,  atm, T_bar, r, iv, "call")
            c_low   = bs_price(s_low,   atm, T_bar, r, iv, "call")
            c_close = bs_price(s_close, atm, T_bar, r, iv, "call")

            p_open  = bs_price(s_open,  atm, T_bar, r, iv, "put")
            p_high  = bs_price(s_low,   atm, T_bar, r, iv, "put")   # put high when spot low
            p_low   = bs_price(s_high,  atm, T_bar, r, iv, "put")   # put low when spot high
            p_close = bs_price(s_close, atm, T_bar, r, iv, "put")

            rows.append({
                "datetime":   ts,
                "nifty_spot": round(s_close, 2),
                "strike":     atm,
                "call_open":  c_open,  "call_high": c_high,
                "call_low":   c_low,   "call_close": c_close,
                "put_open":   p_open,  "put_high":  p_high,
                "put_low":    p_low,   "put_close": p_close,
                "vix":        round(vix, 2),
            })

        spot = spot_series[-1]   # carry spot forward to next day

    df = pd.DataFrame(rows).set_index("datetime")
    return df


if __name__ == "__main__":
    import os
    os.makedirs("data", exist_ok=True)
    print("Generating 30-day synthetic Nifty options data …")
    df = generate_sample_data(n_days=30)
    out = "data/sample_nifty_options_data.csv"
    df.to_csv(out)
    print(f"Saved {len(df):,} rows covering {df.index.normalize().nunique()} days → {out}")
    print(df.tail(3).to_string())
