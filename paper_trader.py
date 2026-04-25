"""
Live paper trading engine — runs every 5 minutes via GitHub Actions.

Schedule:
  Monday    → trade Nifty  1DTE (expires Tuesday)
  Wednesday → trade Sensex 1DTE (expires Thursday)
  All other weekdays → skip (no trade)

State persisted in paper_trade_state.json so each 5-min tick
picks up exactly where the last left off.

Outputs committed back to repo:
  paper_trade_state.json   — live position + intraday history
  paper_trade_log.csv      — completed trades (appended on exit)
  paper_pnl_summary.txt    — running P&L summary
"""

import json
import os
import csv
from datetime import datetime, time as dtime
import pytz

from config import StrategyConfig
from instruments import NIFTY, SENSEX, WEEKDAY_TO_INSTRUMENT, DAY_NAMES
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
# Strategy config — env vars let GitHub Actions override any parameter
# ---------------------------------------------------------------------------
cfg = StrategyConfig(
    signal_type                = os.getenv("SIGNAL_TYPE",       "orb"),
    trade_type                 = os.getenv("TRADE_TYPE",        "straddle"),
    num_lots                   = int(os.getenv("NUM_LOTS",       "1")),
    sl_pct                     = float(os.getenv("SL_PCT",       "80")),
    target_pct                 = float(os.getenv("TARGET_PCT",   "50")),
    orb_duration_minutes       = int(os.getenv("ORB_MINUTES",    "10")),
    use_vix_filter             = os.getenv("USE_VIX",  "true").lower() == "true",
    vix_max_entry              = float(os.getenv("VIX_MAX",      "20.0")),
    vix_exit_rise_pct          = float(os.getenv("VIX_EXIT",     "10.0")),
    early_exit_time            = os.getenv("EARLY_EXIT_TIME",    "14:00"),
    early_exit_min_profit_pct  = float(os.getenv("EARLY_EXIT_PCT", "25")),
    entry_after                = "09:25",
    square_off                 = "15:15",
)

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "position":         None,
        "today_traded":     False,
        "today_date":       None,
        "today_instrument": None,
        "price_history":    [],
        "or_high":          None,
        "or_low":           None,
        "or_locked":        False,
        "completed_trades": [],
    }


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# ORB on intraday price history
# ---------------------------------------------------------------------------

def lock_orb(history: list, orb_minutes: int,
             session_start: str, today_str: str) -> tuple:
    t0  = datetime.strptime(f"{today_str} {session_start}", "%Y-%m-%d %H:%M")
    t0  = IST.localize(t0)
    cut = t0.timestamp() + orb_minutes * 60

    or_bars = [h for h in history
               if datetime.fromisoformat(h["timestamp"]).timestamp() <= cut]
    if not or_bars:
        return None, None

    return (max(b["straddle"] for b in or_bars),
            min(b["straddle"] for b in or_bars))


# ---------------------------------------------------------------------------
# Supertrend signal from history
# ---------------------------------------------------------------------------

def supertrend_signal_from_history(history: list,
                                   period: int = 7,
                                   mult: float = 3.0) -> int:
    import pandas as pd
    from indicators import add_supertrend

    if len(history) < period + 5:
        return 0
    df = pd.DataFrame(history)
    for col in ["straddle_open", "straddle_high", "straddle_low", "straddle_close"]:
        df[col] = df["straddle"]
    df = add_supertrend(df, period=period, multiplier=mult)
    return int(df.iloc[-1].get("supertrend_signal", 0))


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------

def get_signal(state: dict, straddle: float) -> int:
    if cfg.signal_type == "supertrend":
        return supertrend_signal_from_history(
            state["price_history"],
            cfg.supertrend_period,
            cfg.supertrend_multiplier,
        )

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

    # "both"
    st_sig = supertrend_signal_from_history(
        state["price_history"],
        cfg.supertrend_period,
        cfg.supertrend_multiplier,
    )
    if orb_sig == -1 and st_sig == -1:
        return -1
    if orb_sig == +1 or st_sig == +1:
        return +1
    return 0


# ---------------------------------------------------------------------------
# Exit check
# ---------------------------------------------------------------------------

