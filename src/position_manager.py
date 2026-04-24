"""
Layer 3 — Position Manager
===========================
Data model, state persistence, and decision engine for open positions.

Decision tree
  CSP open   → profit close | stop loss | time roll | assignment | expired worthless
  Assigned   → queue CC
  CC open    → profit close | time roll | called away | expired OTM
"""

from __future__ import annotations
import json, logging, os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Optional
import src.config as cfg

logger = logging.getLogger(__name__)


@dataclass
class OptionLeg:
    symbol:            str
    option_type:       str
    strike:            float
    expiry:            str
    dte_at_open:       int
    premium_received:  float
    contracts:         int
    opened_at:         str
    closed_at:         Optional[str]   = None
    premium_paid_back: Optional[float] = None


@dataclass
class WheelPosition:
    id:            str
    ticker:        str
    phase:         str
    cost_basis:    float
    shares:        int
    csp:           Optional[OptionLeg] = None
    cc:            Optional[OptionLeg] = None
    total_premium: float = 0.0
    opened_at:     str   = ""
    closed_at:     Optional[str] = None
    notes:         str   = ""

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, d):
        csp_raw = d.pop("csp", None); cc_raw = d.pop("cc", None)
        pos = cls(**d)
        pos.csp = OptionLeg(**csp_raw) if csp_raw else None
        pos.cc  = OptionLeg(**cc_raw)  if cc_raw  else None
        return pos


@dataclass
class WheelState:
    capital:                 float
    cash_reserved:           float
    total_premium_collected: float = 0.0
    completed_cycles:        int   = 0
    last_run:                str   = ""
    bot_start_date:          str   = ""
    positions:               list  = field(default_factory=list)
    closed_cycles:           list  = field(default_factory=list)

    @property
    def active_positions(self): return [p for p in self.positions if p.phase != "closed"]
    @property
    def open_slots(self):       return cfg.MAX_POSITIONS - len(self.active_positions)
    @property
    def free_capital(self):     return self.capital - self.cash_reserved

    def to_dict(self):
        return {"capital": self.capital, "cash_reserved": self.cash_reserved,
                "total_premium_collected": self.total_premium_collected,
                "completed_cycles": self.completed_cycles, "last_run": self.last_run,
                "bot_start_date": self.bot_start_date,
                "positions": [p.to_dict() for p in self.positions],
                "closed_cycles": [p.to_dict() for p in self.closed_cycles]}

    @classmethod
    def from_dict(cls, d):
        positions = [WheelPosition.from_dict(p) for p in d.get("positions", [])]
        closed    = [WheelPosition.from_dict(p) for p in d.get("closed_cycles", [])]
        return cls(capital=d["capital"], cash_reserved=d["cash_reserved"],
                   total_premium_collected=d.get("total_premium_collected", 0.0),
                   completed_cycles=d.get("completed_cycles", 0),
                   last_run=d.get("last_run", ""), bot_start_date=d.get("bot_start_date", ""),
                   positions=positions, closed_cycles=closed)


def load_state(path: str) -> WheelState:
    if not os.path.exists(path):
        today = date.today().isoformat()
        return WheelState(capital=cfg.TOTAL_CAPITAL, cash_reserved=0.0,
                          bot_start_date=today, last_run=today)
    with open(path) as f:
        return WheelState.from_dict(json.load(f))


def save_state(state: WheelState, path: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state.to_dict(), f, indent=2, default=str)
    os.replace(tmp, path)


@dataclass
class Action:
    position_id:       str
    action_type:       str
    symbol:            str
    reason:            str
    urgency:           str
    limit_price:       Optional[float] = None
    roll_from_symbol:  Optional[str]   = None


