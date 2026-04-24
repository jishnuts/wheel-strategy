"""
Layer 5 — Reporting
====================
P&L metrics, daily summary, and snapshot persistence.
"""

from __future__ import annotations
import json, logging, os
from datetime import date
import src.config as cfg
from src.position_manager import WheelState

logger = logging.getLogger(__name__)


def annualised_yield(total_premium, capital, days):
    if capital <= 0 or days <= 0: return 0.0
    return (total_premium / capital) * (365.0 / days) * 100

def win_rate(state: WheelState) -> float:
    cycles = state.closed_cycles
    if not cycles: return 0.0
    return sum(1 for c in cycles if c.total_premium >= 0) / len(cycles) * 100

def avg_premium_per_cycle(state: WheelState) -> float:
    if not state.closed_cycles: return 0.0
    return sum(c.total_premium for c in state.closed_cycles) / len(state.closed_cycles)


def format_summary(state: WheelState) -> str:
    today = date.today()
    start = state.bot_start_date or today.isoformat()
    try:
        days = max((today - date.fromisoformat(start[:10])).days, 1)
    except Exception:
        days = 1

    initial = cfg.TOTAL_CAPITAL; current = state.capital; pnl = current - initial
    ay = annualised_yield(state.total_premium_collected, initial, days)
    wr = win_rate(state); avg_p = avg_premium_per_cycle(state)
    SEP = "+" + "-"*58 + "+"; SEP2 = "+" + "="*58 + "+"
    def row(lbl, val): return f"|  {lbl:<22}: {val:<34}|"

    lines = [SEP2, f"|  WHEEL STRATEGY  --  {today}".ljust(59)+"|", SEP2,
             row("Starting Capital", f"${initial:>10,.2f}"),
             row("Current Capital",  f"${current:>10,.2f}"),
             row("Total P&L",        f"${pnl:>+10,.2f}"),
             row("Premium Collected",f"${state.total_premium_collected:>10,.2f}"),
             row("Annualised Yield", f"{ay:>9.1f}%"),
             row("Completed Cycles", f"{state.completed_cycles:>10}"),
             row("Win Rate",         f"{wr:>9.1f}%"),
             row("Avg Cycle Premium",f"${avg_p:>10,.2f}"),
             row("Cash Reserved",    f"${state.cash_reserved:>10,.2f}"),
             row("Free Capital",     f"${state.free_capital:>10,.2f}"),
             row("Days Running",     f"{days:>10}"),
             SEP, f"|  OPEN POSITIONS ({len(state.active_positions)}/{cfg.MAX_POSITIONS})".ljust(59)+"|", SEP]

    if state.active_positions:
        for pos in state.active_positions:
            if pos.phase == "csp_open" and pos.csp:
                dte = (date.fromisoformat(pos.csp.expiry) - today).days
                lines.append(f"|  [{pos.ticker:6}] CSP  K=${pos.csp.strike:<7.1f} exp={pos.csp.expiry}  DTE={dte:>3}  prem=${pos.csp.premium_received*100:.0f}".ljust(59)+"|")
            elif pos.phase == "cc_open" and pos.cc:
                dte = (date.fromisoformat(pos.cc.expiry) - today).days
                lines.append(f"|  [{pos.ticker:6}] CC   K=${pos.cc.strike:<7.1f} exp={pos.cc.expiry}  DTE={dte:>3}  prem=${pos.cc.premium_received*100:.0f}".ljust(59)+"|")
            elif pos.phase == "assigned":
                lines.append(f"|  [{pos.ticker:6}] ASSIGNED  {pos.shares}sh  basis=${pos.cost_basis:.2f}".ljust(59)+"|")
    else:
        lines.append(f"|  (none)".ljust(59)+"|")

    if state.closed_cycles:
        lines += [SEP, f"|  RECENT CLOSED CYCLES".ljust(59)+"|", SEP]
        for pos in list(reversed(state.closed_cycles))[:5]:
            lines.append(f"|  [{pos.ticker:6}]  premium=${pos.total_premium:>8.2f}  closed={(pos.closed_at or '')[:10]}".ljust(59)+"|")
    lines.append(SEP2)
    return "\n".join(lines)


def save_daily_snapshot(state: WheelState, results_dir: str) -> None:
    os.makedirs(results_dir, exist_ok=True)
    today = date.today().isoformat()
    start = state.bot_start_date or today
    try:
        days = max((date.today() - date.fromisoformat(start[:10])).days, 1)
    except Exception:
        days = 1

    snap = {"date": today, "capital": round(state.capital, 2),
            "cash_reserved": round(state.cash_reserved, 2),
            "free_capital": round(state.free_capital, 2),
            "total_pnl": round(state.capital - cfg.TOTAL_CAPITAL, 2),
            "total_premium_collected": round(state.total_premium_collected, 2),
            "annualised_yield_pct": round(annualised_yield(state.total_premium_collected, cfg.TOTAL_CAPITAL, days), 2),
            "completed_cycles": state.completed_cycles,
            "win_rate_pct": round(win_rate(state), 1),
            "open_positions": len(state.active_positions),
            "open_slots": state.open_slots}

    with open(os.path.join(results_dir, f"snapshot_{today}.json"), "w") as f:
        json.dump(snap, f, indent=2)

    summary = format_summary(state)
    print(summary)
    with open(os.path.join(results_dir, "daily_log.txt"), "a") as f:
        f.write(summary + "\n\n")
    logger.info("Snapshot saved -> results/snapshot_%s.json", today)