def check_exit(state: dict, straddle: float, vix: float,
               now: datetime) -> str | None:
    pos = state["position"]
    if pos is None:
        return None

    entry = pos["entry_straddle"]

    if straddle >= entry * (1 + cfg.sl_pct / 100):
        return "stop_loss"
    if straddle <= entry * (1 - cfg.target_pct / 100):
        return "target"
    if get_signal(state, straddle) == +1:
        return "signal_reversal"

    if cfg.use_vix_filter and vix:
        if vix_pct_change(pos["entry_vix"], vix) > cfg.vix_exit_rise_pct:
            return "vix_spike"

    # Early profit exit
    if cfg.early_exit_time < cfg.square_off:
        early_t = dtime(*map(int, cfg.early_exit_time.split(":")))
        if now.time() >= early_t:
            profit_pct = (entry - straddle) / entry * 100
            if profit_pct >= cfg.early_exit_min_profit_pct:
                return "early_profit_exit"

    if now.time() >= dtime(*map(int, cfg.square_off.split(":"))):
        return "eod_squareoff"

    return None


# ---------------------------------------------------------------------------
# Trade logging
# ---------------------------------------------------------------------------

def log_trade(pos: dict, inst, exit_straddle: float,
              exit_time: str, reason: str) -> float:
    pnl = (pos["entry_straddle"] - exit_straddle) * inst.lot_size * cfg.num_lots
    row = {
        "entry_time":     pos["entry_time"],
        "exit_time":      exit_time,
        "date":           pos["entry_time"][:10],
        "symbol":         inst.symbol,
        "exchange":       inst.exchange,
        "strike":         pos["strike"],
        "entry_straddle": pos["entry_straddle"],
        "exit_straddle":  round(exit_straddle, 2),
        "entry_vix":      pos["entry_vix"],
        "pnl_rs":         round(pnl, 2),
        "exit_reason":    reason,
        "lots":           cfg.num_lots,
        "lot_size":       inst.lot_size,
    }
    exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=row.keys())
        if not exists:
            w.writeheader()
        w.writerow(row)
    print(f"  Trade logged: {reason}  |  P&L = ₹{pnl:+,.0f}")
    return pnl


def write_summary(state: dict) -> None:
    trades = state.get("completed_trades", [])
    if not trades:
        msg = "  No completed trades yet.\n"
    else:
        total   = sum(t["pnl"] for t in trades)
        wins    = sum(1 for t in trades if t["pnl"] > 0)
        by_sym  = {}
        for t in trades:
            s = t.get("symbol", "?")
            by_sym.setdefault(s, []).append(t["pnl"])

        lines = [
            "=" * 50,
            "  PAPER TRADE SUMMARY",
            "=" * 50,
            f"  Total trades : {len(trades)}",
            f"  Winners      : {wins}  |  Losers: {len(trades)-wins}",
            f"  Win rate     : {wins/len(trades)*100:.1f}%",
            f"  Total P&L    : ₹{total:+,.0f}",
            "",
            "  By instrument:",
        ]
        for sym, pnls in by_sym.items():
            w = sum(1 for p in pnls if p > 0)
            lines.append(f"    {sym:<8} {len(pnls)} trades | "
                         f"Win {w/len(pnls)*100:.0f}% | ₹{sum(pnls):+,.0f}")
        lines.append("=" * 50)
        msg = "\n".join(lines) + "\n"

    print(msg)
    with open(SUMMARY_FILE, "w") as f:
        f.write(msg)


# ---------------------------------------------------------------------------
# Main tick
# ---------------------------------------------------------------------------

