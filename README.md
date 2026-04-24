# Wheel Strategy Bot

An automated **options Wheel Strategy** bot that:

- Sells **Cash-Secured Puts (CSP)** on quality tickers with elevated IV
- On assignment, holds shares and sells **Covered Calls (CC)** above cost basis
- Closes at **50% profit**, stops out at **2× loss**, rolls when **≤ 21 DTE**
- Runs automatically every weekday via **GitHub Actions**
- Emails a daily summary with equity chart after every run

> **Paper trading only by default.** Powered by [Alpaca Markets](https://alpaca.markets).

---

## How the Wheel works

```
Sell CSP
  ├─ Expired OTM  → keep full premium → sell new CSP
  └─ Assigned     → hold 100 shares
       └─ Sell CC (strike ≥ cost basis)
            ├─ Expired OTM  → keep premium → sell new CC
            └─ Called away  → cycle complete ✓  → sell new CSP
```

---

## Architecture (5 layers)

| Layer | File | Role |
|-------|------|------|
| 0 | `src/config.py` | All tunable parameters |
| 1 | `src/market_intel.py` | Score & rank watchlist (IV Rank, trend, earnings) |
| 2 | `src/entry_logic.py` | Select strike/expiry via Black-Scholes delta |
| 3 | `src/position_manager.py` | Decision brain — when to close, roll, assign |
| 4 | `src/execution.py` | Submit limit orders to Alpaca |
| 5 | `src/reporting.py` | P&L metrics, daily snapshots, equity chart |
| — | `src/strategy.py` | Orchestrator — wires all layers |
| — | `src/send_email.py` | Daily email notifier (Gmail SMTP) |

---

## Decision Rules

### CSP (Cash-Secured Put)

| Condition | Action |
|-----------|--------|
| IV Rank > 30, no earnings within 14d, not bearish | Open new CSP |
| Option worth ≤ 50% of premium received | Close for profit |
| Option worth ≥ 2× premium received | Stop loss — close |
| DTE ≤ 21 and not at profit target | Roll to next expiry |
| Expired OTM | Full premium kept |
| Expired ITM | Mark assigned → immediately queue CC |

### CC (Covered Call)

| Condition | Action |
|-----------|--------|
| Assignment detected | Sell CC at or above cost basis |
| Option worth ≤ 50% of premium | Close for profit, sell new CC |
| DTE ≤ 21 | Roll forward |
| Expired OTM | Premium kept, sell new CC |
| Expired ITM | Shares called away — cycle complete ✓ |

---

## Scoring Model

Every ticker in the watchlist is scored each run:

```
score = 0.40 × IV_rank        (52-week RV30 percentile — premium richness)
      + 0.35 × trend          (EMA20 vs EMA50 direction)
      + 0.25 × earnings_safe  (days until next earnings)
```

**Hard disqualifiers:** bearish EMA trend · VIX > 35 · earnings within 14 days · IV Rank < 30

---

## Key Parameters (`src/config.py`)

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `TOTAL_CAPITAL` | 50,000 | Account size (USD) |
| `MAX_POSITIONS` | 3 | Max concurrent wheel positions |
| `CSP_TARGET_DELTA` | 0.25 | Target delta for new puts (~25Δ) |
| `CC_TARGET_DELTA` | 0.30 | Target delta for covered calls (~30Δ) |
| `MIN_DTE` / `MAX_DTE` | 30 / 45 | Expiry entry window |
| `PROFIT_TARGET_PCT` | 0.50 | Close when 50% of max profit captured |
| `STOP_LOSS_MULTIPLIER` | 2.0 | Exit when option = 2× premium received |
| `ROLL_DTE_THRESHOLD` | 21 | Roll when DTE falls below this |
| `MIN_IV_RANK` | 30 | Skip tickers with low IV |
| `MAX_VIX` | 35 | Pause new entries during extreme fear |

---

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/jishnuts/wheel-strategy.git
cd wheel-strategy
pip install -r requirements.txt
```

### 2. Set credentials

```bash
cp .env.template .env   # Windows: copy .env.template .env
```

Edit `.env`:

```
ALPACA_API_KEY=your_paper_api_key
ALPACA_SECRET_KEY=your_paper_secret_key
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
GMAIL_TO=you@gmail.com
```

> **Options trading must be enabled** on your Alpaca paper account.  
> Alpaca Dashboard → Paper Account → Options Trading → Request Approval (Level 1)

### 3. Dry run (no orders submitted)

```bash
python -m src.strategy --dry-run
```

Prints the full decision output, scored tickers, and what orders *would* be placed.

### 4. Run normally (paper mode)

```bash
python -m src.strategy
```

### 5. Windows auto-scheduler (Mon–Fri 9:45 AM ET)

```
setup_scheduler.bat   # run as Administrator once
```

---

## GitHub Actions (automated daily run)

The workflow `.github/workflows/wheel-bot.yml` triggers at **9:45 AM ET** every weekday.  
`state.json` and `results/` are committed back after each run so positions persist.

### Required Secrets (Settings → Secrets → Actions)

| Secret | Value |
|--------|-------|
| `ALPACA_PAPER_KEY` | Alpaca paper API key |
| `ALPACA_PAPER_SECRET` | Alpaca paper secret key |
| `GMAIL_USER` | Gmail sender address |
| `GMAIL_APP_PASSWORD` | Gmail App Password (16 chars, no spaces) |
| `GMAIL_TO` | Recipient email |

---

## Email Notification

After every run the bot emails:

- Portfolio P&L vs starting capital
- Total premium collected + annualised yield
- Open positions table (ticker · strike · DTE · premium received)
- Recent closed cycles with P&L
- **Equity curve chart** (PNG attachment)

---

## Watchlist

Default tickers (edit `WATCHLIST` in `src/config.py`):

| Tier | Tickers | Rationale |
|------|---------|-----------|
| 1 — High IV | TSLA, AMD, NVDA, COIN | Elevated premium, liquid options chains |
| 2 — Blue-chip | AAPL, MSFT, META, GOOGL | Tight spreads, reliable trend signals |
| ETFs | SPY, QQQ | Highest liquidity, moderate IV |

---

## Project Structure

```
wheel-strategy/
├── src/
│   ├── config.py             # All parameters
│   ├── market_intel.py       # Layer 1 — watchlist scoring
│   ├── entry_logic.py        # Layer 2 — strike/expiry selection
│   ├── position_manager.py   # Layer 3 — decision engine + state I/O
│   ├── execution.py          # Layer 4 — Alpaca order submission
│   ├── reporting.py          # Layer 5 — P&L + daily snapshots
│   ├── strategy.py           # Orchestrator
│   └── send_email.py         # Gmail daily summary
├── .github/
│   └── workflows/
│       └── wheel-bot.yml     # Weekday cron schedule
├── results/                  # Snapshots + logs (auto-committed by bot)
├── state.json                # Live position state (auto-committed by bot)
├── requirements.txt
├── .env.template
├── run_daily.bat             # Windows manual runner
└── setup_scheduler.bat       # Windows Task Scheduler setup
```

---

## Disclaimer

Paper trading bot for educational purposes. Options trading involves significant risk of loss. Past paper performance does not guarantee live results. Not financial advice.
