"""
Fetches live Nifty option chain + India VIX from NSE India (free, no API key).

NSE requires a browser-like session (cookies from the home page) — this
module handles that automatically.
"""

import time
import requests
import pandas as pd
from datetime import datetime


# ---------------------------------------------------------------------------
# Session setup  (NSE blocks plain requests without cookies)
# ---------------------------------------------------------------------------

def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.nseindia.com/",
    })
    # Hit homepage to get session cookies
    try:
        session.get("https://www.nseindia.com", timeout=10)
        time.sleep(1)
        session.get("https://www.nseindia.com/option-chain", timeout=10)
        time.sleep(1)
    except Exception:
        pass   # proceed; some cookies may still work
    return session


# ---------------------------------------------------------------------------
# Option chain
# ---------------------------------------------------------------------------

def fetch_option_chain(symbol: str = "NIFTY") -> dict | None:
    """
    Returns the raw NSE option chain JSON for NIFTY (or BANKNIFTY).
    Returns None on failure.
    """
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    session = _make_session()
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[nse_fetcher] option chain fetch failed: {e}")
        return None


def fetch_vix() -> float | None:
    """Returns current India VIX value, or None on failure."""
    url = "https://www.nseindia.com/api/allIndices"
    session = _make_session()
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        for item in data:
            if item.get("index") == "INDIA VIX":
                return float(item["last"])
        return None
    except Exception as e:
        print(f"[nse_fetcher] VIX fetch failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Parse ATM call + put prices from raw option chain
# ---------------------------------------------------------------------------

def get_atm_prices(symbol: str = "NIFTY",
                   strike_step: int = 50) -> dict | None:
    """
    Returns a dict with:
      spot, expiry, atm_strike,
      call_bid, call_ask, call_ltp,
      put_bid,  put_ask,  put_ltp,
      straddle_ltp,
      vix, timestamp
    or None on failure.
    """
    raw = fetch_option_chain(symbol)
    if raw is None:
        return None

    spot = float(raw["records"]["underlyingValue"])
    atm  = round(spot / strike_step) * strike_step

    # Pick nearest weekly expiry (first in the list)
    expiry_dates = raw["records"]["expiryDates"]
    expiry = expiry_dates[0] if expiry_dates else "UNKNOWN"

    # Find ATM row in filtered data (current expiry)
    call_data = put_data = None
    for row in raw.get("filtered", {}).get("data", []):
        if row.get("expiryDate") == expiry and row.get("strikePrice") == atm:
            call_data = row.get("CE", {})
            put_data  = row.get("PE", {})
            break

    if call_data is None or put_data is None:
        print(f"[nse_fetcher] ATM strike {atm} not found in option chain")
        return None

    def safe(d, key):
        val = d.get(key, 0)
        return float(val) if val else 0.0

    call_ltp = safe(call_data, "lastPrice")
    put_ltp  = safe(put_data,  "lastPrice")

    vix = fetch_vix()

    return {
        "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "spot":         spot,
        "expiry":       expiry,
        "atm_strike":   atm,
        "call_ltp":     call_ltp,
        "call_bid":     safe(call_data, "bidPrice"),
        "call_ask":     safe(call_data, "askPrice"),
        "call_oi":      safe(call_data, "openInterest"),
        "call_iv":      safe(call_data, "impliedVolatility"),
        "put_ltp":      put_ltp,
        "put_bid":      safe(put_data,  "bidPrice"),
        "put_ask":      safe(put_data,  "askPrice"),
        "put_oi":       safe(put_data,  "openInterest"),
        "put_iv":       safe(put_data,  "impliedVolatility"),
        "straddle_ltp": call_ltp + put_ltp,
        "vix":          vix,
    }


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Fetching live Nifty ATM straddle prices …")
    data = get_atm_prices()
    if data:
        print(f"\n  Time        : {data['timestamp']}")
        print(f"  Nifty Spot  : {data['spot']}")
        print(f"  ATM Strike  : {data['atm_strike']}  (Expiry: {data['expiry']})")
        print(f"  Call LTP    : {data['call_ltp']}")
        print(f"  Put  LTP    : {data['put_ltp']}")
        print(f"  Straddle    : {data['straddle_ltp']}")
        print(f"  India VIX   : {data['vix']}")
    else:
        print("Failed to fetch data — market may be closed or NSE is blocking.")
