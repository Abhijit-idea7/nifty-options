"""
Live paper trading engine for the Nifty Straddle strategy.

Called every 5 minutes by GitHub Actions during market hours.
State (open positions, price history) is persisted in paper_trade_state.json
so each run picks up exactly where the last left off.

Outputs:
  paper_trade_state.json   — live state (position, history)
  paper_trade_log.csv      — completed trade log (appended each exit)
  paper_pnl_summary.txt    — running P&L summary printed to console
"""

import json
import os
import csv
from datetime import datetime, time as dtime
import pytz

from config import StrategyConfig
from nse_fetcher import get_atm_prices
from indicators import vix_pct_change

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
STATE_FILE   = "paper_trade_state.json"
LOG_FILE     = "paper_trade_log.csv"
SUMMARY_FILE = "paper_pnl_summary.txt"
IST          = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Config (same as backtest — edit here to change live settings)
# ---------------------------------------------------------------------------
cfg = StrategyConfig(
    signal_type           = os.getenv("SIGNAL_TYPE",  "orb"),
    trade_type            = os.getenv("TRADE_TYPE",   "straddle"),
    lot_size              = int(os.getenv("LOT_SIZE",  "75")),
    num_lots              = int(os.getenv("NUM_LOTS",   "1")),
    sl_pct                = float(os.getenv("SL_PCT",      "50")),
    target_pct            = float(os.getenv("TARGET_PCT",  "30")),
    orb_duration_minutes  = int(os.getenv("ORB_MINUTES",  "15")),
    use_vix_filter        = os.getenv("USE_VIX", "true").lower() == "true",
    vix_max_entry         = float(os.getenv("VIX_MAX",    "20.0")),
    vix_exit_rise_pct     = float(os.getenv("VIX_EXIT",   "10.0")),
    entry_after           = "09:30",
    square_off            = "15:15",
)

# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "position":        None,   # dict with trade details, or null
        "today_traded":    False,
        "today_date":      None,
        "price_history":   [],     # list of {timestamp, straddle, vix}
        "or_high":         None,
        "or_low":          None,
        "or_locked":       False,  # True once ORB window has closed
        "completed_trades": [],
    }


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# ORB calculation from intraday history
# ---------------------------------------------------------------------------

