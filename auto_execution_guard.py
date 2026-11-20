"""Fail-closed controls for a future fully-automatic Zerodha execution adapter.

This module deliberately does NOT call KiteConnect.place_order. It provides
persistent daily-loss accounting, per-trade risk checks and a kill switch.
Live execution remains disabled unless a separately reviewed broker adapter
is implemented and explicitly enabled after end-to-end tests.
"""
from __future__ import annotations
import os, sqlite3, time
from contextlib import contextmanager
from decimal import Decimal

CAPITAL_DEFAULT = Decimal("10000")
PER_TRADE_RISK_DEFAULT = Decimal("1000")
DAILY_LOSS_LIMIT_DEFAULT = Decimal("5000")
DB_PATH_DEFAULT = os.environ.get("TRADING_AI_DB_PATH", "trading_ai_guard.sqlite3")


def _money(value, name):
    try: d = Decimal(str(value))
    except Exception as exc: raise ValueError(f"Invalid {name}") from exc
    if not d.is_finite(): raise ValueError(f"Invalid {name}")
    return d

@contextmanager
def _connect(path):
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
    finally: conn.close()

def initialize_guard(db_path=DB_PATH_DEFAULT):
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    with _connect(db_path) as c:
        c.execute("CREATE TABLE IF NOT EXISTS daily_pnl (trade_date TEXT PRIMARY KEY, realized_pnl TEXT NOT NULL DEFAULT '0')")
        c.execute("CREATE TABLE IF NOT EXISTS guard_state (id INTEGER PRIMARY KEY CHECK(id=1), halted INTEGER NOT NULL DEFAULT 1, reason TEXT NOT NULL DEFAULT 'LIVE_EXECUTION_DISABLED')")
        c.execute("INSERT OR IGNORE INTO guard_state(id,halted,reason) VALUES(1,1,'LIVE_EXECUTION_DISABLED')")

def set_kill_switch(halted, reason="MANUAL_KILL_SWITCH", db_path=DB_PATH_DEFAULT):
    initialize_guard(db_path)
    with _connect(db_path) as c:
        c.execute("UPDATE guard_state SET halted=?, reason=? WHERE id=1", (1 if halted else 0, str(reason)[:200]))
    return get_guard_state(db_path)

def get_guard_state(db_path=DB_PATH_DEFAULT):
    initialize_guard(db_path)
    with _connect(db_path) as c:
        row = c.execute("SELECT halted,reason FROM guard_state WHERE id=1").fetchone()
    return {"halted": bool(row[0]), "reason": row[1]}

def record_realized_pnl(amount, *, trade_date, db_path=DB_PATH_DEFAULT):
    """Record a closed trade's net P&L (negative for a loss), exactly once per caller event."""
    pnl = _money(amount, "amount")
    if not trade_date or len(str(trade_date)) != 10:
        raise ValueError("trade_date must be YYYY-MM-DD")
    initialize_guard(db_path)
    with _connect(db_path) as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT realized_pnl FROM daily_pnl WHERE trade_date=?", (trade_date,)).fetchone()
        total = (Decimal(row[0]) if row else Decimal("0")) + pnl
        c.execute("INSERT INTO daily_pnl(trade_date,realized_pnl) VALUES(?,?) ON CONFLICT(trade_date) DO UPDATE SET realized_pnl=excluded.realized_pnl", (trade_date, str(total)))
        c.execute("COMMIT")
    return {"trade_date": trade_date, "realized_pnl": float(total)}

def check_trade_allowed(*, planned_loss, trade_date, capital=CAPITAL_DEFAULT,
                        per_trade_risk=PER_TRADE_RISK_DEFAULT,
                        daily_loss_limit=DAILY_LOSS_LIMIT_DEFAULT,
                        db_path=DB_PATH_DEFAULT, live_flag=None):
    """Return allow/deny decision; never places an order."""
    loss = _money(planned_loss, "planned_loss")
    cap = _money(capital, "capital")
    per_trade = _money(per_trade_risk, "per_trade_risk")
    daily = _money(daily_loss_limit, "daily_loss_limit")
    if min(loss, cap, per_trade, daily) <= 0: raise ValueError("Risk values must be positive")
    initialize_guard(db_path)
    state = get_guard_state(db_path)
    with _connect(db_path) as c:
        row = c.execute("SELECT realized_pnl FROM daily_pnl WHERE trade_date=?", (trade_date,)).fetchone()
    realized = Decimal(row[0]) if row else Decimal("0")
    reasons=[]
    if state["halted"]: reasons.append(state["reason"])
    if live_flag is None: live_flag = os.environ.get("TRADING_AI_LIVE_ENABLED", "false").lower() == "true"
    if not live_flag: reasons.append("LIVE_EXECUTION_DISABLED")
    if loss > per_trade: reasons.append("PER_TRADE_RISK_EXCEEDED")
    if realized <= -daily: reasons.append("DAILY_LOSS_LIMIT_REACHED")
    elif realized - loss < -daily: reasons.append("TRADE_COULD_BREACH_DAILY_LOSS_LIMIT")
    if cap != Decimal("10000"): reasons.append("CAPITAL_CONFIGURATION_MISMATCH")
    return {"allowed": not reasons, "reasons": reasons,
            "capital": float(cap), "planned_loss": float(loss),
            "per_trade_risk_limit": float(per_trade),
            "daily_loss_limit": float(daily), "realized_pnl": float(realized),
            "daily_pnl_after_planned_loss": float(realized-loss),
            "kill_switch": state, "live_flag": bool(live_flag),
            "broker_order_placed": False}
