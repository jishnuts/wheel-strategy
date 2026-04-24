"""
Wheel Strategy — Daily Email Notifier
=======================================
Sends a post-run summary email via Gmail SMTP after each bot run.

Environment variables (set as GitHub Secrets or in .env):
    GMAIL_USER         — sender Gmail address
    GMAIL_APP_PASSWORD — Gmail App Password (16-char)
    GMAIL_TO           — recipient (defaults to GMAIL_USER if unset)

Never raises — email failures are logged as warnings.
"""

from __future__ import annotations
import io, json, logging, os, smtplib
from datetime import date, datetime
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional
import src.config as cfg
from src.position_manager import WheelState

logger = logging.getLogger(__name__)
ACCOUNT_URL = "github.com/jishnuts/wheel-strategy/actions"
W = 58


def _build_chart(results_dir: str) -> Optional[bytes]:
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker
    except ImportError:
        return None

    p = Path(results_dir)
    snap_files = sorted(p.glob("snapshot_*.json")) if p.exists() else []
    if not snap_files: return None

    dates: list[datetime] = []; capital: list[float] = []; premium: list[float] = []
    for sf in snap_files:
        with open(sf) as f: s = json.load(f)
        try:
            dates.append(datetime.strptime(s["date"], "%Y-%m-%d"))
            capital.append(s["capital"])
            premium.append(s.get("total_premium_collected", 0.0))
        except (KeyError, ValueError): continue
    if not dates: return None

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6), facecolor="white",
                                    gridspec_kw={"height_ratios": [3, 1.2], "hspace": 0.35})
    initial = cfg.TOTAL_CAPITAL
    ax1.axhline(initial, color="#BDBDBD", linestyle="--", linewidth=1.2,
                label=f"Start ${initial:,.0f}", zorder=2)
    ax1.fill_between(dates, capital, initial, where=[v >= initial for v in capital],
                     alpha=0.18, color="#43A047", interpolate=True)
    ax1.fill_between(dates, capital, initial, where=[v < initial for v in capital],
                     alpha=0.18, color="#E53935", interpolate=True)
    ax1.plot(dates, capital, color="#1565C0", linewidth=2.2, label="Capital", zorder=3)
    ax1.plot(dates, capital, "o", color="#1565C0", markersize=4, zorder=4)
    if capital:
        last_val = capital[-1]
        colour = "#2E7D32" if last_val >= initial else "#C62828"
        ax1.annotate(f"  ${last_val:,.0f}", xy=(dates[-1], last_val),
                     fontsize=10, fontweight="bold", color=colour, va="center")
    ax1.set_title("Wheel Strategy — Paper Account", fontsize=13, fontweight="bold", pad=10)
    ax1.set_ylabel("Capital (USD)", fontsize=9)
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax1.legend(loc="upper left", fontsize=8, framealpha=0.7)
    ax1.grid(True, alpha=0.2, linestyle="--"); ax1.set_axisbelow(True)
    ax1.spines[["top", "right"]].set_visible(False)

    bar_colours = ["#43A047" if p >= 0 else "#E53935" for p in premium]
    ax2.bar(dates, premium, color=bar_colours, width=0.8, alpha=0.85)
    ax2.set_title("Cumulative Premium Collected", fontsize=10, pad=6)
    ax2.set_ylabel("USD", fontsize=9)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax2.grid(True, alpha=0.2, linestyle="--", axis="y")
    ax2.set_axisbelow(True); ax2.spines[["top", "right"]].set_visible(False)

    for ax in (ax1, ax2):
        n_days = (dates[-1] - dates[0]).days if len(dates) > 1 else 0
        if n_days > 30:
            ax.xaxis.set_major_locator(mdates.MonthLocator())
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
        elif n_days > 10:
            ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        else:
            ax.xaxis.set_major_locator(mdates.DayLocator())
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=8)

    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig); buf.seek(0)
    return buf.read()


def _pnl_sign(v): return "+" if v >= 0 else ""


