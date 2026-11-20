"""Read-only Zerodha order/position reconciliation and durable exit-watch ledger.

This module NEVER submits, modifies, or cancels broker orders. Any mismatch,
unknown broker position, or stale/missing quote is reported as a HALT condition.
"""
from __future__ import annotations
import os, sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

DB_DEFAULT = os.environ.get('TRADING_AI_DB_PATH', 'trading_ai_guard.sqlite3')


def _db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('''CREATE TABLE IF NOT EXISTS exit_watch (
        watch_id TEXT PRIMARY KEY, exchange TEXT NOT NULL, tradingsymbol TEXT NOT NULL,
        quantity INTEGER NOT NULL, average_price TEXT NOT NULL, stop_price TEXT NOT NULL,
        target_price TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'WATCHING',
        last_ltp TEXT, trigger TEXT, updated_at TEXT NOT NULL)''')
    c.commit()
    return c


def _num(v, label):
    try: x = Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError) as e: raise ValueError(f'Invalid {label}') from e
    if not x.is_finite(): raise ValueError(f'Invalid {label}')
    return x


def reconcile_broker_state(kite, *, db_path=DB_DEFAULT):
    """Read broker orders/positions and compare with locally persisted live intents.
    Conservative: any broker net position not represented by a locally known
    intent is an exception requiring manual review.
    """
    orders = kite.orders()
    positions = kite.positions()
    net = positions.get('net', []) if isinstance(positions, dict) else []
    broker_open = [p for p in net if int(p.get('quantity') or 0) != 0]
    from live_order_manager import initialize_order_ledger
    initialize_order_ledger(db_path)
    with _db(db_path) as c:
        intents = c.execute("SELECT * FROM live_order_intents WHERE state IN ('SUBMITTED','OPEN','PARTIAL')").fetchall()
    known = {(r['exchange'], r['tradingsymbol']) for r in intents}
    unknown = []
    for p in broker_open:
        key = (str(p.get('exchange') or '').upper(), str(p.get('tradingsymbol') or '').upper())
        if key not in known:
            unknown.append({'exchange': key[0], 'tradingsymbol': key[1], 'quantity': int(p.get('quantity') or 0)})
    pending = [o for o in orders if str(o.get('status','')).upper() in {'OPEN','TRIGGER PENDING','PARTIALLY FILLED'}]
    mismatches = []
    if unknown: mismatches.append('UNKNOWN_BROKER_POSITION')
    if pending: mismatches.append('PENDING_OR_PARTIAL_BROKER_ORDER')
    if mismatches:
        try:
            from auto_execution_guard import set_kill_switch
            set_kill_switch(True, 'RECONCILIATION_MISMATCH', db_path)
        except Exception:
            pass
    return {'ok': not mismatches, 'open_positions': len(broker_open),
            'pending_orders': len(pending), 'unknown_positions': unknown,
            'mismatches': mismatches, 'broker_order_count': len(orders),
            'broker_order_placed': False}


def register_exit_watch(*, watch_id, exchange, tradingsymbol, quantity,
                        average_price, stop_price, target_price, db_path=DB_DEFAULT):
    qty = int(quantity)
    avg, stop, target = (_num(average_price,'average_price'), _num(stop_price,'stop_price'), _num(target_price,'target_price'))
    if not watch_id or qty == 0 or not (Decimal('0') < stop < avg < target):
        raise ValueError('Invalid long-position exit watch')
    now = datetime.now(timezone.utc).isoformat()
    with _db(db_path) as c:
        try:
            c.execute('''INSERT INTO exit_watch
                (watch_id,exchange,tradingsymbol,quantity,average_price,stop_price,target_price,status,updated_at)
                VALUES(?,?,?,?,?,?,?,'WATCHING',?)''',
                (str(watch_id),str(exchange).upper(),str(tradingsymbol).upper(),qty,str(avg),str(stop),str(target),now))
            c.commit()
        except sqlite3.IntegrityError:
            return {'created':False,'reason':'DUPLICATE_WATCH_ID','broker_order_placed':False}
    return {'created':True,'status':'WATCHING','broker_order_placed':False}


def evaluate_exit_watch(watch_id, ltp, *, db_path=DB_DEFAULT):
    price = _num(ltp,'ltp')
    now = datetime.now(timezone.utc).isoformat()
    with _db(db_path) as c:
        row = c.execute('SELECT * FROM exit_watch WHERE watch_id=?',(str(watch_id),)).fetchone()
        if not row: return {'ok':False,'reason':'WATCH_NOT_FOUND','broker_order_placed':False}
        if row['status'] != 'WATCHING': return {'ok':False,'reason':'WATCH_NOT_ACTIVE','status':row['status'],'broker_order_placed':False}
        stop,target = Decimal(row['stop_price']),Decimal(row['target_price'])
        trigger = 'STOP_LOSS' if price <= stop else ('TARGET' if price >= target else None)
        status = 'EXIT_REQUIRED' if trigger else 'WATCHING'
        c.execute('UPDATE exit_watch SET last_ltp=?,trigger=?,status=?,updated_at=? WHERE watch_id=?',
                  (str(price),trigger,status,now,str(watch_id)))
        c.commit()
    return {'ok':True,'status':status,'trigger':trigger,'ltp':float(price),
            'action_required':bool(trigger),'broker_order_placed':False}


def list_exit_watches(*, db_path=DB_DEFAULT):
    with _db(db_path) as c:
        return [dict(r) for r in c.execute('SELECT * FROM exit_watch ORDER BY updated_at DESC').fetchall()]
