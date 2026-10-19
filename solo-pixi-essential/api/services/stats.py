"""Unit statistics from the mv_unit / mv_unit_day views.

A unit is one (work order, MAC1). Within the requested scope each unit is counted once,
by its latest PASS if it ever passed, otherwise by its latest attempt — the same rule
the dashboard has always used (ROW_NUMBER … ORDER BY result = 'PASS' first, start_time
DESC). mv_unit keeps that result per unit over all dates (used when there is no date filter);
mv_unit_day keeps one row per (effective date, work order, MAC1), so a date-range scope only
combines a few day rows per unit instead of sorting every test record.
"""
import logging
import threading
import time
from collections import OrderedDict
from datetime import timedelta

import config
import db
from services.filters import Scope, mv_where, raw_where

log = logging.getLogger("pixi.stats")


def units_cte(scope: Scope):
    """CTE `c`: one row per unit in scope, with the record it is counted by (result, chosen_*).

    Without a date filter the precomputed mv_unit is used as is; with one, the unit rows
    are rebuilt from the day rows inside the range."""
    if scope.date_from is None and scope.date_to is None:
        where, params = ("WHERE wo_key = %s", [scope.work_order]) if scope.work_order else ("", [])
        return f"c AS (SELECT * FROM mv_unit {where})", params
    where, params = mv_where(scope)
    sql = f"""
    d AS (SELECT * FROM mv_unit_day {where}),
    u AS (
        SELECT wo_key, mac1,
               SUM(n)::int                                                     AS attempts,
               SUM(n_pass)::int                                                AS pass_count,
               SUM(n_fail)::int                                                AS fail_count,
               SUM(n_stop)::int                                                AS stop_count,
               BOOL_OR(n_pass > 0)                                             AS has_pass,
               MIN(first_time)                                                 AS first_time,
               MAX(last_time)                                                  AS last_time,
               MAX(last_pass_time)                                             AS last_pass_time,
               (ARRAY_AGG(last_result ORDER BY last_time DESC))[1]             AS latest_result,
               (ARRAY_AGG(eff_date ORDER BY last_time DESC))[1]                AS latest_date,
               (ARRAY_AGG(eff_date ORDER BY last_pass_time DESC NULLS LAST))[1] AS pass_date,
               (ARRAY_AGG(last_mac2 ORDER BY last_time DESC))[1]               AS last_mac2,
               SUM(dur_sum)                                                    AS dur_sum,
               SUM(dur_n)::int                                                 AS dur_n
        FROM d GROUP BY wo_key, mac1
    ),
    c AS (
        SELECT u.*,
               CASE WHEN has_pass THEN 'PASS' ELSE latest_result END           AS result,
               CASE WHEN has_pass THEN pass_date ELSE latest_date END          AS chosen_date,
               CASE WHEN has_pass THEN last_pass_time ELSE last_time END       AS chosen_time
        FROM u
    )"""
    return sql, params


def _round2(value):
    return round(float(value), 2) if value is not None else None


def summary(conn, scope: Scope):
    cte, params = units_cte(scope)
    row = db.fetch_one(conn, f"""
        WITH {cte}
        SELECT COUNT(*)                                                   AS total,
               COUNT(*) FILTER (WHERE result = 'PASS')                    AS pass,
               COUNT(*) FILTER (WHERE result = 'FAIL')                    AS fail,
               COUNT(*) FILTER (WHERE result = 'STOP')                    AS stop,
               ROUND(COUNT(*) FILTER (WHERE result = 'PASS') * 100.0 / NULLIF(COUNT(*), 0), 2) AS yield_pct,
               COUNT(*) FILTER (WHERE attempts > 1)                       AS retry_units,
               COUNT(*) FILTER (WHERE has_pass AND stop_count > 0 AND fail_count = 0 AND attempts > 1) AS stop_to_pass,
               COALESCE(SUM(attempts), 0)                                 AS test_attempts,
               MIN(first_time)                                            AS first_test,
               MAX(last_time)                                             AS last_test
        FROM c
    """, params)
    total = row["total"] or 0
    row["yield_pct"] = _round2(row["yield_pct"])
    row["retry_rate"] = round((row["retry_units"] or 0) * 100.0 / max(total, 1), 2)
    return row


def pass_fail_split(conn, scope: Scope):
    cte, params = units_cte(scope)
    return db.fetch_all(conn, f"""
        WITH {cte}
        SELECT result, COUNT(*) AS count FROM c GROUP BY result ORDER BY result
    """, params)


def year_of_latest(conn, scope: Scope):
    where, params = mv_where(scope)
    row = db.fetch_one(conn, f"SELECT EXTRACT(YEAR FROM MAX(eff_date))::int AS y FROM mv_unit_day {where}", params)
    return row["y"] if row and row["y"] else None


