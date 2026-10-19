"""PostgreSQL access: a small per-worker connection pool plus dedicated connections.

    with db() as conn:            # pooled; commit on success, rollback on error
        rows = fetch_all(conn, "SELECT …", params)

Long-running work that holds a session advisory lock (web upload ingest, reparse,
view refresh) uses `connect()` instead, so a lock never leaks back into the pool.
"""
import logging
import threading
import time
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

import config

log = logging.getLogger("pixi.db")

_pool = None
_pool_lock = threading.Lock()
_slots = threading.BoundedSemaphore(config.DB_POOL_MAX)


class TimedCursor(psycopg2.extras.RealDictCursor):
    """Dict rows; logs statements slower than SLOW_QUERY_MS."""

    def execute(self, query, vars=None):
        t0 = time.perf_counter()
        try:
            return super().execute(query, vars)
        finally:
            ms = (time.perf_counter() - t0) * 1000
            if ms >= config.SLOW_QUERY_MS:
                text = " ".join(str(query).split())
                log.warning("slow query %.0f ms: %s", ms, text[:300])


def connect():
    """A dedicated (non-pooled) connection; the caller closes it."""
    return psycopg2.connect(config.DATABASE_URL, cursor_factory=TimedCursor)


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadedConnectionPool(1, config.DB_POOL_MAX, config.DATABASE_URL, cursor_factory=TimedCursor)
        return _pool


@contextmanager
def db():
    """Pooled connection. Waits for a free slot instead of failing when all are busy."""
    _slots.acquire()
    pool = _get_pool()
    conn = None
    broken = False
    try:
        conn = pool.getconn()
        if conn.closed:
            pool.putconn(conn, close=True)
            conn = pool.getconn()
        yield conn
        conn.commit()
    except psycopg2.InterfaceError:
        broken = True
        raise
    except psycopg2.OperationalError:
        broken = True
        raise
    except BaseException:
        if conn is not None and not conn.closed:
            try:
                conn.rollback()
            except psycopg2.Error:
                broken = True
        raise
    finally:
        if conn is not None:
            try:
                pool.putconn(conn, close=broken or bool(conn.closed))
            except psycopg2.pool.PoolError:
                pass
        _slots.release()


def fetch_all(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def fetch_one(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def execute(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def close_pool():
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None
