"""
Layer 4 — Execution
====================
Translates Actions from Layer 3 into Alpaca limit orders and updates WheelState.

Rules
  * All option orders use day limit orders (never market)
  * STO: limit slightly inside bid for fast fill
  * BTC: limit slightly above ask to guarantee exit
  * State saved atomically after each successful order
"""

from __future__ import annotations
import logging, os, uuid
from datetime import date, datetime
from typing import Optional
import src.config as cfg
from src.entry_logic import OptionCandidate, select_cc, select_csp
from src.position_manager import (
    ACT_CLOSE_CC, ACT_CLOSE_CC_EXPIRE, ACT_CLOSE_CSP, ACT_CLOSE_CSP_EXPIRE,
    ACT_MARK_ASSIGNED, ACT_MARK_CALLED_AWAY, ACT_OPEN_CC, ACT_ROLL_CC, ACT_ROLL_CSP,
    Action, OptionLeg, WheelPosition, WheelState, save_state,
)

logger = logging.getLogger(__name__)


def _trading_client():
    from alpaca.trading.client import TradingClient
    return TradingClient(api_key=os.environ["ALPACA_API_KEY"],
                         secret_key=os.environ["ALPACA_SECRET_KEY"],
                         paper=cfg.PAPER_MODE)

def _data_client():
    from alpaca.data.historical.option import OptionHistoricalDataClient
    return OptionHistoricalDataClient(api_key=os.environ["ALPACA_API_KEY"],
                                      secret_key=os.environ["ALPACA_SECRET_KEY"])


def get_option_quotes(symbols: list[str]) -> dict[str, float]:
    if not symbols: return {}
    quotes: dict[str, float] = {}
    try:
        from alpaca.data.requests import OptionLatestQuoteRequest
        resp = _data_client().get_option_latest_quote(
            OptionLatestQuoteRequest(symbol_or_symbols=symbols))
        for sym, q in resp.items():
            if q.bid_price and q.ask_price:
                quotes[sym] = round((q.bid_price + q.ask_price) / 2, 2)
    except Exception as exc:
        logger.warning("Option quote fetch failed: %s", exc)
    return quotes


def get_all_open_symbols(state: WheelState) -> list[str]:
    syms: list[str] = []
    for pos in state.active_positions:
        if pos.csp and not pos.csp.closed_at: syms.append(pos.csp.symbol)
        if pos.cc  and not pos.cc.closed_at:  syms.append(pos.cc.symbol)
    return syms


def _sto(client, candidate: OptionCandidate, dry_run: bool) -> Optional[dict]:
    lp = round(max(candidate.bid * 0.99, candidate.mid * 0.95), 2)
    logger.info("  STO %s  qty=1  limit=$%.2f", candidate.symbol, lp)
    if dry_run:
        return {"id": "dry_run_sto", "status": "dry_run", "filled_price": lp}
    try:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest
        order = client.submit_order(LimitOrderRequest(
            symbol=candidate.symbol, qty=candidate.contracts,
            side=OrderSide.SELL, type="limit",
            time_in_force=TimeInForce.DAY, limit_price=lp))
        logger.info("  OK STO %s  status=%s", order.id, order.status)
        return {"id": str(order.id), "status": str(order.status), "filled_price": lp}
    except Exception as exc:
        logger.error("  FAIL STO: %s", exc)
        return None


def _btc(client, symbol: str, qty: int, limit_price: float, dry_run: bool) -> Optional[dict]:
    logger.info("  BTC %s  qty=%d  limit=$%.2f", symbol, qty, limit_price)
    if dry_run:
        return {"id": "dry_run_btc", "status": "dry_run", "filled_price": limit_price}
    try:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest
        order = client.submit_order(LimitOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.BUY, type="limit",
            time_in_force=TimeInForce.DAY, limit_price=limit_price))
        logger.info("  OK BTC %s  status=%s", order.id, order.status)
        return {"id": str(order.id), "status": str(order.status), "filled_price": limit_price}
    except Exception as exc:
        logger.error("  FAIL BTC: %s", exc)
        return None


