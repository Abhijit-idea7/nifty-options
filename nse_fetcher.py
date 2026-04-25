"""
Live option chain fetchers for NSE (Nifty) and BSE (Sensex).

Both are free — no API key required — but both need a browser-like
session with valid cookies from the exchange homepage.

Usage:
  from nse_fetcher import get_atm_prices
  from instruments import NIFTY, SENSEX

  data = get_atm_prices(NIFTY)   # Monday
  data = get_atm_prices(SENSEX)  # Wednesday
"""

import time
import requests
from datetime import datetime, timedelta
from instruments import InstrumentConfig


# ---------------------------------------------------------------------------
# Shared session builder
# ---------------------------------------------------------------------------

def _make_session(home_url: str, warmup_url: str) -> requests.Session:
    """Create a session with exchange cookies to avoid bot-detection blocks."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept": "*/*",
        "Connection": "keep-alive",
    })
    try:
        session.get(home_url,   timeout=10)
        time.sleep(0.8)
        session.get(warmup_url, timeout=10)
        time.sleep(0.8)
    except Exception:
        pass
    return session


# ---------------------------------------------------------------------------
# India VIX  (always from NSE — used as vol proxy for both indices)
# ---------------------------------------------------------------------------

def fetch_india_vix() -> float | None:
    session = _make_session(
        "https://www.nseindia.com",
        "https://www.nseindia.com/market-data/live-market-indices",
    )
    try:
        resp = session.get(
            "https://www.nseindia.com/api/allIndices", timeout=15
        )
        resp.raise_for_status()
        for item in resp.json().get("data", []):
            if item.get("index") == "INDIA VIX":
                return float(item["last"])
    except Exception as e:
        print(f"[fetcher] VIX fetch failed: {e}")
    return None


# ---------------------------------------------------------------------------
# NSE — Nifty option chain
# ---------------------------------------------------------------------------

def _fetch_nifty_chain() -> dict | None:
    session = _make_session(
        "https://www.nseindia.com",
        "https://www.nseindia.com/option-chain",
    )
    try:
        resp = session.get(
            "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[fetcher] NSE Nifty chain failed: {e}")
        return None


def _parse_nse_chain(raw: dict, inst: InstrumentConfig) -> dict | None:
    """Extract ATM call + put from NSE option chain JSON."""
    spot   = float(raw["records"]["underlyingValue"])
    atm    = round(spot / inst.strike_step) * inst.strike_step
    expiry = raw["records"]["expiryDates"][0]   # nearest weekly

    call_data = put_data = None
    for row in raw.get("filtered", {}).get("data", []):
        if row.get("expiryDate") == expiry and row.get("strikePrice") == atm:
            call_data = row.get("CE", {})
            put_data  = row.get("PE", {})
            break

    if not call_data or not put_data:
        print(f"[fetcher] NSE: ATM strike {atm} not found")
        return None

    def s(d, k):
        v = d.get(k, 0)
        return float(v) if v else 0.0

    c_ltp = s(call_data, "lastPrice")
    p_ltp = s(put_data,  "lastPrice")
    return {
        "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol":       inst.symbol,
        "exchange":     inst.exchange,
        "spot":         spot,
        "expiry":       expiry,
        "atm_strike":   atm,
        "call_ltp":     c_ltp,
        "call_bid":     s(call_data, "bidPrice"),
        "call_ask":     s(call_data, "askPrice"),
        "call_iv":      s(call_data, "impliedVolatility"),
        "put_ltp":      p_ltp,
        "put_bid":      s(put_data,  "bidPrice"),
        "put_ask":      s(put_data,  "askPrice"),
        "put_iv":       s(put_data,  "impliedVolatility"),
        "straddle_ltp": round(c_ltp + p_ltp, 2),
        "vix":          fetch_india_vix(),
        "lot_size":     inst.lot_size,
    }


# ---------------------------------------------------------------------------
# BSE — Sensex option chain
# ---------------------------------------------------------------------------

def _next_thursday() -> str:
    """Return next Thursday's date as YYYYMMDD (BSE expiry date format)."""
    today = datetime.now()
    days_ahead = (3 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return (today + timedelta(days=days_ahead)).strftime("%Y%m%d")


def _fetch_sensex_chain() -> dict | None:
    expiry = _next_thursday()
    url = (
        f"https://api.bseindia.com/BseIndiaAPI/api/GetIndexOptionChain/w"
        f"?index=SENSEX&expdt={expiry}&strikeprice="
    )
    session = _make_session(
        "https://www.bseindia.com",
        "https://www.bseindia.com/markets/Derivatives/DerivativesHome.aspx",
    )
    session.headers.update({"Referer": "https://www.bseindia.com/"})
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[fetcher] BSE Sensex chain failed: {e}")
        return None


def _parse_bse_chain(raw: dict, inst: InstrumentConfig) -> dict | None:
    """Extract ATM call + put from BSE option chain JSON."""
    try:
        spot_str = raw.get("CurrentIndexValue") or raw.get("indexvalue", "0")
        spot = float(str(spot_str).replace(",", ""))
        atm  = round(spot / inst.strike_step) * inst.strike_step

        c_ltp = p_ltp = 0.0
        c_bid = c_ask = p_bid = p_ask = 0.0
        c_iv  = p_iv  = 0.0

        rows = raw.get("OptionChainDetails", [])
        for row in rows:
            strike = float(str(row.get("StrikePrice", "0")).replace(",", ""))
            if abs(strike - atm) < 1:
                opt_type = str(row.get("OptionType", "")).upper()
                ltp_val  = float(str(row.get("LTP", "0")).replace(",", "") or "0")
                bid_val  = float(str(row.get("BidRate", "0")).replace(",", "") or "0")
                ask_val  = float(str(row.get("OfferRate", "0")).replace(",", "") or "0")
                iv_val   = float(str(row.get("IV", "0")).replace(",", "") or "0")
                if "CE" in opt_type or opt_type == "C":
                    c_ltp, c_bid, c_ask, c_iv = ltp_val, bid_val, ask_val, iv_val
                elif "PE" in opt_type or opt_type == "P":
                    p_ltp, p_bid, p_ask, p_iv = ltp_val, bid_val, ask_val, iv_val

        if c_ltp == 0 and p_ltp == 0:
            print(f"[fetcher] BSE: ATM {atm} prices are zero — market may be closed")
            return None

        return {
            "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol":       inst.symbol,
            "exchange":     inst.exchange,
            "spot":         spot,
            "expiry":       _next_thursday(),
            "atm_strike":   atm,
            "call_ltp":     c_ltp,
            "call_bid":     c_bid,
            "call_ask":     c_ask,
            "call_iv":      c_iv,
            "put_ltp":      p_ltp,
            "put_bid":      p_bid,
            "put_ask":      p_ask,
            "put_iv":       p_iv,
            "straddle_ltp": round(c_ltp + p_ltp, 2),
            "vix":          fetch_india_vix(),
            "lot_size":     inst.lot_size,
        }
    except Exception as e:
        print(f"[fetcher] BSE chain parse error: {e}")
        return None


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------

def get_atm_prices(inst: InstrumentConfig) -> dict | None:
    """
    Fetch live ATM straddle prices for the given instrument.
    Routes to NSE for Nifty, BSE for Sensex automatically.
    Returns None on failure (network issue / market closed).
    """
    if inst.exchange == "NSE":
        raw = _fetch_nifty_chain()
        return _parse_nse_chain(raw, inst) if raw else None
    elif inst.exchange == "BSE":
        raw = _fetch_sensex_chain()
        return _parse_bse_chain(raw, inst) if raw else None
    else:
        print(f"[fetcher] Unknown exchange: {inst.exchange}")
        return None


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from instruments import NIFTY, SENSEX

    for inst in [NIFTY, SENSEX]:
        print(f"\nFetching {inst.symbol} ({inst.exchange}) …")
        data = get_atm_prices(inst)
        if data:
            print(f"  Spot      : {data['spot']}")
            print(f"  ATM Strike: {data['atm_strike']}  (Expiry: {data['expiry']})")
            print(f"  Call LTP  : {data['call_ltp']}")
            print(f"  Put LTP   : {data['put_ltp']}")
            print(f"  Straddle  : {data['straddle_ltp']}")
            print(f"  India VIX : {data['vix']}")
        else:
            print(f"  Failed — market may be closed or exchange API is blocking.")
