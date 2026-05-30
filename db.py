"""
SQLite storage for the network profiler.

Metrics are stored in "long" format (one row per metric per timestamp) so new
targets or metrics can be added without schema changes. Speed tests get their
own table because they're wider and far less frequent.
"""
import os
import sqlite3
from contextlib import contextmanager

import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    ts     TEXT NOT NULL,          -- ISO8601 UTC, e.g. 2026-05-30T12:00:00Z
    metric TEXT NOT NULL,          -- e.g. gateway_ping_ms, ping_google_ms, dns_ms
    value  REAL,                   -- NULL means a failed/timed-out measurement
    PRIMARY KEY (ts, metric)
);
CREATE INDEX IF NOT EXISTS idx_metrics_metric_ts ON metrics(metric, ts);

CREATE TABLE IF NOT EXISTS speedtests (
    ts            TEXT NOT NULL PRIMARY KEY,
    download_mbps REAL,
    upload_mbps   REAL,
    ping_ms       REAL,
    server        TEXT
);

CREATE TABLE IF NOT EXISTS events (
    ts      TEXT NOT NULL,
    kind    TEXT NOT NULL,         -- 'info' | 'warn' | 'error'
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


def init_db(path=None):
    path = path or config.DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
    return path


@contextmanager
def connect(path=None):
    path = path or config.DB_PATH
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL keeps the collector writing while the dashboard reads concurrently.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def write_metrics(ts, values, path=None):
    """values: dict of {metric_name: value_or_None}."""
    rows = [(ts, name, val) for name, val in values.items()]
    with connect(path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO metrics (ts, metric, value) VALUES (?, ?, ?)",
            rows,
        )


def write_speedtest(ts, download_mbps, upload_mbps, ping_ms, server, path=None):
    with connect(path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO speedtests (ts, download_mbps, upload_mbps, ping_ms, server) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, download_mbps, upload_mbps, ping_ms, server),
        )


def write_event(ts, kind, message, path=None):
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO events (ts, kind, message) VALUES (?, ?, ?)",
            (ts, kind, message),
        )


def prune(older_than_iso, path=None):
    with connect(path) as conn:
        conn.execute("DELETE FROM metrics WHERE ts < ?", (older_than_iso,))
        conn.execute("DELETE FROM speedtests WHERE ts < ?", (older_than_iso,))
        conn.execute("DELETE FROM events WHERE ts < ?", (older_than_iso,))


def fetch_series(metrics, since_iso, path=None):
    """Return {metric: [(ts, value), ...]} for the given metric names."""
    if not metrics:
        return {}
    placeholders = ",".join("?" for _ in metrics)
    out = {m: [] for m in metrics}
    with connect(path) as conn:
        cur = conn.execute(
            f"SELECT ts, metric, value FROM metrics "
            f"WHERE metric IN ({placeholders}) AND ts >= ? ORDER BY ts ASC",
            (*metrics, since_iso),
        )
        for row in cur:
            out[row["metric"]].append((row["ts"], row["value"]))
    return out


def fetch_speedtests(since_iso, path=None):
    with connect(path) as conn:
        cur = conn.execute(
            "SELECT * FROM speedtests WHERE ts >= ? ORDER BY ts ASC", (since_iso,)
        )
        return [dict(r) for r in cur]


def distinct_metrics(path=None):
    with connect(path) as conn:
        cur = conn.execute("SELECT DISTINCT metric FROM metrics ORDER BY metric")
        return [r["metric"] for r in cur]
