"""
Wheel Strategy Bot — Configuration
All tunable parameters in one place. Edit here, not in the logic files.
"""

# ── Capital & Position Sizing ──────────────────────────────────────────────────
TOTAL_CAPITAL          = 50_000      # Total account capital in USD
MAX_POSITIONS          = 3           # Max concurrent wheel positions
CAPITAL_PER_WHEEL      = TOTAL_CAPITAL / MAX_POSITIONS   # ~$16,667 per wheel

# ── Watchlist ──────────────────────────────────────────────────────────────────
WATCHLIST: list[str] = [
    # Tier 1 — high IV, liquid options, earnings-driven premium
    "TSLA", "AMD", "NVDA", "COIN",
    # Tier 2 — stable blue-chips, tighter spreads
    "AAPL", "MSFT", "META", "GOOGL",
    # ETFs — very liquid, moderate IV
    "SPY", "QQQ",
]

# ── Entry Gate Filters ────────────────────────────────────────────────────────
MIN_IV_RANK            = 30.0
MAX_VIX                = 35.0
EARNINGS_BUFFER_DAYS   = 14
MIN_OPEN_INTEREST      = 100
MAX_BID_ASK_SPREAD_PCT = 0.15
MIN_STOCK_PRICE        = 10.0

# ── Strike & Expiration Selection ────────────────────────────────────────────
CSP_TARGET_DELTA       = 0.25
CC_TARGET_DELTA        = 0.30
MIN_DTE                = 30
MAX_DTE                = 45
IDEAL_DTE              = 38

# ── Position Management ───────────────────────────────────────────────────────
PROFIT_TARGET_PCT      = 0.50
STOP_LOSS_MULTIPLIER   = 2.00
ROLL_DTE_THRESHOLD     = 21

# ── Trend Filter ──────────────────────────────────────────────────────────────
EMA_SHORT              = 20
EMA_LONG               = 50
REQUIRE_BULL_TREND     = False

# ── Alpaca ────────────────────────────────────────────────────────────────────
PAPER_MODE             = True
PAPER_BASE_URL         = "https://paper-api.alpaca.markets"
LIVE_BASE_URL          = "https://api.alpaca.markets"

# ── Scoring weights (must sum to 1.0) ────────────────────────────────────────
SCORE_WEIGHT_IV_RANK   = 0.40
SCORE_WEIGHT_TREND     = 0.35
SCORE_WEIGHT_SAFETY    = 0.25
