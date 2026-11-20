"""Fail-closed Zerodha live-order adapter scaffold.

This module intentionally refuses to submit broker orders. The current project
still lacks broker-trade reconciliation, durable exit supervision across Render
restarts, and verified net-P&L accounting. Do not remove the hard block until
those controls have been implemented and independently tested.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

DB_DEFAULT = os.environ.get("TRADING_AI_DB_PATH", "trading_ai_guard.sqlite3")


def _positive(value, name):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {name}") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return number


def initialize_order_ledger(db_path=DB_DEFAULT):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS live_order_intents (
            intent_id TEXT PRIMARY KEY,
            exchange TEXT NOT NULL,
            tradingsymbol TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            side TEXT NOT NULL,
            state TEXT NOT NULL,
            broker_order_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            detail TEXT
        )""")
        conn.commit()


def record_order_intent(*, intent_id, exchange, tradingsymbol, quantity,
                        side="BUY", db_path=DB_DEFAULT):
    """Persist a unique intent before any future broker submission is attempted."""
    if not intent_id or not str(intent_id).strip():
        raise ValueError("intent_id is required")
    qty = int(quantity)
    if qty <= 0:
        raise ValueError("quantity must be positive")
    side = str(side).upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    initialize_order_ledger(db_path)
    now = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.execute("""INSERT INTO live_order_intents
                (intent_id,exchange,tradingsymbol,quantity,side,state,created_at,updated_at,detail)
                VALUES(?,?,?,?,?,'BLOCKED_NOT_READY',?,?,?)""",
                (str(intent_id), str(exchange).upper(), str(tradingsymbol).upper(),
                 qty, side, now, now, "Live execution hard-disabled"))
            conn.commit()
        return {"created": True, "state": "BLOCKED_NOT_READY", "broker_order_placed": False}
    except sqlite3.IntegrityError:
        return {"created": False, "state": "DUPLICATE_INTENT", "broker_order_placed": False}


def submit_live_order(*, kite, intent_id, exchange, tradingsymbol, quantity,
                      entry_price, stop_price, target_price, planned_loss,
                      trade_date, db_path=DB_DEFAULT):
    """Fail-closed placeholder; deliberately never calls kite.place_order().

    The inputs are validated and an intent is persisted for audit. It then
    returns a hard block until broker reconciliation and durable SL/target
    supervision are complete. Environment flags cannot override this block.
    """
    _positive(entry_price, "entry_price")
    stop = _positive(stop_price, "stop_price")
    entry = _positive(entry_price, "entry_price")
    target = _positive(target_price, "target_price")
    loss = _positive(planned_loss, "planned_loss")
    if not (stop < entry < target):
        raise ValueError("Expected long-option plan: stop < entry < target")
    intent = record_order_intent(
        intent_id=intent_id, exchange=exchange, tradingsymbol=tradingsymbol,
        quantity=quantity, side="BUY", db_path=db_path,
    )
    if not intent["created"]:
        return {**intent, "reason": "DUPLICATE_INTENT"}
    return {
        "allowed": False,
        "reason": "LIVE_EXECUTION_HARD_BLOCKED_UNTIL_EXIT_AND_RECONCILIATION_TESTS_PASS",
        "intent_id": str(intent_id),
        "planned_loss": float(loss),
        "live_flag": os.environ.get("TRADING_AI_LIVE_ENABLED", "false").lower() == "true",
        "broker_order_placed": False,
    }


def get_order_intents(*, db_path=DB_DEFAULT, limit=50):
    initialize_order_ledger(db_path)
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM live_order_intents ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [dict(row) for row in rows]