def compute_orb(history: list, orb_minutes: int, session_start_str: str,
                today_str: str) -> tuple:
    """Returns (or_high, or_low) or (None, None) if not enough data."""
    session_start = datetime.strptime(
        f"{today_str} {session_start_str}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=IST)
    cutoff = session_start.timestamp() + orb_minutes * 60

    or_bars = [h for h in history
               if datetime.fromisoformat(h["timestamp"]).timestamp() <= cutoff]
    if not or_bars:
        return None, None

    or_high = max(b["straddle"] for b in or_bars)
    or_low  = min(b["straddle"] for b in or_bars)
    return or_high, or_low


# ---------------------------------------------------------------------------
# Supertrend signal (lightweight, on history list)
# ---------------------------------------------------------------------------

def supertrend_signal(history: list, period: int = 7, mult: float = 3.0) -> int:
    """Returns -1 (bearish), +1 (bullish), 0 (not enough data)."""
    import pandas as pd
    from indicators import add_supertrend

    if len(history) < period + 5:
        return 0

    df = pd.DataFrame(history)
    df["straddle_open"]  = df["straddle"]
    df["straddle_high"]  = df["straddle"]
    df["straddle_low"]   = df["straddle"]
    df["straddle_close"] = df["straddle"]
    df = add_supertrend(df, period=period, multiplier=mult)

    last = df.iloc[-1]
    return int(last.get("supertrend_signal", 0))


# ---------------------------------------------------------------------------
# Signal logic
# ---------------------------------------------------------------------------

def get_signal(state: dict, straddle: float) -> int:
    """
    Returns -1 (enter short), +1 (exit / bullish on premium), 0 (no signal).
    """
    history = state["price_history"]

    if cfg.signal_type == "supertrend":
        return supertrend_signal(history, cfg.supertrend_period, cfg.supertrend_multiplier)

    if cfg.signal_type in ("orb", "both"):
        or_high = state.get("or_high")
        or_low  = state.get("or_low")
        if or_high is None or or_low is None:
            orb_sig = 0
        elif straddle < or_low:
            orb_sig = -1
        elif straddle > or_high:
            orb_sig = +1
        else:
            orb_sig = 0

        if cfg.signal_type == "orb":
            return orb_sig

        # "both" — need agreement
        st_sig = supertrend_signal(history, cfg.supertrend_period, cfg.supertrend_multiplier)
        if orb_sig == -1 and st_sig == -1:
            return -1
        if orb_sig == +1 or st_sig == +1:
            return +1
        return 0

    return 0


# ---------------------------------------------------------------------------
# Exit conditions
# ---------------------------------------------------------------------------

def check_exit(state: dict, straddle: float, vix: float,
               now: datetime) -> str | None:
    pos = state["position"]
    if pos is None:
        return None

    entry_straddle = pos["entry_straddle"]
    sl_level  = entry_straddle * (1 + cfg.sl_pct / 100)
    tgt_level = entry_straddle * (1 - cfg.target_pct / 100)

    if straddle >= sl_level:
        return "stop_loss"

    if straddle <= tgt_level:
        return "target"

    signal = get_signal(state, straddle)
    if signal == +1:
        return "signal_reversal"

    if cfg.use_vix_filter and vix:
        vix_chg = vix_pct_change(pos["entry_vix"], vix)
        if vix_chg > cfg.vix_exit_rise_pct:
            return "vix_spike"

    sq_time = dtime(*map(int, cfg.square_off.split(":")))
    if now.time() >= sq_time:
        return "eod_squareoff"

    return None


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_trade(pos: dict, exit_straddle: float, exit_time: str,
              reason: str, lots: int, lot_size: int) -> None:
    pnl = (pos["entry_straddle"] - exit_straddle) * lot_size * lots
    row = {
        "entry_time":     pos["entry_time"],
        "exit_time":      exit_time,
        "date":           pos["entry_time"][:10],
        "strike":         pos["strike"],
        "entry_straddle": pos["entry_straddle"],
        "exit_straddle":  round(exit_straddle, 2),
        "entry_vix":      pos["entry_vix"],
        "pnl_rs":         round(pnl, 2),
        "exit_reason":    reason,
        "lots":           lots,
    }
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
    print(f"  Trade logged: {reason}  P&L = ₹{pnl:+,.0f}")


def print_summary(state: dict) -> None:
    trades = state.get("completed_trades", [])
    if not trades:
        print("  No completed trades yet.")
        return
    total_pnl = sum(t["pnl"] for t in trades)
    wins   = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    summary = (
        f"\n{'='*50}\n"
        f"  PAPER TRADE SUMMARY\n"
        f"{'='*50}\n"
        f"  Total trades : {len(trades)}\n"
        f"  Winners      : {len(wins)}\n"
        f"  Losers       : {len(losses)}\n"
        f"  Win rate     : {len(wins)/len(trades)*100:.1f}%\n"
        f"  Total P&L    : ₹{total_pnl:+,.0f}\n"
        f"{'='*50}\n"
    )
    print(summary)
    with open(SUMMARY_FILE, "w") as f:
        f.write(summary)


# ---------------------------------------------------------------------------
# Main tick function (called every 5 minutes by GitHub Actions)
# ---------------------------------------------------------------------------

def run_tick() -> None:
    now = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")
    now_time  = now.time()

    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S IST')}] Paper trader tick")

    # Market hours guard: 09:15 – 15:30 IST, Mon–Fri
    if now.weekday() >= 5:
        print("  Weekend — skipping.")
        return
    market_open  = dtime(9, 15)
    market_close = dtime(15, 30)
    if not (market_open <= now_time <= market_close):
        print("  Outside market hours — skipping.")
        return

    # Load persistent state
    state = load_state()

    # New day reset
    if state["today_date"] != today_str:
        print(f"  New trading day: {today_str}")
        state["today_date"]   = today_str
        state["today_traded"] = False
        state["price_history"] = []
        state["or_high"]      = None
        state["or_low"]       = None
        state["or_locked"]    = False
        # Don't reset position — might have been missed exit (shouldn't happen)

    # Fetch live prices
    print("  Fetching live NSE data …")
    data = get_atm_prices()
    if data is None:
        print("  ❌ Could not fetch NSE data — will retry next tick.")
        save_state(state)
        return

    straddle = data["straddle_ltp"]
    vix      = data.get("vix") or 0.0
    print(f"  Spot: {data['spot']}  ATM: {data['atm_strike']}  "
          f"Straddle: {straddle:.1f}  VIX: {vix:.2f}")

    # Append to intraday price history
    state["price_history"].append({
        "timestamp": now.isoformat(),
        "straddle":  straddle,
        "vix":       vix,
    })

    # Lock ORB once window closes
    entry_after = dtime(*map(int, cfg.entry_after.split(":")))
    if not state["or_locked"] and now_time >= entry_after:
        or_h, or_l = compute_orb(
            state["price_history"], cfg.orb_duration_minutes,
            cfg.session_start, today_str
        )
        state["or_high"]   = or_h
        state["or_low"]    = or_l
        state["or_locked"] = True
        print(f"  ORB locked → High: {or_h:.1f}  Low: {or_l:.1f}")

    # ── Manage open position ──────────────────────────────────────────────
    if state["position"] is not None:
        reason = check_exit(state, straddle, vix, now)
        if reason:
            pos = state["position"]
            exit_straddle = straddle
            pnl = (pos["entry_straddle"] - exit_straddle) * cfg.lot_size * cfg.num_lots
            log_trade(pos, exit_straddle, now.isoformat(), reason,
                      cfg.num_lots, cfg.lot_size)
            state["completed_trades"].append({
                "date":   today_str,
                "pnl":    pnl,
                "reason": reason,
            })
            state["position"]    = None
            state["today_traded"] = True
        else:
            pos = state["position"]
            current_pnl = (pos["entry_straddle"] - straddle) * cfg.lot_size * cfg.num_lots
            print(f"  📊 OPEN POSITION  Entry: {pos['entry_straddle']:.1f}  "
                  f"Now: {straddle:.1f}  Unrealised P&L: ₹{current_pnl:+,.0f}")

    # ── Look for entry ────────────────────────────────────────────────────
    elif not state["today_traded"]:
        sq_time = dtime(*map(int, cfg.square_off.split(":")))
        too_late = now_time >= sq_time
        vix_ok   = (not cfg.use_vix_filter) or (vix <= cfg.vix_max_entry)
        after_orb = now_time >= entry_after and state["or_locked"]

        if too_late:
            print("  ⏰ Past square-off time — no new entries today.")
        elif not after_orb:
            print(f"  ⏳ Waiting for ORB window to close (after {cfg.entry_after})")
        elif not vix_ok:
            print(f"  🚫 VIX {vix:.1f} > {cfg.vix_max_entry} — skipping entry")
        else:
            signal = get_signal(state, straddle)
            print(f"  Signal: {signal}  (−1=short entry, +1=bullish, 0=neutral)")
            if signal == -1:
                state["position"] = {
                    "entry_time":     now.isoformat(),
                    "strike":         data["atm_strike"],
                    "entry_call":     data["call_ltp"],
                    "entry_put":      data["put_ltp"],
                    "entry_straddle": straddle,
                    "entry_vix":      vix,
                }
                print(f"  ✅ ENTERED SHORT STRADDLE @ {straddle:.1f}  "
                      f"Strike: {data['atm_strike']}  VIX: {vix:.1f}")
    else:
        print("  ✔ Already traded today — monitoring only.")

    print_summary(state)
    save_state(state)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run_tick()
