"""
SQLite storage for Trading AI Agent.
Paper/research data only. This module does not place orders.
"""
import json
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DB_PATH = os.environ.get("DB_PATH", "trading_ai.db")


def _now_ist():
    return datetime.now(IST).isoformat(timespec="seconds")


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they do not already exist."""
    with get_connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS signal_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            underlying TEXT NOT NULL,
            signal TEXT,
            signal_stage TEXT,
            option_type TEXT,
            option_symbol TEXT,
            spot REAL,
            option_ltp REAL,
            pcr REAL,
            technical_bias TEXT,
            oi_bias TEXT,
            bias_5m TEXT,
            bias_15m TEXT,
            confidence_score INTEGER,
            result_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_signal_history_time
        ON signal_history(created_at);
        CREATE INDEX IF NOT EXISTS idx_signal_history_underlying
        ON signal_history(underlying);

        CREATE TABLE IF NOT EXISTS option_chain_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            underlying TEXT NOT NULL,
            expiry TEXT,
            spot REAL,
            atm_strike REAL,
            pcr REAL,
            total_ce_oi REAL,
            total_pe_oi REAL,
            max_pain REAL,
            chain_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_option_snapshots_time
        ON option_chain_snapshots(created_at);

        CREATE TABLE IF NOT EXISTS swing_scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            price REAL,
            ema20 REAL,
            ema50 REAL,
            rsi REAL,
            support REAL,
            resistance REAL,
            signal TEXT,
            result_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_swing_history_time
        ON swing_scan_history(created_at);
        """)


def save_signal(result):
    result = result or {}
    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO signal_history (
                created_at, underlying, signal, signal_stage, option_type,
                option_symbol, spot, option_ltp, pcr, technical_bias, oi_bias,
                bias_5m, bias_15m, confidence_score, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            _now_ist(), str(result.get("underlying") or "UNKNOWN"),
            result.get("signal"), result.get("signal_stage"),
            result.get("option_type"), result.get("option_symbol"),
            result.get("spot"), result.get("option_ltp"), result.get("pcr"),
            result.get("technical_bias"), result.get("oi_bias"),
            result.get("bias_5m"), result.get("bias_15m"),
            result.get("confidence_score"), json.dumps(result, default=str)
        ))
        return cursor.lastrowid


def save_option_snapshot(chain):
    chain = chain or {}
    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO option_chain_snapshots (
                created_at, underlying, expiry, spot, atm_strike, pcr,
                total_ce_oi, total_pe_oi, max_pain, chain_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            _now_ist(), str(chain.get("underlying") or "UNKNOWN"),
            chain.get("expiry"), chain.get("spot_price"),
            chain.get("atm_strike"), chain.get("pcr"),
            chain.get("total_ce_oi"), chain.get("total_pe_oi"),
            chain.get("max_pain"), json.dumps(chain, default=str)
        ))
        return cursor.lastrowid


def save_swing_rows(rows):
    saved = 0
    with get_connection() as conn:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            conn.execute("""
                INSERT INTO swing_scan_history (
                    created_at, symbol, price, ema20, ema50, rsi,
                    support, resistance, signal, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                _now_ist(),
                str(row.get("stock") or row.get("symbol") or "UNKNOWN"),
                row.get("price"), row.get("ema20"), row.get("ema50"),
                row.get("rsi"), row.get("support"), row.get("resistance"),
                row.get("signal"), json.dumps(row, default=str)
            ))
            saved += 1
    return saved


def get_history_counts():
    with get_connection() as conn:
        return {
            "signals": conn.execute(
                "SELECT COUNT(*) FROM signal_history"
            ).fetchone()[0],
            "option_snapshots": conn.execute(
                "SELECT COUNT(*) FROM option_chain_snapshots"
            ).fetchone()[0],
            "swing_rows": conn.execute(
                "SELECT COUNT(*) FROM swing_scan_history"
            ).fetchone()[0],
        }
