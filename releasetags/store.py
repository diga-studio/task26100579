"""SQLite-backed persistence for batch plans and state."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# Batch states
IN_PROGRESS = "IN_PROGRESS"
ABORTING = "ABORTING"
COMMITTED = "COMMITTED"
ABORTED = "ABORTED"

# Item kinds
KIND_CREATE = "create"
KIND_BORROW = "borrow"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    seq INTEGER NOT NULL,
    repo_path TEXT NOT NULL,
    commit_oid TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    planned_object TEXT NOT NULL,
    PRIMARY KEY (batch_id, seq)
);
"""


@dataclass(frozen=True)
class Item:
    repo_path: str
    commit_oid: str
    tag_name: str
    kind: str
    planned_object: str


class Store:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def get_state(self, batch_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT state FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        return row[0] if row else None

    def create_batch(self, batch_id: str, items: list[Item]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO batches (batch_id, state) VALUES (?, ?)",
                (batch_id, IN_PROGRESS),
            )
            self._conn.executemany(
                "INSERT INTO items"
                " (batch_id, seq, repo_path, commit_oid, tag_name, kind, planned_object)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (batch_id, seq, it.repo_path, it.commit_oid, it.tag_name,
                     it.kind, it.planned_object)
                    for seq, it in enumerate(items)
                ],
            )

    def set_state(self, batch_id: str, state: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE batches SET state = ? WHERE batch_id = ?",
                (state, batch_id),
            )

    def get_items(self, batch_id: str) -> list[Item]:
        rows = self._conn.execute(
            "SELECT repo_path, commit_oid, tag_name, kind, planned_object"
            " FROM items WHERE batch_id = ? ORDER BY seq",
            (batch_id,),
        ).fetchall()
        return [Item(*row) for row in rows]