def execute_actions(state, actions, current_prices, option_quotes, state_path, dry_run=False):
    client = _trading_client()
    today_str = datetime.now().isoformat()
    pos_map = {p.id: p for p in state.positions}

    for action in actions:
        pos = pos_map.get(action.position_id)
        if pos is None:
            logger.warning("Unknown position %s — skip", action.position_id); continue
        ticker = pos.ticker; price = current_prices.get(ticker, 0.0)
        logger.info("\n>> %s | %s | %s", action.action_type, ticker, action.reason)

        if action.action_type == ACT_CLOSE_CSP and pos.csp:
            result = _btc(client, pos.csp.symbol, pos.csp.contracts, action.limit_price, dry_run)
            if result:
                net = (pos.csp.premium_received - action.limit_price) * 100 * pos.csp.contracts
                pos.csp.closed_at = today_str; pos.csp.premium_paid_back = action.limit_price
                pos.total_premium += net; state.total_premium_collected += net; state.capital += net
                state.cash_reserved = max(0.0, state.cash_reserved - pos.csp.strike * 100 * pos.csp.contracts)
                _close_position(state, pos, today_str)

        elif action.action_type == ACT_CLOSE_CSP_EXPIRE and pos.csp:
            premium = pos.csp.premium_received * 100 * pos.csp.contracts
            pos.total_premium += premium; state.total_premium_collected += premium; state.capital += premium
            state.cash_reserved = max(0.0, state.cash_reserved - pos.csp.strike * 100 * pos.csp.contracts)
            pos.csp.closed_at = today_str; _close_position(state, pos, today_str)
            logger.info("  OK Expired worthless — kept $%.2f", premium)

        elif action.action_type == ACT_MARK_ASSIGNED and pos.csp:
            state.cash_reserved = max(0.0, state.cash_reserved - pos.csp.strike * 100 * pos.csp.contracts)
            pos.shares = 100 * pos.csp.contracts; pos.cost_basis = pos.csp.strike; pos.phase = "assigned"
            logger.info("  OK Assigned — %d sh of %s @$%.2f", pos.shares, ticker, pos.cost_basis)

        elif action.action_type == ACT_ROLL_CSP and pos.csp:
            cur_val = option_quotes.get(pos.csp.symbol, pos.csp.premium_received * 0.5)
            btc_res = _btc(client, pos.csp.symbol, pos.csp.contracts, round(cur_val * 1.08, 2), dry_run)
            if not btc_res: continue
            new_csp = select_csp(ticker, price)
            if not new_csp.is_valid: logger.warning("  Roll: %s", new_csp.reason); continue
            sto_res = _sto(client, new_csp, dry_run)
            if not sto_res: continue
            old_coll = pos.csp.strike * 100 * pos.csp.contracts
            pos.csp = OptionLeg(symbol=new_csp.symbol, option_type="P", strike=new_csp.strike,
                                expiry=new_csp.expiry.isoformat(), dte_at_open=new_csp.dte,
                                premium_received=new_csp.mid, contracts=1, opened_at=today_str)
            pos.cost_basis = new_csp.strike
            state.cash_reserved = max(0.0, state.cash_reserved - old_coll) + new_csp.strike * 100

        elif action.action_type == ACT_OPEN_CC:
            cc_cand = select_cc(ticker, price, pos.cost_basis)
            if not cc_cand.is_valid: logger.warning("  CC selection failed: %s", cc_cand.reason); continue
            result = _sto(client, cc_cand, dry_run)
            if not result: continue
            pos.cc = OptionLeg(symbol=cc_cand.symbol, option_type="C", strike=cc_cand.strike,
                               expiry=cc_cand.expiry.isoformat(), dte_at_open=cc_cand.dte,
                               premium_received=cc_cand.mid, contracts=1, opened_at=today_str)
            premium_usd = cc_cand.mid * 100
            pos.total_premium += premium_usd; state.total_premium_collected += premium_usd
            state.capital += premium_usd; pos.phase = "cc_open"

        elif action.action_type == ACT_CLOSE_CC and pos.cc:
            result = _btc(client, pos.cc.symbol, 1, action.limit_price, dry_run)
            if result:
                net = (pos.cc.premium_received - action.limit_price) * 100
                pos.total_premium += net; state.total_premium_collected += net; state.capital += net
                pos.cc.closed_at = today_str; pos.phase = "assigned"

        elif action.action_type == ACT_CLOSE_CC_EXPIRE and pos.cc:
            premium = pos.cc.premium_received * 100
            pos.total_premium += premium; state.total_premium_collected += premium
            state.capital += premium; pos.cc.closed_at = today_str; pos.phase = "assigned"

        elif action.action_type == ACT_MARK_CALLED_AWAY and pos.cc:
            share_pl = (pos.cc.strike - pos.cost_basis) * pos.shares
            state.capital += share_pl; pos.cc.closed_at = today_str
            _close_position(state, pos, today_str)

        elif action.action_type == ACT_ROLL_CC and pos.cc:
            cur_val = option_quotes.get(pos.cc.symbol, pos.cc.premium_received * 0.5)
            btc_res = _btc(client, pos.cc.symbol, 1, round(cur_val * 1.08, 2), dry_run)
            if not btc_res: continue
            new_cc = select_cc(ticker, price, pos.cost_basis)
            if not new_cc.is_valid: logger.warning("  Roll CC: %s", new_cc.reason); continue
            sto_res = _sto(client, new_cc, dry_run)
            if not sto_res: continue
            pos.cc = OptionLeg(symbol=new_cc.symbol, option_type="C", strike=new_cc.strike,
                               expiry=new_cc.expiry.isoformat(), dte_at_open=new_cc.dte,
                               premium_received=new_cc.mid, contracts=1, opened_at=today_str)

        save_state(state, state_path)
    return state