def monthly(conn, scope: Scope, year):
    """Jan–Dec counts of units, by the month of the record each unit is counted by."""
    from datetime import date
    year_scope = Scope(max(filter(None, [scope.date_from, date(year, 1, 1)])),
                       min(filter(None, [scope.date_to, date(year + 1, 1, 1)])),
                       scope.work_order)
    cte, params = units_cte(year_scope)
    rows = db.fetch_all(conn, f"""
        WITH {cte},
        data AS (
            SELECT EXTRACT(MONTH FROM chosen_date)::int AS m,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE result = 'PASS') AS passed,
                   COUNT(*) FILTER (WHERE result = 'FAIL') AS failed,
                   COUNT(*) FILTER (WHERE result = 'STOP') AS stopped
            FROM c GROUP BY 1
        )
        SELECT m.m AS month, COALESCE(d.total, 0) AS total, COALESCE(d.passed, 0) AS passed,
               COALESCE(d.failed, 0) AS failed, COALESCE(d.stopped, 0) AS stopped
        FROM generate_series(1, 12) AS m(m) LEFT JOIN data d ON d.m = m.m
        ORDER BY m.m
    """, params)
    return {"year": year, "months": rows}


def trend(conn, scope: Scope):
    """Yield over time: hourly when the scope covers one day, otherwise daily (gap-filled)."""
    cte, params = units_cte(scope)
    span = db.fetch_one(conn, f"""
        WITH {cte} SELECT MIN(chosen_date) AS lo, MAX(chosen_date) AS hi, COUNT(*) AS n FROM c
    """, params)
    if not span or not span["n"]:
        return {"unit": "day", "points": []}
    unit = "hour" if span["lo"] == span["hi"] else "day"
    if unit == "hour":
        rows = db.fetch_all(conn, f"""
            WITH {cte},
            data AS (
                SELECT date_trunc('hour', chosen_time) AS t, COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE result = 'PASS') AS passed
                FROM c GROUP BY 1
            ),
            hours AS (SELECT generate_series(MIN(t), MAX(t), interval '1 hour') AS t FROM data)
            SELECT h.t, COALESCE(d.total, 0) AS total, COALESCE(d.passed, 0) AS passed
            FROM hours h LEFT JOIN data d ON d.t = h.t ORDER BY h.t
        """, params)
    else:
        rows = db.fetch_all(conn, f"""
            WITH {cte},
            data AS (
                SELECT chosen_date AS t, COUNT(*) AS total, COUNT(*) FILTER (WHERE result = 'PASS') AS passed
                FROM c GROUP BY 1
            ),
            days AS (SELECT generate_series(MIN(t), MAX(t), interval '1 day')::date AS t FROM data)
            SELECT dd.t, COALESCE(d.total, 0) AS total, COALESCE(d.passed, 0) AS passed
            FROM days dd LEFT JOIN data d ON d.t = dd.t ORDER BY dd.t
        """, params)
    for r in rows:
        r["yield_pct"] = round(r["passed"] * 100.0 / r["total"], 2) if r["total"] else None
    return {"unit": unit, "points": rows}


def work_order_summary(conn, scope: Scope):
    cte, params = units_cte(scope)
    rows = db.fetch_all(conn, f"""
        WITH {cte},
        per_wo AS (
            SELECT wo_key,
                   COUNT(*)                                              AS total,
                   COUNT(*) FILTER (WHERE result = 'PASS')               AS passed,
                   COUNT(*) FILTER (WHERE result = 'FAIL')               AS failed,
                   COUNT(*) FILTER (WHERE result = 'STOP')               AS stopped,
                   ROUND(COUNT(*) FILTER (WHERE result = 'PASS') * 100.0 / NULLIF(COUNT(*), 0), 2) AS yield_pct,
                   COUNT(*) FILTER (WHERE attempts > 1)                  AS retry_pairs,
                   COUNT(*) FILTER (WHERE has_pass AND stop_count > 0 AND fail_count = 0 AND attempts > 1) AS stop_to_pass
            FROM c GROUP BY wo_key
        ),
        att AS (
            SELECT wo_key, SUM(attempts) AS test_attempts, SUM(dur_sum) AS dur_sum, SUM(dur_n) AS dur_n,
                   MIN(first_time) AS first_test, MAX(last_time) AS last_test
            FROM c GROUP BY wo_key
        )
        SELECT CASE WHEN p.wo_key = '' THEN '(unknown)' ELSE p.wo_key END AS work_order,
               p.total, a.test_attempts, p.passed, p.failed, p.stopped, p.yield_pct,
               ROUND(p.retry_pairs * 100.0 / NULLIF(p.total, 0), 2)  AS retry_rate,
               ROUND((a.dur_sum / NULLIF(a.dur_n, 0))::numeric, 2)    AS avg_duration_sec,
               a.first_test, a.last_test, p.stop_to_pass, t.target_total
        FROM per_wo p
        JOIN att a ON a.wo_key = p.wo_key
        LEFT JOIN alignment_targets t ON t.work_order = p.wo_key
        ORDER BY a.last_test DESC
    """, params)
    for r in rows:
        for k in ("yield_pct", "retry_rate", "avg_duration_sec"):
            r[k] = _round2(r[k])
    return rows


