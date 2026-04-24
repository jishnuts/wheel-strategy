"""
Layer 2 — Entry Logic
======================
Selects the optimal CSP or CC contract using Black-Scholes delta targeting.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional
import numpy as np
import yfinance as yf
from scipy.stats import norm
import src.config as cfg

logger = logging.getLogger(__name__)


@dataclass
class OptionCandidate:
    ticker:        str
    symbol:        str
    option_type:   str
    expiry:        date
    strike:        float
    dte:           int
    bid:           float
    ask:           float
    mid:           float
    delta:         float
    iv:            float
    open_interest: int
    is_valid:      bool
    reason:        str
    contracts:     int = 1


def _bs_delta(S, K, T, r, sigma, option_type="P"):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return float(norm.cdf(d1) - 1.0) if option_type == "P" else float(norm.cdf(d1))


def _to_alpaca_symbol(ticker, expiry, option_type, strike):
    return f"{ticker}{expiry.strftime('%y%m%d')}{option_type}{int(round(strike*1000)):08d}"


def _best_expiry(expirations):
    today = date.today()
    candidates = []
    for d_str in expirations:
        exp = date.fromisoformat(d_str)
        dte = (exp - today).days
        if cfg.MIN_DTE <= dte <= cfg.MAX_DTE:
            candidates.append((abs(dte - cfg.IDEAL_DTE), exp, dte))
    if not candidates:
        return None, 0
    candidates.sort()
    return candidates[0][1], candidates[0][2]


def _invalid(ticker, opt_type, msg):
    return OptionCandidate(ticker=ticker, symbol="", option_type=opt_type,
                           expiry=date.today(), strike=0.0, dte=0, bid=0.0, ask=0.0,
                           mid=0.0, delta=0.0, iv=0.0, open_interest=0,
                           is_valid=False, reason=msg)


def select_csp(ticker: str, price: float) -> OptionCandidate:
    try:
        stock = yf.Ticker(ticker)
        expiry, dte = _best_expiry(stock.options)
        if expiry is None:
            return _invalid(ticker, "P", f"No expiry in [{cfg.MIN_DTE},{cfg.MAX_DTE}] DTE window")
        puts = stock.option_chain(expiry.isoformat()).puts
        puts = puts[puts["openInterest"] >= cfg.MIN_OPEN_INTEREST]
        if puts.empty:
            return _invalid(ticker, "P", "No puts with sufficient OI")
        T = dte / 365.0; r = 0.045; best = None; best_dist = 999.0
        for _, row in puts.iterrows():
            strike = float(row["strike"]); iv = float(row.get("impliedVolatility", 0.30))
            bid = float(row.get("bid", 0.0)); ask = float(row.get("ask", 0.0))
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
            if mid <= 0 or bid <= 0: continue
            if (ask - bid) / mid > cfg.MAX_BID_ASK_SPREAD_PCT: continue
            delta = _bs_delta(price, strike, T, r, iv, "P")
            dist = abs(abs(delta) - cfg.CSP_TARGET_DELTA)
            if dist < best_dist:
                best_dist = dist
                best = dict(strike=strike, bid=bid, ask=ask, mid=mid, delta=delta,
                            iv=iv, oi=int(row.get("openInterest", 0)))
        if best is None:
            return _invalid(ticker, "P", "No valid put passed liquidity filters")
        return OptionCandidate(ticker=ticker, symbol=_to_alpaca_symbol(ticker, expiry, "P", best["strike"]),
                               option_type="P", expiry=expiry, strike=best["strike"], dte=dte,
                               bid=best["bid"], ask=best["ask"], mid=best["mid"], delta=best["delta"],
                               iv=best["iv"], open_interest=best["oi"], is_valid=True,
                               reason=f"delta={best['delta']:+.3f}  IV={best['iv']*100:.1f}%  mid=${best['mid']:.2f}  DTE={dte}")
    except Exception as exc:
        logger.error("CSP selection error for %s: %s", ticker, exc)
        return _invalid(ticker, "P", str(exc))


def select_cc(ticker: str, price: float, cost_basis: float) -> OptionCandidate:
    try:
        stock = yf.Ticker(ticker)
        expiry, dte = _best_expiry(stock.options)
        if expiry is None:
            return _invalid(ticker, "C", f"No expiry in [{cfg.MIN_DTE},{cfg.MAX_DTE}] DTE window")
        calls = stock.option_chain(expiry.isoformat()).calls
        calls = calls[(calls["strike"] >= cost_basis) & (calls["openInterest"] >= cfg.MIN_OPEN_INTEREST)]
        if calls.empty:
            return _invalid(ticker, "C", f"No calls >= cost_basis ${cost_basis:.2f} with sufficient OI")
        T = dte / 365.0; r = 0.045; best = None; best_dist = 999.0
        for _, row in calls.iterrows():
            strike = float(row["strike"]); iv = float(row.get("impliedVolatility", 0.30))
            bid = float(row.get("bid", 0.0)); ask = float(row.get("ask", 0.0))
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
            if mid <= 0 or bid <= 0: continue
            if (ask - bid) / mid > cfg.MAX_BID_ASK_SPREAD_PCT: continue
            delta = _bs_delta(price, strike, T, r, iv, "C")
            dist = abs(delta - cfg.CC_TARGET_DELTA)
            if dist < best_dist:
                best_dist = dist
                best = dict(strike=strike, bid=bid, ask=ask, mid=mid, delta=delta,
                            iv=iv, oi=int(row.get("openInterest", 0)))
        if best is None:
            return _invalid(ticker, "C", "No valid call passed liquidity filters")
        return OptionCandidate(ticker=ticker, symbol=_to_alpaca_symbol(ticker, expiry, "C", best["strike"]),
                               option_type="C", expiry=expiry, strike=best["strike"], dte=dte,
                               bid=best["bid"], ask=best["ask"], mid=best["mid"], delta=best["delta"],
                               iv=best["iv"], open_interest=best["oi"], is_valid=True,
                               reason=f"delta={best['delta']:+.3f}  IV={best['iv']*100:.1f}%  mid=${best['mid']:.2f}  strike>={cost_basis:.2f}  DTE={dte}")
    except Exception as exc:
        logger.error("CC selection error for %s: %s", ticker, exc)
        return _invalid(ticker, "C", str(exc))