def _close_position(state, pos, closed_at):
    pos.phase = "closed"; pos.closed_at = closed_at; state.completed_cycles += 1
    if pos in state.positions: state.positions.remove(pos)
    state.closed_cycles.append(pos)


def open_new_wheels(state, ranked, current_prices, state_path, dry_run=False):
    client = _trading_client()
    today_str = datetime.now().isoformat()
    occupied = {p.ticker for p in state.active_positions}
    opened = 0

    for scored in ranked:
        if state.open_slots <= 0: break
        ticker = scored.ticker; price = current_prices.get(ticker, scored.price)
        if ticker in occupied:
            logger.info("  %s: already in portfolio", ticker); continue
        est_collateral = price * 0.90 * 100
        if state.free_capital < est_collateral:
            logger.warning("  %s: need ~$%.0f, only $%.0f free", ticker, est_collateral, state.free_capital)
            continue
        logger.info("\n>> Opening new CSP on %s (score=%.4f)", ticker, scored.score)
        csp = select_csp(ticker, price)
        if not csp.is_valid: logger.warning("  CSP failed: %s", csp.reason); continue
        collateral = csp.strike * 100 * csp.contracts
        if state.free_capital < collateral:
            logger.warning("  %s: collateral $%.0f > free $%.0f", ticker, collateral, state.free_capital); continue
        result = _sto(client, csp, dry_run)
        if not result: continue
        pos_id = f"wheel_{ticker}_{date.today().strftime('%Y%m%d')}_{str(uuid.uuid4())[:4]}"
        premium = csp.mid * 100 * csp.contracts
        pos = WheelPosition(id=pos_id, ticker=ticker, phase="csp_open", cost_basis=csp.strike, shares=0,
                            csp=OptionLeg(symbol=csp.symbol, option_type="P", strike=csp.strike,
                                          expiry=csp.expiry.isoformat(), dte_at_open=csp.dte,
                                          premium_received=csp.mid, contracts=csp.contracts, opened_at=today_str),
                            total_premium=premium, opened_at=today_str)
        state.positions.append(pos)
        state.cash_reserved += collateral; state.capital += premium
        state.total_premium_collected += premium; occupied.add(ticker); opened += 1
        logger.info("  OK %s  strike=$%.2f  premium=$%.2f  collateral=$%.0f",
                    csp.symbol, csp.strike, premium, collateral)
        save_state(state, state_path)

    logger.info("New wheels opened: %d", opened)
    return state
