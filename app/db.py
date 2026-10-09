"""SQLite persistence (stdlib only). One short-lived connection per operation."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .services.processing import ProcessedFile

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    status TEXT NOT NULL,
    feature_count INTEGER NOT NULL DEFAULT 0,
    crs TEXT,
    error TEXT,
    warnings TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS features (
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    layer TEXT NOT NULL,
    geometry_type TEXT,
    geometry TEXT,
    crs TEXT,
    properties TEXT NOT NULL,
    m_status TEXT NOT NULL,
    area_sq_m REAL,
    length_m REAL,
    m_crs TEXT,
    m_warnings TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (file_id, idx)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repository:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:  # commit / rollback
                yield conn
        finally:
            conn.close()

    # -- files ---------------------------------------------------------
    def create_file(self, filename: str, file_type: str) -> str:
        file_id = uuid.uuid4().hex
        with self._conn() as c:
            c.execute(
                "INSERT INTO files (id, filename, file_type, status, created_at)"
                " VALUES (?, ?, ?, 'PROCESSING', ?)",
                (file_id, filename, file_type, _now()),
            )
        return file_id

    def mark_failed(self, file_id: str, error: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE files SET status='FAILED', error=?, completed_at=? WHERE id=?",
                (error, _now(), file_id),
            )

    def complete_file(self, file_id: str, result: ProcessedFile) -> None:
        """Store all features and flip the status to COMPLETED in one transaction."""
        rows = [
            (
                file_id, f.index, f.layer, f.geometry_type,
                None if f.geometry is None else json.dumps(f.geometry),
                f.crs, json.dumps(f.properties, default=str),
                f.measurement.status.value, f.measurement.area_sq_m, f.measurement.length_m,
                f.measurement.measurement_crs, json.dumps(f.measurement.warnings),
            )
            for f in result.features
        ]
        with self._conn() as c:
            c.executemany("INSERT INTO features VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            c.execute(
                "UPDATE files SET status='COMPLETED', feature_count=?, crs=?, warnings=?,"
                " completed_at=? WHERE id=?",
                (len(rows), result.crs, json.dumps(result.warnings), _now(), file_id),
            )

    def get_file(self, file_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["warnings"] = json.loads(d["warnings"])
        return d

    # -- features ------------------------------------------------------
    def list_features(self, file_id: str, limit: int, offset: int) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM features WHERE file_id=? ORDER BY idx LIMIT ? OFFSET ?",
                (file_id, limit, offset),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["geometry"] = json.loads(d["geometry"]) if d["geometry"] else None
            d["properties"] = json.loads(d["properties"])
            d["m_warnings"] = json.loads(d["m_warnings"])
            out.append(d)
        return out

    def measurement_summary(self, file_id: str) -> dict:
        with self._conn() as c:
            totals = c.execute(
                "SELECT COALESCE(SUM(area_sq_m),0) a, COALESCE(SUM(length_m),0) l"
                " FROM features WHERE file_id=?",
                (file_id,),
            ).fetchone()
            counts = c.execute(
                "SELECT m_status, COUNT(*) n FROM features WHERE file_id=? GROUP BY m_status",
                (file_id,),
            ).fetchall()
        return {
            "total_area_sq_m": totals["a"],
            "total_length_m": totals["l"],
            "status_counts": {r["m_status"]: r["n"] for r in counts},
        }