def retries(conn, scope: Scope, limit=200):
    """Units tested more than once, most attempts first; plus totals by risk bucket."""
    cte, params = units_cte(scope)
    counts = db.fetch_one(conn, f"""
        WITH {cte}
        SELECT COUNT(*) FILTER (WHERE attempts > 1)  AS units,
               COUNT(*) FILTER (WHERE attempts >= 4) AS high,
               COUNT(*) FILTER (WHERE attempts = 3)  AS medium,
               COUNT(*) FILTER (WHERE attempts = 2)  AS low
        FROM c
    """, params)
    rows = db.fetch_all(conn, f"""
        WITH {cte}
        SELECT NULLIF(wo_key, '') AS work_order, mac1, last_mac2 AS mac2, latest_date AS unit_date,
               attempts, pass_count, fail_count, stop_count,
               first_time AS first_attempt, last_time AS last_attempt,
               CASE WHEN attempts >= 4 THEN 'high' WHEN attempts >= 3 THEN 'medium' ELSE 'low' END AS retry_risk
        FROM c WHERE attempts > 1
        ORDER BY attempts DESC, last_time DESC
        LIMIT %s
    """, params + [limit])
    return {**counts, "items": rows}


def filter_options(conn):
    """Work orders by most recent activity, plus the overall date range."""
    wos = db.fetch_all(conn, """
        SELECT wo_key AS work_order, MIN(eff_date) AS first_date, MAX(eff_date) AS last_date,
               MAX(last_time) AS last_test, COUNT(DISTINCT mac1) AS units
        FROM mv_unit_day WHERE wo_key <> ''
        GROUP BY wo_key ORDER BY MAX(last_time) DESC, wo_key
    """)
    span = db.fetch_one(conn, "SELECT MIN(eff_date) AS min_date, MAX(eff_date) AS max_date FROM mv_unit_day")
    return {"work_orders": wos, **(span or {})}


def alignment(conn, scope: Scope):
    """Per work order: units vs planned quantity (alignment_targets) and the aligned yield."""
    rows = work_order_summary(conn, scope)
    totals = {"work_orders": len(rows), "gap": 0, "stop_to_pass": 0, "aligned_pass": 0, "aligned_total": 0}
    for r in rows:
        target = r.get("target_total")
        gap = max(0, target - r["total"]) if target is not None else 0
        r["gap"] = gap
        r["aligned_total"] = r["total"] + gap
        r["aligned_fail"] = r["failed"] + gap
        r["aligned_yield"] = round(r["passed"] * 100.0 / r["aligned_total"], 2) if r["aligned_total"] else None
        totals["gap"] += gap
        totals["stop_to_pass"] += r["stop_to_pass"] or 0
        totals["aligned_pass"] += r["passed"]
        totals["aligned_total"] += r["aligned_total"]
    totals["aligned_yield"] = (round(totals["aligned_pass"] * 100.0 / totals["aligned_total"], 2)
                               if totals["aligned_total"] else None)
    return {"items": rows, "totals": totals}


# ─── Raw-row queries (module_test, date-range filters use idx_mt_eff_date) ───

def fail_analysis(conn, scope: Scope, limit=20):
    where, params = raw_where(scope, ["result IN ('FAIL','STOP')", "fail_step_name IS NOT NULL"])
    return db.fetch_all(conn, f"""
        SELECT fail_step_name AS fail_step, COALESCE(fail_error_code, '') AS fail_error_code,
               COALESCE(fail_message, '') AS fail_message, COUNT(*) AS count
        FROM module_test {where}
        GROUP BY fail_step_name, fail_error_code, fail_message
        ORDER BY count DESC LIMIT %s
    """, params + [limit])


def config_analysis(conn, scope: Scope):
    where, params = raw_where(scope, ["result IN ('FAIL','STOP')", "fail_step_name IS NULL"])
    return db.fetch_all(conn, f"""
        SELECT result, COUNT(*) AS count FROM module_test {where}
        GROUP BY result ORDER BY count DESC
    """, params)


