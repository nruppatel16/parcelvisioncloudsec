import os
import sqlite3
from contextlib import contextmanager
from datetime import date

DB_PATH = os.getenv("DB_PATH", "/data/lobbyops.db")


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS parcels (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                building     TEXT    NOT NULL,
                unit         TEXT    NOT NULL,
                name         TEXT,
                supplier     TEXT,
                parcel_type  TEXT,
                status       TEXT    DEFAULT 'pending',
                retry_count  INTEGER DEFAULT 0,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME
            )
        """)
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_building_status ON parcels (building, status)"
        )


def enqueue(building: str, unit: str, name: str, supplier: str, parcel_type: str) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO parcels (building, unit, name, supplier, parcel_type) VALUES (?, ?, ?, ?, ?)",
            (building, unit, name, supplier, parcel_type),
        )
        return cur.lastrowid


def get_pending(building: str) -> list:
    with _conn() as con:
        rows = con.execute(
            """SELECT id, unit, name, supplier, parcel_type, created_at
               FROM parcels
               WHERE building = ? AND status = 'pending'
               ORDER BY created_at ASC""",
            (building,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_complete(building: str, unit: str):
    with _conn() as con:
        con.execute(
            """UPDATE parcels
               SET status = 'complete', completed_at = CURRENT_TIMESTAMP
               WHERE building = ? AND unit = ? AND status = 'pending'""",
            (building, unit),
        )


def mark_failed(building: str, unit: str):
    with _conn() as con:
        con.execute(
            """UPDATE parcels
               SET status = 'failed'
               WHERE building = ? AND unit = ? AND status IN ('pending', 'failed')""",
            (building, unit),
        )


def increment_retry(building: str, unit: str):
    # Items reaching retry_count >= 3 move to 'flagged' for manual review
    with _conn() as con:
        con.execute(
            """UPDATE parcels
               SET retry_count = retry_count + 1,
                   status = CASE WHEN retry_count + 1 >= 3 THEN 'flagged' ELSE status END
               WHERE building = ? AND unit = ? AND status IN ('pending', 'failed')""",
            (building, unit),
        )


def get_flagged(building: str) -> list:
    with _conn() as con:
        rows = con.execute(
            """SELECT id, unit, name, supplier, parcel_type, retry_count, created_at
               FROM parcels
               WHERE building = ? AND status = 'flagged'
               ORDER BY created_at ASC""",
            (building,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_metrics(building: str) -> dict:
    today = date.today().isoformat()
    with _conn() as con:
        today_total = con.execute(
            "SELECT COUNT(*) FROM parcels WHERE building = ? AND DATE(created_at) = ?",
            (building, today),
        ).fetchone()[0]

        today_complete = con.execute(
            "SELECT COUNT(*) FROM parcels WHERE building = ? AND DATE(created_at) = ? AND status = 'complete'",
            (building, today),
        ).fetchone()[0]

        pending_count = con.execute(
            "SELECT COUNT(*) FROM parcels WHERE building = ? AND status = 'pending'",
            (building,),
        ).fetchone()[0]

        failed_count = con.execute(
            "SELECT COUNT(*) FROM parcels WHERE building = ? AND status = 'failed'",
            (building,),
        ).fetchone()[0]

        avg_row = con.execute(
            """SELECT AVG(
                   CAST((julianday(completed_at) - julianday(created_at)) * 86400 AS INTEGER)
               )
               FROM parcels
               WHERE building = ? AND status = 'complete' AND completed_at IS NOT NULL""",
            (building,),
        ).fetchone()[0]

    return {
        "today_total":            today_total,
        "today_complete":         today_complete,
        "pending_count":          pending_count,
        "failed_count":           failed_count,
        "avg_processing_seconds": round(avg_row, 1) if avg_row is not None else None,
    }
