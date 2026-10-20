"""Append-only decisions with a hash chain. No entry without durable explanation."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from crown.research.models import digest, serial


class Journal:
    def __init__(self, path: Path | None = None):
        self.events = []
        self.connection = sqlite3.connect(path or ":memory:")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL UNIQUE)")
        self.connection.execute("CREATE TRIGGER IF NOT EXISTS immutable_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append-only journal'); END")
        self.connection.execute("CREATE TRIGGER IF NOT EXISTS immutable_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append-only journal'); END")
        if self.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]:
            self.connection.close()
            raise ValueError("Research run journal already exists; use a new output directory")
        self.head = "0" * 64

    def append(self, *, at, kind: str, reason: str, policy_hash: str, inputs: dict, **details) -> dict:
        if not reason or not policy_hash or not inputs:
            raise ValueError("Decision requires a reason, policy hash and input provenance")
        event = serial(dict(seq=len(self.events), at=at, kind=kind, reason=reason,
                            policy_hash=policy_hash, inputs=inputs, **details))
        hashed = digest({"event": event, "previous": self.head})
        with self.connection:
            self.connection.execute("INSERT INTO events VALUES (?,?,?,?)", (event["seq"], json.dumps(event, sort_keys=True), self.head, hashed))
        self.events.append(event | {"previous_hash": self.head, "event_hash": hashed})
        self.head = hashed
        return event

    def close(self):
        self.connection.close()