def top_fail_causes(conn, scope: Scope, limit=5):
    """Failure attempts grouped by error code / step — used by the overview and AI report."""
    where, params = raw_where(scope, ["result IN ('FAIL','STOP')"])
    return db.fetch_all(conn, f"""
        SELECT COALESCE(fail_error_code, '') AS error_code, COALESCE(fail_step_name, '') AS step,
               COALESCE(fail_category, '') AS category, COUNT(*) AS count
        FROM module_test {where}
        GROUP BY 1, 2, 3 ORDER BY count DESC, 1 LIMIT %s
    """, params + [limit])


def fails(conn, scope: Scope, page=1, page_size=50, search=None):
    extra = ["result IN ('FAIL','STOP')"]
    s_params = []
    if search:
        extra.append("(mac1 ILIKE %s OR mac2 ILIKE %s OR fail_step_name ILIKE %s OR fail_error_code ILIKE %s OR work_order ILIKE %s)")
        like = f"%{search}%"
        s_params = [like] * 5
    where, params = raw_where(scope, extra)
    params = params + s_params
    total = db.fetch_one(conn, f"SELECT COUNT(*) AS n FROM module_test {where}", params)["n"]
    pages = max(1, -(-total // page_size))
    page = min(max(1, page), pages)
    rows = db.fetch_all(conn, f"""
        SELECT id, work_order, mac1, mac2, COALESCE(unit_date, start_time::date) AS unit_date, start_time,
               test_duration_sec, result, fail_step_num, fail_step_name, fail_message,
               fail_error_code, fail_category, bdr_pass, edr1_pass, le_pass, bt_rx_pass, cal_pass,
               wifi24_tx_pass, wifi5_tx_pass
        FROM module_test {where}
        ORDER BY start_time DESC, id DESC LIMIT %s OFFSET %s
    """, params + [page_size, (page - 1) * page_size])
    return {"items": rows, "total": total, "page": page, "page_size": page_size, "pages": pages}


# ─── Result cache ────────────────────────────────────────────────────────────
# Everything the dashboard reads changes only when the views are refreshed (or a planned
# quantity is saved), so results are cached per worker under that data version. A refresh
# or a target change produces a new version; nothing stale is ever served.

_cache = OrderedDict()
_cache_lock = threading.Lock()
CACHE_MAX = 256


def data_version(conn):
    row = db.fetch_one(conn, """
        SELECT (SELECT updated_at FROM app_meta WHERE key = 'stats_refreshed_at') AS stats,
               (SELECT MAX(updated_at)::text || '/' || COUNT(*) FROM alignment_targets) AS targets
    """)
    return (str(row["stats"]), row["targets"])


def cached(conn, key, compute):
    k = (key, data_version(conn))
    with _cache_lock:
        if k in _cache:
            _cache.move_to_end(k)
            return _cache[k]
    value = compute()
    with _cache_lock:
        _cache[k] = value
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return value


# ─── View refresh ────────────────────────────────────────────────────────────

_refresh_lock = threading.Lock()


def refreshed_at(conn):
    row = db.fetch_one(conn, "SELECT updated_at FROM app_meta WHERE key = 'stats_refreshed_at'")
    return row["updated_at"] if row else None


def refresh(wait=False):
    """REFRESH MATERIALIZED VIEW CONCURRENTLY for both views (readers are never blocked).

    Returns the refresh time, or None when another worker is already refreshing and
    wait=False. With wait=True (after an upload or delete) it waits for that refresh
    and then runs its own, so the caller's change is included."""
    with _refresh_lock:
        conn = db.connect()
        try:
            conn.autocommit = True
            cur = conn.cursor()
            if wait:
                cur.execute("SELECT pg_advisory_lock(%s)", (config.STATS_REFRESH_LOCK_KEY,))
            else:
                cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (config.STATS_REFRESH_LOCK_KEY,))
                if not cur.fetchone()["ok"]:
                    return None
            try:
                t0 = time.perf_counter()
                cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_unit_day")
                cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_unit")
                cur.execute("""
                    INSERT INTO app_meta (key, value, updated_at) VALUES ('stats_refreshed_at', NOW()::text, NOW())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                    RETURNING updated_at
                """)
                at = cur.fetchone()["updated_at"]
                log.info("statistics views refreshed in %.0f ms", (time.perf_counter() - t0) * 1000)
                return at
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s)", (config.STATS_REFRESH_LOCK_KEY,))
        finally:
            conn.close()


def refresh_if_due():
    """Called by the scheduler: refresh when the last one is older than STATS_REFRESH_SEC."""
    with db.db() as conn:
        row = db.fetch_one(conn, """
            SELECT NOW() - updated_at >= %s AS due FROM app_meta WHERE key = 'stats_refreshed_at'
        """, (timedelta(seconds=config.STATS_REFRESH_SEC),))
    if row is None or row["due"]:
        return refresh(wait=False)
    return None
