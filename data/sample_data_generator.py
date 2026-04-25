"""
Generates synthetic 1-minute option data for backtesting.

For each instrument only the 1DTE trading day is generated:
  Nifty  → Mondays   (1DTE before Tuesday expiry)
  Sensex → Wednesdays (1DTE before Thursday expiry)

This keeps the data lean and mirrors exactly what the live strategy
will see during paper/live trading.
"""

import math
import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, time


# ---------------------------------------------------------------------------
# Black-Scholes (no scipy dependency)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937
            + t * (-1.821255978 + t * 1.330274429))))
    cdf = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x * x) * poly
    return cdf if x >= 0 else 1.0 - cdf


def _bs(S, K, T, r, sigma, opt):
    if T <= 1e-6:
        return max(0.05, S - K) if opt == "call" else max(0.05, K - S)
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sq)
    d2 = d1 - sigma * sq
    if opt == "call":
        return max(0.05, S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2))
    return max(0.05, K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1))


# ---------------------------------------------------------------------------
# Trading day generators
# ---------------------------------------------------------------------------

def _weekdays_matching(n_weeks: int, target_weekday: int) -> list:
    """
    Return the last n_weeks occurrences of target_weekday
    (0=Mon … 4=Fri), ending last week.
    """
    days = []
    d = datetime.now().date() - timedelta(days=1)
    while len(days) < n_weeks:
        if d.weekday() == target_weekday:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


# ---------------------------------------------------------------------------
# Per-instrument generator
# ---------------------------------------------------------------------------

def generate_instrument_data(inst,          # InstrumentConfig
                              n_weeks: int = 30,
                              seed_offset: int = 0) -> pd.DataFrame:
    """
    Generate minute-bar synthetic option data for one instrument,
    only on its 1DTE trading day.

    Returns DataFrame with columns:
      symbol, nifty_spot (or sensex_spot), strike, dte,
      call_open/high/low/close, put_open/high/low/close, vix
    """
    rng  = np.random.default_rng(42 + seed_offset)
    r    = 0.065
    rows = []

    trade_days = _weekdays_matching(n_weeks, inst.trade_weekday)
    spot       = inst.base_spot

    for trade_date in trade_days:
        # DTE = 1 (trading the day before expiry)
        dte    = 1
        T_open = dte / 365.0

        # Day-level characteristics
        day_iv  = max(0.09, min(0.40, inst.base_iv + rng.normal(0, 0.015)))
        day_vix = max(10.0, min(45.0, day_iv * 100 * math.sqrt(12)))

        atm = round(spot / inst.strike_step) * inst.strike_step

        # 375 one-minute bars: 09:15–15:30
        n_bars      = 375
        minute_vol  = day_iv / math.sqrt(252 * n_bars)
        intra_rets  = rng.normal(0, minute_vol, n_bars)

        # Occasional intraday shocks (rare but realistic)
        mask = rng.random(n_bars) < 0.004
        intra_rets[mask] += (
            rng.choice([-1, 1], mask.sum())
            * rng.uniform(0.002, 0.005, mask.sum())
        )

        spots = np.empty(n_bars + 1)
        spots[0] = spot
        for i in range(n_bars):
            spots[i + 1] = spots[i] * (1.0 + intra_rets[i])

        mkt_open = datetime.combine(trade_date, time(9, 15))

        for bar in range(n_bars):
            ts = mkt_open + timedelta(minutes=bar)

            # Time remaining decreases through the day
            T_bar = max(1e-5, T_open - (bar / n_bars) / 365.0)

            s_o = spots[bar]
            s_c = spots[bar + 1]
            sp  = abs(rng.normal(0, minute_vol / 2)) * s_o
            s_h = max(s_o, s_c) + sp
            s_l = min(s_o, s_c) - sp

            iv  = max(0.08, day_iv  * (1 + rng.normal(0, 0.015)))
            vix = max(10.0, day_vix * (1 + rng.normal(0, 0.008)))

            co  = round(_bs(s_o, atm, T_bar, r, iv, "call"), 2)
            ch  = round(_bs(s_h, atm, T_bar, r, iv, "call"), 2)
            cl  = round(_bs(s_l, atm, T_bar, r, iv, "call"), 2)
            cc  = round(_bs(s_c, atm, T_bar, r, iv, "call"), 2)

            po  = round(_bs(s_o, atm, T_bar, r, iv, "put"), 2)
            ph  = round(_bs(s_l, atm, T_bar, r, iv, "put"), 2)   # put high when spot low
            pl  = round(_bs(s_h, atm, T_bar, r, iv, "put"), 2)
            pc  = round(_bs(s_c, atm, T_bar, r, iv, "put"), 2)

            rows.append({
                "datetime":   ts,
                "symbol":     inst.symbol,
                "spot":       round(s_c, 2),
                "strike":     atm,
                "dte":        dte,
                "call_open":  co, "call_high": ch,
                "call_low":   cl, "call_close": cc,
                "put_open":   po, "put_high":  ph,
                "put_low":    pl, "put_close": pc,
                "vix":        round(vix, 2),
            })

        spot = spots[-1]

    df = pd.DataFrame(rows).set_index("datetime")
    return df


# ---------------------------------------------------------------------------
# Generate and save both instruments
# ---------------------------------------------------------------------------

def generate_all(n_weeks: int = 30, data_dir: str = "data") -> dict:
    """
    Generate data for Nifty (Mondays) and Sensex (Wednesdays).
    Saves separate CSVs and returns {symbol: DataFrame}.
    """
    from instruments import NIFTY, SENSEX  # imported here to avoid circular import

    os.makedirs(data_dir, exist_ok=True)
    results = {}

    for inst, offset in [(NIFTY, 0), (SENSEX, 100)]:
        print(f"Generating {inst.symbol} data ({inst.trade_day_name}s, {n_weeks} weeks) …")
        df   = generate_instrument_data(inst, n_weeks=n_weeks, seed_offset=offset)
        path = os.path.join(data_dir, f"{inst.symbol.lower()}_options_data.csv")
        df.to_csv(path)
        results[inst.symbol] = df
        print(f"  → {len(df):,} bars, {df.index.normalize().nunique()} trading days  [{path}]")

    return results


if __name__ == "__main__":
    generate_all(n_weeks=30)
    print("\nDone. Data saved to data/ folder.")
