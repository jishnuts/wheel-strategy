"""
Wheel Strategy — Main Orchestrator
====================================
Run order
  1  Load state
  2  Fetch prices for all tickers
  3  Fetch live option quotes for open legs
  4  Evaluate open positions -> action list
  5  Execute position-management actions
  6  Score watchlist for new entries
  7  Select contracts + open new CSPs
  8  Save state
  9  Save daily report + send email

Usage
  python -m src.strategy              # normal paper run
  python -m src.strategy --dry-run    # log orders but don't submit
"""

from __future__ import annotations
import argparse, logging, sys
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import yfinance as yf
from dotenv import load_dotenv
import src.config as cfg
from src.execution import execute_actions, get_all_open_symbols, get_option_quotes, open_new_wheels
from src.market_intel import rank_watchlist
from src.position_manager import load_state, save_state
from src.reporting import save_daily_snapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%H:%M:%S", handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)


def fetch_prices(tickers: list[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for t in tickers:
        try:
            h = yf.download(t, period="3d", interval="1d", progress=False, auto_adjust=True)
            if not h.empty:
                prices[t] = float(h["Close"].squeeze().iloc[-1])
        except Exception as exc:
            logger.warning("Price fetch failed for %s: %s", t, exc)
    return prices


def run_daily(state_path="state.json", results_dir="results", dry_run=False):
    load_dotenv()
    mode_label = "DRY-RUN" if dry_run else ("PAPER" if cfg.PAPER_MODE else "LIVE")
    logger.info("=" * 60)
    logger.info("  WHEEL STRATEGY BOT  --  %s  [%s]",
                datetime.now().strftime("%Y-%m-%d %H:%M"), mode_label)
    logger.info("=" * 60)

    state = load_state(state_path)
    logger.info("State: %d position(s) | capital=$%.2f | reserved=$%.2f | free=$%.2f",
                len(state.active_positions), state.capital, state.cash_reserved, state.free_capital)

    all_tickers = list(set(cfg.WATCHLIST + [p.ticker for p in state.active_positions]))
    logger.info("Fetching prices for %d tickers...", len(all_tickers))
    prices = fetch_prices(all_tickers)
    logger.info("Prices ready: %d / %d", len(prices), len(all_tickers))

    open_syms = get_all_open_symbols(state)
    option_quotes = get_option_quotes(open_syms) if open_syms else {}
    if open_syms:
        logger.info("Quotes received: %d / %d", len(option_quotes), len(open_syms))

    active_state_path = (state_path + ".dryrun") if dry_run else state_path

    from src.position_manager import evaluate_positions
    logger.info("\n--- Evaluating open positions ---")
    actions = evaluate_positions(state, prices, option_quotes)
    if actions:
        logger.info("%d action(s):", len(actions))
        for a in actions:
            logger.info("  * %-22s  %s  -- %s", a.action_type, a.symbol, a.reason)
        state = execute_actions(state, actions, prices, option_quotes, active_state_path, dry_run)
    else:
        logger.info("No position management actions needed.")

    if state.open_slots > 0:
        logger.info("\n--- Scanning watchlist for %d new slot(s) ---", state.open_slots)
        ranked = rank_watchlist(cfg.WATCHLIST)
        if ranked:
            logger.info("Top candidates:")
            for ts in ranked[:5]:
                logger.info("  %s  score=%.4f  %s", ts.ticker, ts.score, ts.reason)
            state = open_new_wheels(state, ranked, prices, active_state_path, dry_run)
        else:
            logger.info("No qualified candidates today.")
    else:
        logger.info("\n--- All %d slots filled -- no new entries ---", cfg.MAX_POSITIONS)

    state.last_run = datetime.now().isoformat()
    save_state(state, active_state_path)
    logger.info("State saved -> %s", active_state_path)

    save_daily_snapshot(state, results_dir)

    from src.send_email import send_daily_email
    send_daily_email(state, results_dir, mode_label=mode_label)

    logger.info("\n[OK] Daily run complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wheel Strategy Bot")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--state",   default="state.json")
    parser.add_argument("--results", default="results")
    args = parser.parse_args()
    run_daily(state_path=args.state, results_dir=args.results, dry_run=args.dry_run)
