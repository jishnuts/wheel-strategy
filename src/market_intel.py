"""
Layer 1 — Market Intelligence
==============================
Scores every ticker in the watchlist and returns a ranked list of candidates.

Scoring model
  score = 0.40 x IV_rank_norm  +  0.35 x trend_norm  +  0.25 x earnings_safety_norm
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd
import yfinance as yf
import src.config as cfg

logger = logging.getLogger(__name__)


@dataclass
class TickerScore:
    ticker:        str
    price:         float
    iv_rank:       float
    trend:         float
    earnings_safe: bool
    days_to_earn:  int
    vix:           float
    score:         float
    reason:        str


def _get_iv_rank(ticker: str) -> float:
    try:
        hist = yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=True)
        if len(hist) < 35:
            return 0.0
        closes  = hist["Close"].squeeze()
        log_ret = np.log(closes / closes.shift(1)).dropna()
        rv30    = log_ret.rolling(30).std() * np.sqrt(252) * 100
        rv30    = rv30.dropna()
        current = float(rv30.iloc[-1])
        low, high = float(rv30.min()), float(rv30.max())
        if high == low:
            return 50.0
        return round((current - low) / (high - low) * 100, 1)
    except Exception as exc:
        logger.debug("IV rank failed for %s: %s", ticker, exc)
        return 0.0


def _get_trend(ticker: str) -> float:
    try:
        hist = yf.download(ticker, period="3mo", interval="1d", progress=False, auto_adjust=True)
        if len(hist) < cfg.EMA_LONG:
            return 0.0
        closes = hist["Close"].squeeze()
        ema_s  = float(closes.ewm(span=cfg.EMA_SHORT, adjust=False).mean().iloc[-1])
        ema_l  = float(closes.ewm(span=cfg.EMA_LONG,  adjust=False).mean().iloc[-1])
        diff   = (ema_s - ema_l) / ema_l
        if diff >  0.01: return  1.0
        if diff < -0.01: return -1.0
        return 0.0
    except Exception as exc:
        logger.debug("Trend failed for %s: %s", ticker, exc)
        return 0.0


def _days_to_earnings(ticker: str) -> int:
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            stock = yf.Ticker(ticker)
            dates = stock.earnings_dates
        if dates is None or dates.empty:
            return 999
        today  = pd.Timestamp.today().normalize()
        future = dates[dates.index > today]
        if future.empty:
            return 999
        return int((future.index[0].date() - today.date()).days)
    except Exception as exc:
        logger.debug("Earnings check for %s: %s", ticker, exc)
        return 999


def get_vix() -> float:
    try:
        vix = yf.download("^VIX", period="2d", interval="1d", progress=False, auto_adjust=True)
        return float(vix["Close"].squeeze().iloc[-1])
    except Exception:
        return 20.0


def _latest_price(ticker: str) -> Optional[float]:
    try:
        hist = yf.download(ticker, period="3d", interval="1d", progress=False, auto_adjust=True)
        return None if hist.empty else float(hist["Close"].squeeze().iloc[-1])
    except Exception:
        return None


def score_ticker(ticker: str, vix: float) -> Optional[TickerScore]:
    price = _latest_price(ticker)
    if price is None:
        return None
    if price < cfg.MIN_STOCK_PRICE:
        return TickerScore(ticker, price, 0, 0, False, 0, vix, 0.0,
                           f"Price ${price:.2f} below minimum")

    iv_rank     = _get_iv_rank(ticker)
    trend       = _get_trend(ticker)
    days_earn   = _days_to_earnings(ticker)
    earnings_ok = days_earn > cfg.EARNINGS_BUFFER_DAYS

    if vix > cfg.MAX_VIX:
        return TickerScore(ticker, price, iv_rank, trend, earnings_ok, days_earn, vix,
                           0.0, f"VIX {vix:.1f} > max {cfg.MAX_VIX}")
    if not earnings_ok:
        return TickerScore(ticker, price, iv_rank, trend, earnings_ok, days_earn, vix,
                           0.0, f"Earnings in {days_earn}d")
    if trend < 0:
        return TickerScore(ticker, price, iv_rank, trend, earnings_ok, days_earn, vix,
                           0.0, "Bearish trend (EMA20 < EMA50 by >1%) — skip")
    if iv_rank < cfg.MIN_IV_RANK:
        return TickerScore(ticker, price, iv_rank, trend, earnings_ok, days_earn, vix,
                           0.0, f"IV Rank {iv_rank:.0f} < min {cfg.MIN_IV_RANK:.0f}")

    trend_norm = (trend + 1) / 2
    earn_norm  = min(days_earn / 90, 1.0)
    iv_norm    = iv_rank / 100
    score = (cfg.SCORE_WEIGHT_IV_RANK * iv_norm
           + cfg.SCORE_WEIGHT_TREND   * trend_norm
           + cfg.SCORE_WEIGHT_SAFETY  * earn_norm)

    trend_label = {1.0: "bull", 0.0: "neutral", -1.0: "bear"}.get(trend, "?")
    reason = f"IVR={iv_rank:.0f}  trend={trend_label}  earn={days_earn}d  score={score:.4f}"
    return TickerScore(ticker, price, iv_rank, trend, earnings_ok, days_earn, vix,
                       round(score, 4), reason)


def rank_watchlist(watchlist: list[str]) -> list[TickerScore]:
    vix = get_vix()
    logger.info("VIX = %.1f", vix)
    results: list[TickerScore] = []
    for ticker in watchlist:
        ts = score_ticker(ticker, vix)
        if ts is None:
            logger.warning("  %s: data fetch failed — skipped", ticker)
            continue
        icon = "OK" if ts.score > 0 else "FAIL"
        logger.info("  %s %s: %s", icon, ticker, ts.reason)
        results.append(ts)
    qualified = sorted([r for r in results if r.score > 0], key=lambda x: x.score, reverse=True)
    logger.info("Qualified: %d / %d", len(qualified), len(watchlist))
    return qualified
