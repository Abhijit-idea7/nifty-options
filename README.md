# Nifty Weekly Options — Straddle/Strangle Backtest

Day-trading strategy that sells ATM Nifty straddles (or strangles), uses
ORB or Supertrend signals on the combined premium, and applies an India VIX
filter for risk management.

---

## How to run the backtest on GitHub (no coding required)

### Step 1 — Upload this folder to a new GitHub repository
1. Go to [github.com](https://github.com) → **New repository**
2. Give it a name (e.g. `nifty-straddle-backtest`) → click **Create repository**
3. Upload all files from this folder:
   - Click **uploading an existing file** on the repo page
   - Drag-and-drop the entire folder contents → click **Commit changes**

### Step 2 — Run the backtest
1. In your GitHub repo, click the **Actions** tab (top menu)
2. On the left, click **"Run Nifty Straddle Backtest"**
3. Click the **"Run workflow"** button (grey dropdown, top-right of the list)
4. A form appears — fill in your strategy settings (or leave defaults):

| Setting | What it means | Default |
|---|---|---|
| Signal method | `orb` = Opening Range Breakout, `supertrend`, or `both` | `orb` |
| Trade structure | `straddle` = same strike, `strangle` = OTM legs | `straddle` |
| Stop-loss % | Exit if premium rises this much from entry | `50` |
| Target % | Exit if premium falls this much from entry | `30` |
| ORB window | Minutes to calculate opening range | `15` |
| VIX filter | Skip entry / force exit on VIX spikes | `true` |
| Max VIX for entry | Don't enter new trades above this VIX level | `20` |
| VIX exit spike % | Exit if VIX rises this % from your entry VIX | `10` |
| Number of lots | How many Nifty lots to simulate | `1` |

5. Click the green **"Run workflow"** button

### Step 3 — Download results
1. Wait ~2 minutes for the run to finish (green tick ✓)
2. Click the finished run name
3. Scroll down to **Artifacts** → click **"backtest-results"** to download a ZIP
4. The ZIP contains:
   - `backtest_results.png` — equity curve, daily P&L bars, exit reason chart
   - `trade_log.csv` — every trade with entry/exit prices and P&L
   - `metrics_summary.csv` — win rate, Sharpe ratio, max drawdown, etc.

---

## Strategy Logic

```
EVERY TRADING DAY:

1. Identify ATM strike at market open (nearest 50-point multiple)
2. Build synthetic straddle premium = Call price + Put price (minute bars)
3. Apply indicator on straddle premium:
   - ORB: Calculate opening range (first 15 min). Entry when premium
     breaks BELOW range low (premium contracting = good for short seller)
   - Supertrend: Entry when straddle price goes below Supertrend line

4. ENTRY (all must be true):
   ✓ After 09:30 (ORB window complete)
   ✓ Signal is bearish on straddle premium
   ✓ India VIX below threshold (default 20)
   ✓ No open position today

5. SHORT STRADDLE: Sell ATM Call + Sell ATM Put
   Collect premium = Call price + Put price at entry

6. EXIT (first condition wins):
   A. Stop-loss     — straddle rises 50% from entry (VIX spike / big move)
   B. Target        — straddle falls 30% from entry (time decay + low vol)
   C. Signal flip   — straddle price turns bullish (premium expanding)
   D. VIX spike     — India VIX rises >10% from entry level
   E. EOD square-off — forced exit at 15:15 regardless

7. P&L = (Entry premium − Exit premium) × Lot size × Number of lots
```

---

## Understanding the signals

**ORB (Opening Range Breakout) on straddle premium:**
- If the straddle premium is falling below the morning low → market is
  calm, IV is dropping → good time to be short premium
- If the premium breaks above the morning high → volatility expanding →
  exit immediately

**Supertrend on straddle premium:**
- Works like Supertrend on a stock price chart, but applied to the
  combined option premium
- Below the Supertrend line = premium in downtrend = short-friendly
- Above the Supertrend line = premium rising = exit signal

**India VIX filter:**
- High VIX = expensive options + uncertain market = risky to be short
- VIX > 20 at entry → skip the trade entirely
- VIX spikes 10% during the trade → exit immediately to protect P&L

---

## File structure

```
├── run_backtest.py          ← main runner (called by GitHub Actions)
├── config.py                ← all strategy parameters
├── backtest_engine.py       ← trade simulation loop
├── strategy.py              ← entry/exit decision logic
├── indicators.py            ← ORB and Supertrend calculations
├── data_loader.py           ← loads CSV data or generates sample data
├── report.py                ← charts and performance metrics
├── requirements.txt         ← Python libraries needed
├── data/
│   └── sample_data_generator.py   ← creates synthetic test data
└── .github/
    └── workflows/
        └── run_backtest.yml        ← GitHub Actions workflow
```

---

## Using real data (future step)

Replace `data/sample_nifty_options_data.csv` with real historical data
from your broker. The CSV must have these columns:

```
datetime, nifty_spot, strike,
call_open, call_high, call_low, call_close,
put_open,  put_high,  put_low,  put_close,
vix
```

Data providers that export this format: Zerodha Kite, Upstox, Fyers,
Quantsapp, Opstra, NSE Bhav Copy (with some pre-processing).