def run_tick() -> None:
    now       = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")
    weekday   = now.weekday()   # 0=Mon 1=Tue 2=Wed 3=Thu 4=Fri

    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S IST')}]  "
          f"Paper trader tick — {DAY_NAMES[weekday] if weekday < 5 else 'Weekend'}")

    # Weekend guard
    if weekday >= 5:
        print("  Weekend — skipping.")
        return

    # Instrument routing: Mon=Nifty, Wed=Sensex, others=skip
    inst = WEEKDAY_TO_INSTRUMENT.get(weekday)
    if inst is None:
        print(f"  {DAY_NAMES[weekday]} is not a 1DTE trading day — skipping.")
        print(f"  (Trade days: Monday=Nifty 1DTE, Wednesday=Sensex 1DTE)")
        return

    # Market hours guard
    market_open  = dtime(9, 15)
    market_close = dtime(15, 30)
    if not (market_open <= now.time() <= market_close):
        print(f"  Outside market hours — skipping.  (Trading {inst.symbol} today)")
        return

    print(f"  Instrument today: {inst.symbol} ({inst.exchange})  "
          f"[expires {inst.expiry_day_name}]")

    # Load state
    state = load_state()

    # New day reset
    if state["today_date"] != today_str:
        print(f"  New day: {today_str}")
        state.update({
            "today_date":       today_str,
            "today_instrument": inst.symbol,
            "today_traded":     False,
            "price_history":    [],
            "or_high":          None,
            "or_low":           None,
            "or_locked":        False,
        })
        # Safety: close any stale position from previous day
        if state["position"] is not None:
            print("  WARNING: Stale position found — force-closing.")
            state["position"] = None

    # Fetch live prices
    print(f"  Fetching {inst.symbol} ATM prices from {inst.exchange} …")
    data = get_atm_prices(inst)
    if data is None:
        print("  ❌ Fetch failed — will retry next tick.")
        save_state(state)
        return

    straddle = data["straddle_ltp"]
    vix      = data.get("vix") or 0.0
    print(f"  Spot: {data['spot']:,.0f}  |  ATM: {data['atm_strike']}  |  "
          f"Straddle: {straddle:.1f}  |  VIX: {vix:.2f}")

    # Append to intraday history
    state["price_history"].append({
        "timestamp": now.isoformat(),
        "straddle":  straddle,
        "vix":       vix,
    })

    # Lock ORB after window closes
    entry_after = dtime(*map(int, cfg.entry_after.split(":")))
    if not state["or_locked"] and now.time() >= entry_after:
        orh, orl = lock_orb(
            state["price_history"],
            cfg.orb_duration_minutes,
            cfg.session_start,
            today_str,
        )
        state["or_high"]   = orh
        state["or_low"]    = orl
        state["or_locked"] = True
        print(f"  ORB locked → High: {orh:.1f}  Low: {orl:.1f}")

    # ── Manage open position ─────────────────────────────────────────────
    if state["position"] is not None:
        reason = check_exit(state, straddle, vix, now)
        if reason:
            pos = state["position"]
            pnl = log_trade(pos, inst, straddle, now.isoformat(), reason)
            state["completed_trades"].append({
                "date":   today_str,
                "symbol": inst.symbol,
                "pnl":    pnl,
                "reason": reason,
            })
            state["position"]    = None
            state["today_traded"] = True
        else:
            pos        = state["position"]
            cur_pnl    = (pos["entry_straddle"] - straddle) * inst.lot_size * cfg.num_lots
            print(f"  📊 OPEN {inst.symbol}  Entry: {pos['entry_straddle']:.1f}  "
                  f"Now: {straddle:.1f}  Unrealised: ₹{cur_pnl:+,.0f}")

    # ── Look for entry ───────────────────────────────────────────────────
    elif not state["today_traded"]:
        sq_time  = dtime(*map(int, cfg.square_off.split(":")))
        too_late = now.time() >= sq_time
        vix_ok   = (not cfg.use_vix_filter) or (vix <= cfg.vix_max_entry)
        orb_rdy  = now.time() >= entry_after and state["or_locked"]

        if too_late:
            print("  ⏰ Past square-off — no new entry today.")
        elif not orb_rdy:
            print(f"  ⏳ Waiting for ORB to lock (after {cfg.entry_after})")
        elif not vix_ok:
            print(f"  🚫 VIX {vix:.1f} > {cfg.vix_max_entry} — skipping entry")
        else:
            sig = get_signal(state, straddle)
            print(f"  Signal: {sig}  (−1=enter short, +1=bullish/exit, 0=wait)")
            if sig == -1:
                state["position"] = {
                    "entry_time":     now.isoformat(),
                    "strike":         data["atm_strike"],
                    "entry_call":     data["call_ltp"],
                    "entry_put":      data["put_ltp"],
                    "entry_straddle": straddle,
                    "entry_vix":      vix,
                }
                print(f"  ✅ ENTERED SHORT {inst.symbol} STRADDLE @ {straddle:.1f}  "
                      f"Strike: {data['atm_strike']}  VIX: {vix:.1f}")
    else:
        print("  ✔ Already traded today — monitoring complete.")

    write_summary(state)
    save_state(state)


if __name__ == "__main__":
    run_tick()