def build_email_body(state: WheelState, mode_label: str) -> tuple[str, str]:
    today = date.today().isoformat(); initial = cfg.TOTAL_CAPITAL; capital = state.capital
    pnl = capital - initial; pnl_pct = pnl / initial * 100
    premium = state.total_premium_collected; free = state.free_capital
    reserved = state.cash_reserved; n_open = len(state.active_positions)
    start_str = state.bot_start_date or today
    try:
        days = max((date.today() - date.fromisoformat(start_str[:10])).days, 1)
    except Exception:
        days = 1
    ann_yield = (premium / initial) * (365.0 / days) * 100 if initial > 0 else 0.0
    cycles = state.closed_cycles
    wr = (sum(1 for c in cycles if c.total_premium >= 0) / len(cycles) * 100) if cycles else 0.0
    subject = (f"Wheel Bot | {today} | {mode_label} | "
               f"${capital:,.0f} | P&L {_pnl_sign(pnl)}${pnl:,.0f}")
    SEP = "+" + "-"*W + "+"; SEP2 = "+" + "="*W + "+"
    def row(label, val): return f"|  {label:<24}: {val:<{W-28}}|"
    L: list[str] = []
    L += [SEP2, f"|  WHEEL STRATEGY BOT  --  {today}  [{mode_label}]".ljust(W+1)+"|", SEP2, ""]
    L += [SEP, f"|  PORTFOLIO SUMMARY".ljust(W+1)+"|", SEP,
          row("Starting Capital",  f"${initial:>12,.2f}"),
          row("Current Capital",   f"${capital:>12,.2f}"),
          row("Total P&L",         f"${pnl:>+12,.2f}  ({_pnl_sign(pnl)}{abs(pnl_pct):.2f}%)"),
          row("Cash Reserved",     f"${reserved:>12,.2f}"),
          row("Free Capital",      f"${free:>12,.2f}"), ""]
    L += [SEP, f"|  PREMIUM INCOME".ljust(W+1)+"|", SEP,
          row("Total Collected",   f"${premium:>12,.2f}"),
          row("Annualised Yield",  f"{ann_yield:>11.1f}%"),
          row("Completed Cycles",  f"{state.completed_cycles:>13}"),
          row("Win Rate",          f"{wr:>11.1f}%"), ""]
    L += [SEP, f"|  OPEN POSITIONS  ({n_open}/{cfg.MAX_POSITIONS})".ljust(W+1)+"|", SEP]
    today_d = date.today()
    if state.active_positions:
        for pos in state.active_positions:
            if pos.phase == "csp_open" and pos.csp:
                dte = (date.fromisoformat(pos.csp.expiry) - today_d).days
                L.append(f"|  [{pos.ticker:<6}] CSP  K=${pos.csp.strike:<7.1f} exp={pos.csp.expiry}  DTE={dte:>3}  prem=${pos.csp.premium_received*100:.0f}".ljust(W+1)+"|")
            elif pos.phase == "cc_open" and pos.cc:
                dte = (date.fromisoformat(pos.cc.expiry) - today_d).days
                L.append(f"|  [{pos.ticker:<6}] CC   K=${pos.cc.strike:<7.1f} exp={pos.cc.expiry}  DTE={dte:>3}  prem=${pos.cc.premium_received*100:.0f}".ljust(W+1)+"|")
            elif pos.phase == "assigned":
                L.append(f"|  [{pos.ticker:<6}] ASSIGNED  {pos.shares}sh  basis=${pos.cost_basis:.2f}".ljust(W+1)+"|")
    else:
        L.append(f"|  (none)".ljust(W+1)+"|")
    if state.closed_cycles:
        L += [SEP, f"|  RECENT CLOSED CYCLES".ljust(W+1)+"|", SEP]
        for pos in list(reversed(state.closed_cycles))[:5]:
            L.append(f"|  [{pos.ticker:<6}]  premium=${pos.total_premium:>8.2f}  closed={(pos.closed_at or '')[:10]}".ljust(W+1)+"|")
    L += [SEP2, "", f"  Chart attached  |  {ACCOUNT_URL}", SEP2, ""]
    return subject, "\n".join(L)


def _smtp_send(subject, body, gmail_user, app_password, to, chart_bytes=None):
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject; msg["From"] = gmail_user; msg["To"] = to
    msg.attach(MIMEText(body, "plain"))
    if chart_bytes:
        img = MIMEImage(chart_bytes, "png")
        img.add_header("Content-Disposition", "attachment", filename="wheel_equity.png")
        msg.attach(img)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, app_password)
        server.sendmail(gmail_user, [to], msg.as_string())


def send_daily_email(state: WheelState, results_dir: str, mode_label: str = "PAPER") -> None:
    gmail_user   = os.environ.get("GMAIL_USER", "").strip()
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    to           = (os.environ.get("GMAIL_TO") or gmail_user).strip()
    if not gmail_user or not app_password:
        logger.info("Email skipped — GMAIL_USER / GMAIL_APP_PASSWORD not set."); return
    try:
        subject, body = build_email_body(state, mode_label)
        chart = _build_chart(results_dir)
        _smtp_send(subject, body, gmail_user, app_password, to, chart)
        logger.info("Email sent -> %s | %s", to, subject)
        if chart: logger.info("Chart attached (%d bytes)", len(chart))
    except Exception as exc:
        logger.warning("Email notification failed: %s", exc)
