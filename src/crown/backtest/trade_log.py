"""SQLite trade log — 01-architecture.md: 'every decision and order, for
audit and for the paper-vs-actual performance comparison.' Append-only;
one row per notable event so the full replay can be reconstructed and
audited after the fact, not just the final P&L."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class TradeLog:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                letter_date TEXT,
                trade_id TEXT,
                symbol TEXT,
                event_type TEXT NOT NULL,
                detail TEXT,
                price REAL,
                qty REAL,
                pnl REAL
            )
        """)
        self.conn.commit()

    def log(
        self, ts: str, event_type: str, *,
        letter_date: str | None = None, trade_id: str | None = None,
        symbol: str | None = None, detail: dict | str | None = None,
        price: float | None = None, qty: float | None = None, pnl: float | None = None,
    ) -> None:
        if isinstance(detail, dict):
            detail = json.dumps(detail)
        self.conn.execute(
            "INSERT INTO events (ts, letter_date, trade_id, symbol, event_type, detail, price, qty, pnl) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, letter_date, trade_id, symbol, event_type, detail, price, qty, pnl),
        )
        self.conn.commit()

    def all_events(self) -> list[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.execute("SELECT * FROM events ORDER BY id")
        return cur.fetchall()

    def close(self) -> None:
        self.conn.close()