ACT_CLOSE_CSP        = "close_csp"
ACT_CLOSE_CSP_EXPIRE = "close_csp_expired"
ACT_MARK_ASSIGNED    = "mark_assigned"
ACT_ROLL_CSP         = "roll_csp"
ACT_OPEN_CC          = "open_cc"
ACT_CLOSE_CC         = "close_cc"
ACT_CLOSE_CC_EXPIRE  = "close_cc_expired_otm"
ACT_MARK_CALLED_AWAY = "mark_called_away"
ACT_ROLL_CC          = "roll_cc"


def evaluate_positions(state, current_prices, option_quotes) -> list[Action]:
    actions: list[Action] = []
    today = date.today()

    for pos in state.active_positions:
        price = current_prices.get(pos.ticker, 0.0)

        if pos.phase == "csp_open" and pos.csp:
            csp = pos.csp
            dte_now = (date.fromisoformat(csp.expiry) - today).days
            if dte_now <= 0:
                if price < csp.strike:
                    actions.append(Action(pos.id, ACT_MARK_ASSIGNED, csp.symbol,
                                          f"CSP expired ITM — ${price:.2f} < ${csp.strike:.2f}", "assignment"))
                else:
                    actions.append(Action(pos.id, ACT_CLOSE_CSP_EXPIRE, csp.symbol,
                                          "CSP expired OTM — full premium kept", "profit"))
                continue
            cur_val = option_quotes.get(csp.symbol)
            if cur_val is None: continue
            profit_pct = 1.0 - (cur_val / csp.premium_received)
            loss_mult  = cur_val / csp.premium_received
            if profit_pct >= cfg.PROFIT_TARGET_PCT:
                actions.append(Action(pos.id, ACT_CLOSE_CSP, csp.symbol,
                                      f"Profit target — {profit_pct*100:.0f}% captured", "profit",
                                      limit_price=round(cur_val * 1.05, 2)))
            elif loss_mult >= cfg.STOP_LOSS_MULTIPLIER:
                actions.append(Action(pos.id, ACT_CLOSE_CSP, csp.symbol,
                                      f"Stop loss — {loss_mult:.1f}x premium", "stop",
                                      limit_price=round(cur_val * 1.10, 2)))
            elif dte_now <= cfg.ROLL_DTE_THRESHOLD and profit_pct < cfg.PROFIT_TARGET_PCT:
                actions.append(Action(pos.id, ACT_ROLL_CSP, csp.symbol,
                                      f"Time roll — DTE={dte_now}", "time",
                                      roll_from_symbol=csp.symbol))

        elif pos.phase == "assigned":
            actions.append(Action(pos.id, ACT_OPEN_CC, pos.ticker,
                                  f"Assigned {pos.shares}sh at ${pos.cost_basis:.2f}", "assignment"))

        elif pos.phase == "cc_open" and pos.cc:
            cc = pos.cc
            dte_now = (date.fromisoformat(cc.expiry) - today).days
            if dte_now <= 0:
                if price > cc.strike:
                    actions.append(Action(pos.id, ACT_MARK_CALLED_AWAY, cc.symbol,
                                          f"CC expired ITM — shares called away at ${cc.strike:.2f}", "profit"))
                else:
                    actions.append(Action(pos.id, ACT_CLOSE_CC_EXPIRE, cc.symbol,
                                          "CC expired OTM — premium kept", "profit"))
                continue
            cur_val = option_quotes.get(cc.symbol)
            if cur_val is None: continue
            profit_pct = 1.0 - (cur_val / cc.premium_received)
            if profit_pct >= cfg.PROFIT_TARGET_PCT:
                actions.append(Action(pos.id, ACT_CLOSE_CC, cc.symbol,
                                      f"CC profit target — {profit_pct*100:.0f}% captured", "profit",
                                      limit_price=round(cur_val * 1.05, 2)))
            elif dte_now <= cfg.ROLL_DTE_THRESHOLD and profit_pct < cfg.PROFIT_TARGET_PCT:
                actions.append(Action(pos.id, ACT_ROLL_CC, cc.symbol,
                                      f"CC time roll — DTE={dte_now}", "time",
                                      roll_from_symbol=cc.symbol))
    return actions
