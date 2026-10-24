"""SQLite store for market metrics and news.

Every metric row keeps who said it, for which period, under what definition and where it came from,
so the agent can always show its sources and never blends figures from different brokers.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config, runtime
from .domain import valid_metric

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY,
    publisher TEXT NOT NULL,      -- e.g. JLL, Savills, Bank of England
    report TEXT NOT NULL,         -- report or series name
    period TEXT NOT NULL,         -- period the figure describes, e.g. 2026-Q3, 2026-08
    published TEXT,               -- publication date (ISO)
    submarket TEXT NOT NULL,      -- Central London, City, West End, Canary Wharf, Midtown, ...
    metric TEXT NOT NULL,         -- take_up_sqft, vacancy_rate, prime_rent_psf, ...
    value REAL,
    unit TEXT,
    definition TEXT,              -- how the publisher defines it; differences matter
    note TEXT,
    url TEXT,
    origin TEXT NOT NULL,         -- snapshot | live
    retrieved_at TEXT NOT NULL,
    UNIQUE (publisher, report, period, submarket, metric)
);
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    source TEXT,
    published TEXT,
    topics TEXT,                  -- JSON list
    submarkets TEXT,              -- JSON list
    impact TEXT,                  -- high | medium | low
    origin TEXT NOT NULL,
    retrieved_at TEXT NOT NULL
);
"""

METRIC_FIELDS = ["publisher", "report", "period", "published", "submarket", "metric", "value", "unit",
                 "definition", "note", "url"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """`with store.connect() as conn:` - commits on success, rolls back on error, and always closes.
    (A bare sqlite3 connection used in `with` only commits; it stays open.)"""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Skills run in parallel threads, each with its own connection: WAL lets readers and a writer overlap,
    # and the timeout makes a second writer wait for the lock instead of failing.
    runtime.checkpoint()
    conn = sqlite3.connect(config.DB_PATH, timeout=runtime.timeout(15))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        if not runtime.live_only() and not conn.execute("SELECT 1 FROM metrics LIMIT 1").fetchone():
            load_snapshot(conn)
        if not conn.in_transaction:
            conn.execute("BEGIN")
        yield conn
        with runtime.write_guard():
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_snapshot(conn: sqlite3.Connection) -> None:
    """Seed an empty database from the bundled broker snapshot so the agent works offline."""
    path = config.SNAPSHOT_DIR / "broker_metrics.json"
    if not path.exists():
        return
    rows = json.loads(path.read_text(encoding="utf-8"))
    upsert_metrics(conn, rows, origin="snapshot")


def upsert_metrics(conn: sqlite3.Connection, rows: list[dict], origin: str) -> int:
    runtime.checkpoint()
    if origin not in {"snapshot", "live"} or any(not valid_metric(r) for r in rows):
        raise ValueError("Invalid market metric rows or origin.")
    stamp = now()
    for r in rows:
        runtime.checkpoint()
        values = [r.get(f) for f in METRIC_FIELDS]
        conn.execute(
            f"""INSERT INTO metrics ({", ".join(METRIC_FIELDS)}, origin, retrieved_at)
                VALUES ({", ".join("?" * len(METRIC_FIELDS))}, ?, ?)
                ON CONFLICT (publisher, report, period, submarket, metric) DO UPDATE SET
                  value = excluded.value, unit = excluded.unit, definition = excluded.definition,
                  note = excluded.note, url = excluded.url, published = excluded.published,
                  origin = excluded.origin, retrieved_at = excluded.retrieved_at""",
            [*values, origin, stamp],
        )
    return len(rows)


def query_metrics(conn: sqlite3.Connection, metric: str | None = None, submarket: str | None = None,
                  publisher: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM metrics WHERE 1=1", []
    if metric:
        sql += " AND metric = ?"
        args.append(metric)
    if submarket:
        sql += " AND lower(submarket) = lower(?)"
        args.append(submarket)
    if publisher:
        sql += " AND lower(publisher) = lower(?)"
        args.append(publisher)
    sql += " ORDER BY metric, submarket, period DESC, publisher"
    return [dict(r) for r in conn.execute(sql, args)]


def save_news(conn: sqlite3.Connection, items: list[dict], origin: str) -> None:
    stamp = now()
    for it in items:
        conn.execute(
            """INSERT OR IGNORE INTO news (title, url, source, published, topics, submarkets, impact, origin, retrieved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [it["title"], it["url"], it.get("source"), it.get("published"), json.dumps(it.get("topics", [])),
             json.dumps(it.get("submarkets", [])), it.get("impact"), origin, stamp],
        )
