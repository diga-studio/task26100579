"""SQLite persistence for batch plans and progress."""

from __future__ import annotations

import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    batch_id TEXT NOT NULL,
    repo TEXT NOT NULL,
    commit_oid TEXT NOT NULL,
    tag TEXT NOT NULL,
    kind TEXT NOT NULL,
    planned_oid TEXT,
    tagger_date TEXT,
    PRIMARY KEY (batch_id, repo)
);
"""

STATE_PUBLISHING = "PUBLISHING"
STATE_ABORTING = "ABORTING"
STATE_COMMITTED = "COMMITTED"
STATE_ABORTED = "ABORTED"

KIND_CREATE = "create"
KIND_BORROW = "borrow"

TERMINAL_STATES = (STATE_COMMITTED, STATE_ABORTED)


class Store:
    def __init__(self, db_path: str) -> None:
        parent = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def get_batch_state(self, batch_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT state FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        return row["state"] if row else None

    def create_batch(self, batch_id: str, items: list[dict]) -> None:
        """Persist a new plan in PUBLISHING state (single transaction)."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO batches (batch_id, state) VALUES (?, ?)",
                (batch_id, STATE_PUBLISHING),
            )
            self._conn.executemany(
                "INSERT INTO items"
                " (batch_id, repo, commit_oid, tag, kind, planned_oid, tagger_date)"
                " VALUES (:batch_id, :repo, :commit, :tag, :kind,"
                " :planned_oid, :tagger_date)",
                [dict(item, batch_id=batch_id) for item in items],
            )

    def set_state(self, batch_id: str, state: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE batches SET state = ? WHERE batch_id = ?",
                (state, batch_id),
            )

    def get_items(self, batch_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM items WHERE batch_id = ? ORDER BY rowid",
                (batch_id,),
            )
        )

    def set_planned_oid(
        self, batch_id: str, repo: str, planned_oid: str, tagger_date: str
    ) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE items SET planned_oid = ?, tagger_date = ?"
                " WHERE batch_id = ? AND repo = ?",
                (planned_oid, tagger_date, batch_id, repo),
            )
