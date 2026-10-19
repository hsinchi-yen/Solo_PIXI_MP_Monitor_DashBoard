"""
Solo PIXI Module Test Analysis — FastAPI Backend
"""
import os
import hashlib
import hmac
import queue
import shutil
import uuid
import zipfile
import psycopg2
import psycopg2.extras
from fastapi import FastAPI, Query, Header, Body, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

import json
import urllib.request
import urllib.error
import asyncio
import time
import threading
from ai_summary_helper import build_summary_messages
import auth_utils
import log_splitter
import upload_chat

app = FastAPI(title="Solo PIXI Module Test Analysis")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://pixi:pixipass@localhost:5433/pixi_test")
DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), "solo_pixi_dashboard.html")

# Administrator account (DB Tweak + account management); uploader accounts live in app_users
TWEAK_USER = os.environ.get("TWEAK_USER", "pixi")
TWEAK_PASS = os.environ.get("TWEAK_PASS", "pixipass")
SESSION_TTL_HOURS = float(os.environ.get("SESSION_TTL_HOURS", "8"))

# LLM AI Summary configurations
LLM_API_BASE = os.environ.get("LLM_API_BASE", "http://10.20.30.23:8000/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "tn8227")
LLM_MODEL = os.environ.get("LLM_MODEL", "Qwen3.8-27B-UD-Q6_K_L")
# Dense 27B model: thinking adds ~15x latency for no gain on templated reports.
LLM_ENABLE_THINKING = os.environ.get("LLM_ENABLE_THINKING", "false").lower() == "true"
LLM_REPORT_MAX_TOKENS = int(os.environ.get("LLM_REPORT_MAX_TOKENS", "1536"))
LLM_CHAT_TIMEOUT = 60  # per call; on timeout the uploader falls back to the form

# Log Uploader
UPLOAD_TMP_DIR = os.environ.get("UPLOAD_TMP_DIR", "/tmp/pixi_uploads")
UPLOAD_TTL_SEC = 3600
UPLOAD_MAX_LOG_ALL_BYTES = 500 * 1024 * 1024
UPLOAD_LOCK_KEY = 7360001  # pg advisory lock: one web upload at a time across workers
MIGRATION_LOCK_KEY = 7360002


LLM_STATUS_CACHE = {"status": "error", "connected": False}

async def update_llm_status_loop():
    while True:
        try:
            req = urllib.request.Request(f"{LLM_API_BASE}/models", headers={"Authorization": f"Bearer {LLM_API_KEY}"})
            response = await asyncio.to_thread(urllib.request.urlopen, req, timeout=3)
            if response.status == 200:
                LLM_STATUS_CACHE.update({"status": "ok", "connected": True})
            else:
                LLM_STATUS_CACHE.update({"status": "error", "connected": False})
        except Exception:
            LLM_STATUS_CACHE.update({"status": "error", "connected": False})
        await asyncio.sleep(30)

@app.on_event("startup")
async def start_llm_status_loop():
    asyncio.create_task(update_llm_status_loop())


def get_conn():
    return psycopg2.connect(DATABASE_URL)


# ─── Startup: DB migration ───────────────────────────────────────────────────
@app.on_event("startup")
def run_migrations():
    conn = get_conn()
    cur = conn.cursor()
    # Both uvicorn workers run this at startup; serialise so CREATE TABLE IF NOT EXISTS cannot race.
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,))
    for col, dtype in [("fail_error_code", "TEXT"), ("fail_category", "TEXT"), ("unit_date", "DATE")]:
        cur.execute(f"""
            DO $$ BEGIN
                ALTER TABLE module_test ADD COLUMN {col} {dtype};
            EXCEPTION WHEN duplicate_column THEN NULL;
            END $$;
        """)
    cur.execute("""
        UPDATE module_test
        SET unit_date = COALESCE(unit_date, start_time::date)
        WHERE start_time IS NOT NULL
          AND unit_date IS NULL
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mt_unit_date ON module_test(unit_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mt_unit_date_time ON module_test(unit_date, start_time)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mt_workorder_mac1_time ON module_test(work_order, mac1, start_time)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alignment_targets (
            work_order TEXT PRIMARY KEY,
            target_total INT NOT NULL,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS upload_log (
            id              TEXT PRIMARY KEY,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            finished_at     TIMESTAMPTZ,
            client_ip       TEXT,
            username        TEXT,
            user_role       TEXT,
            zip_name        TEXT,
            zip_size        BIGINT,
            work_order      TEXT,
            inferred_wo     TEXT,
            planned_qty     INT,
            prev_target     INT,
            unique_mac1     INT,
            total_records   INT,
            facts           JSONB,
            validation      JSONB,
            chat_transcript JSONB,
            mode            TEXT,
            status          TEXT,
            uploaded        INT,
            skipped         INT,
            failed          INT,
            failures        JSONB,
            reverted        INT,
            elapsed_sec     REAL,
            error           TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_upload_log_created ON upload_log(created_at DESC)")
    cur.execute("ALTER TABLE module_test ADD COLUMN IF NOT EXISTS upload_id TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_module_test_upload_id ON module_test(upload_id)")
    # Phase-1 test databases named the uploader column tweak_user.
    cur.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema = current_schema() AND table_name = 'upload_log'
                         AND column_name = 'tweak_user') THEN
                ALTER TABLE upload_log RENAME COLUMN tweak_user TO username;
            END IF;
        END $$;
    """)
    cur.execute("ALTER TABLE upload_log ADD COLUMN IF NOT EXISTS user_role TEXT")
    cur.execute("ALTER TABLE upload_log ADD COLUMN IF NOT EXISTS reverted_by TEXT")
    cur.execute("ALTER TABLE upload_log ADD COLUMN IF NOT EXISTS reverted_at TIMESTAMPTZ")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_upload_log_username ON upload_log(username, created_at DESC)")
    # Accounts: uploaders are rows here; the administrator is TWEAK_USER from the environment.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_users (
            id                  SERIAL PRIMARY KEY,
            username            TEXT NOT NULL UNIQUE,
            display_name        TEXT NOT NULL,
            department          TEXT,
            email               TEXT,
            password_hash       TEXT NOT NULL,
            enabled             BOOLEAN NOT NULL DEFAULT TRUE,
            created_at          TIMESTAMPTZ DEFAULT NOW(),
            created_by          TEXT,
            updated_at          TIMESTAMPTZ,
            password_changed_at TIMESTAMPTZ,
            last_login_at       TIMESTAMPTZ,
            last_login_ip       TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_sessions (
            token_hash  TEXT PRIMARY KEY,
            username    TEXT NOT NULL,
            role        TEXT NOT NULL,
            cred_fp     TEXT,
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            expires_at  TIMESTAMPTZ NOT NULL,
            client_ip   TEXT,
            revoked_at  TIMESTAMPTZ
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_app_sessions_username ON app_sessions(username)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS auth_log (
            id          BIGSERIAL PRIMARY KEY,
            at          TIMESTAMPTZ DEFAULT NOW(),
            username    TEXT,
            event       TEXT,
            success     BOOLEAN,
            reason      TEXT,
            client_ip   TEXT,
            user_agent  TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_auth_log_at ON auth_log(at DESC)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS account_audit (
            id          BIGSERIAL PRIMARY KEY,
            at          TIMESTAMPTZ DEFAULT NOW(),
            actor       TEXT,
            action      TEXT,
            target      TEXT,
            detail      JSONB,
            client_ip   TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_account_audit_at ON account_audit(at DESC)")
    conn.commit()
    cur.close()
    conn.close()


# ─── Latest record CTE (dedup retries — keep latest per work_order+mac1) ───
LATEST_CTE = """
WITH latest AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY COALESCE(work_order, ''), mac1 ORDER BY CASE WHEN result = 'PASS' THEN 1 ELSE 2 END ASC, start_time DESC) AS rn
    FROM module_test {where}
)
"""


def _build_where(unit_date=None, work_order=None, year=None, month=None, week=None, day=None):
    clauses = []
    params = []
    if unit_date:
        clauses.append("COALESCE(unit_date, start_time::date) = %s")
        params.append(unit_date)
    if work_order:
        clauses.append("work_order = %s")
        params.append(work_order)
    if year:
        clauses.append("EXTRACT(YEAR FROM COALESCE(unit_date, start_time::date)) = %s")
        params.append(int(year))
    if month:
        clauses.append("EXTRACT(MONTH FROM COALESCE(unit_date, start_time::date)) = %s")
        params.append(int(month))
    if week:
        clauses.append("EXTRACT(WEEK FROM COALESCE(unit_date, start_time::date)) = %s")
        params.append(int(week))
    if day:
        clauses.append("COALESCE(unit_date, start_time::date) = %s")
        params.append(day)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _resolve_trend_day(cur, unit_date=None, work_order=None, year=None, month=None, week=None, day=None):
    if day:
        return day
    if unit_date:
        return unit_date

    where, params = _build_where(
        work_order=work_order,
        year=year,
        month=month,
        week=week,
    )
    cur.execute(f"""
        WITH latest AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY COALESCE(work_order, ''), mac1 ORDER BY CASE WHEN result = 'PASS' THEN 1 ELSE 2 END ASC, start_time DESC) AS rn
            FROM module_test {where}
        )
        SELECT MAX(COALESCE(unit_date, start_time::date)) AS target_day
        FROM latest
        WHERE rn = 1
    """, params)
    row = cur.fetchone()
    return str(row['target_day']) if row and row['target_day'] else None


def _get_filter_options(work_order=None, year=None, month=None, week=None, day=None):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT DISTINCT EXTRACT(YEAR FROM COALESCE(unit_date, start_time::date))::int AS v FROM module_test ORDER BY v DESC")
    years = [r['v'] for r in cur.fetchall()]

    m_where, m_params = _build_where(work_order=work_order, year=year)
    cur.execute(
        f"SELECT DISTINCT EXTRACT(MONTH FROM COALESCE(unit_date, start_time::date))::int AS v FROM module_test {m_where} ORDER BY v DESC",
        m_params,
    )
    months = [r['v'] for r in cur.fetchall()]

    w_where, w_params = _build_where(work_order=work_order, year=year, month=month)
    cur.execute(
        f"SELECT DISTINCT EXTRACT(WEEK FROM COALESCE(unit_date, start_time::date))::int AS v FROM module_test {w_where} ORDER BY v DESC",
        w_params,
    )
    weeks = [r['v'] for r in cur.fetchall()]

    d_where, d_params = _build_where(work_order=work_order, year=year, month=month, week=week)
    cur.execute(
        f"SELECT DISTINCT COALESCE(unit_date, start_time::date) AS v FROM module_test {d_where} ORDER BY v DESC",
        d_params,
    )
    days = [str(r['v']) for r in cur.fetchall()]

    u_where, u_params = _build_where(work_order=work_order, year=year, month=month, week=week, day=day)
    cur.execute(
        f"SELECT DISTINCT COALESCE(unit_date, start_time::date) AS v FROM module_test {u_where} ORDER BY v DESC",
        u_params,
    )
    unit_dates = [str(r['v']) for r in cur.fetchall()]

    wo_where, wo_params = _build_where(year=year, month=month, week=week, day=day)
    cur.execute(f"""
        SELECT work_order AS v
        FROM module_test {wo_where}
        {'AND' if wo_where else 'WHERE'} work_order IS NOT NULL AND work_order <> ''
        GROUP BY work_order
        ORDER BY MAX(start_time) DESC, work_order
    """, wo_params)
    work_orders = [r['v'] for r in cur.fetchall()]

    cur.close()
    conn.close()
    return {
        "years": years,
        "months": months,
        "weeks": weeks,
        "days": days,
        "unit_dates": unit_dates,
        "work_orders": work_orders,
    }


def _normalize_page_size(page_size):
    if page_size is None:
        return 50
    if isinstance(page_size, str):
        if page_size.lower() == "all":
            return None
        try:
            page_size = int(page_size)
        except ValueError as exc:
            raise ValueError("Invalid page_size") from exc
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    return page_size


# ─── Data Alignment ──────────────────────────────────────────────────────────
@app.get("/api/alignment-targets")
def api_get_alignment_targets():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT work_order, target_total FROM alignment_targets")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {row["work_order"]: row["target_total"] for row in rows}

@app.post("/api/alignment-targets")
def api_set_alignment_targets(targets: dict = Body(...)):
    conn = get_conn()
    cur = conn.cursor()
    for wo, target in targets.items():
        if target is None or target == "":
            cur.execute("DELETE FROM alignment_targets WHERE work_order = %s", (wo,))
        else:
            cur.execute("""
                INSERT INTO alignment_targets (work_order, target_total, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (work_order) DO UPDATE
                SET target_total = EXCLUDED.target_total, updated_at = NOW()
            """, (wo, target))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "ok"}


# ─── Serve dashboard ─────────────────────────────────────────────────────────
@app.get("/")
def serve_dashboard():
    return FileResponse(DASHBOARD_PATH, media_type="text/html")


@app.get("/health")
def health():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return {"status": "ok"}
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)


# ─── Summary KPIs ────────────────────────────────────────────────────────────
@app.get("/api/summary")
def api_summary(unit_date: str = None, work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None):
    where, params = _build_where(unit_date, work_order, year, month, week, day)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    sql = f"""
    {LATEST_CTE.format(where=where)}
    SELECT
        COUNT(*)                                           AS total,
        COUNT(*) FILTER (WHERE result = 'PASS')            AS pass,
        COUNT(*) FILTER (WHERE result = 'FAIL')            AS fail,
        COUNT(*) FILTER (WHERE result = 'STOP')            AS stop,
        ROUND(COUNT(*) FILTER (WHERE result = 'PASS') * 100.0
              / NULLIF(COUNT(*), 0), 2)                    AS yield_pct
    FROM latest WHERE rn = 1
    """
    cur.execute(sql, params)
    row = cur.fetchone()

    cur.execute(f"""
        WITH filtered AS (
            SELECT * FROM module_test {where}
        ),
        grouped_pairs AS (
            SELECT COALESCE(work_order, '') AS work_order_key, mac1, COUNT(*) AS attempts
            FROM filtered
            GROUP BY COALESCE(work_order, ''), mac1
        )
        SELECT
            COUNT(*) FILTER (WHERE attempts > 1) AS retry_macs,
            COUNT(*) AS total_macs
        FROM grouped_pairs
    """, params)
    rr = cur.fetchone()
    retry_units = rr['retry_macs'] or 0
    retry_rate = round(retry_units * 100.0 / max(rr['total_macs'] or 0, 1), 2)

    cur.close()
    conn.close()
    return {**dict(row), "retry_units": retry_units, "retry_rate": retry_rate}


# ─── Yield trend (hourly, gap-filled) ───────────────────────────────────────
@app.get("/api/yield-trend")
def api_yield_trend(unit_date: str = None, work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    target_day = _resolve_trend_day(
        cur,
        unit_date=unit_date,
        work_order=work_order,
        year=year,
        month=month,
        week=week,
        day=day,
    )
    if not target_day:
        cur.close()
        conn.close()
        return []

    where, params = _build_where(day=target_day, work_order=work_order, year=year, month=month, week=week)

    sql = f"""
    WITH latest AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY COALESCE(work_order, ''), mac1 ORDER BY CASE WHEN result = 'PASS' THEN 1 ELSE 2 END ASC, start_time DESC) AS rn
        FROM module_test {where}
    ),
    dedup AS (
        SELECT * FROM latest WHERE rn = 1
    ),
    hours AS (
        SELECT generate_series(
            %s::date + time '07:00',
            %s::date + time '19:00',
            '1 hour'::interval
        ) AS hour
    ),
    data AS (
        SELECT date_trunc('hour', start_time) AS hour,
               COUNT(*)                                              AS total,
               COUNT(*) FILTER (WHERE result = 'PASS')               AS passed,
               ROUND(COUNT(*) FILTER (WHERE result = 'PASS') * 100.0
                     / NULLIF(COUNT(*), 0), 2)                       AS yield_pct
        FROM dedup
        WHERE start_time >= %s::date + time '07:00'
          AND start_time < %s::date + time '20:00'
        GROUP BY 1
    )
    SELECT h.hour, COALESCE(d.total, 0) AS total,
           COALESCE(d.passed, 0) AS passed,
           COALESCE(d.yield_pct, 0) AS yield_pct
    FROM hours h LEFT JOIN data d ON h.hour = d.hour
    ORDER BY h.hour
    """
    cur.execute(sql, params + [target_day, target_day, target_day, target_day])
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


# ─── Pass/Fail/Stop split ───────────────────────────────────────────────────
@app.get("/api/pass-fail-split")
def api_pass_fail_split(unit_date: str = None, work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None):
    where, params = _build_where(unit_date, work_order, year, month, week, day)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"""
        WITH latest AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY COALESCE(work_order, ''), mac1 ORDER BY CASE WHEN result = 'PASS' THEN 1 ELSE 2 END ASC, start_time DESC) AS rn
            FROM module_test {where}
        )
        SELECT result, COUNT(*) AS count
        FROM latest
        WHERE rn = 1
        GROUP BY result
        ORDER BY result
    """, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


# ─── Fail analysis (TestFail — identified causes only) ──────────────────────
@app.get("/api/fail-analysis")
def api_fail_analysis(unit_date: str = None, work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None):
    where, params = _build_where(unit_date, work_order, year, month, week, day)
    extra = " AND result IN ('FAIL','STOP') AND fail_step_name IS NOT NULL" if where else "WHERE result IN ('FAIL','STOP') AND fail_step_name IS NOT NULL"
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"""
        SELECT fail_step_name AS fail_step,
               COALESCE(fail_error_code, '') AS fail_error_code,
               COALESCE(fail_message, '') AS fail_message,
               COUNT(*) AS count
        FROM module_test {where} {extra}
        GROUP BY fail_step_name, fail_error_code, fail_message
        ORDER BY count DESC LIMIT 20
    """, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


# ─── Config analysis (DeviceConfigure — unidentified FAIL/STOP) ──────────────
@app.get("/api/config-analysis")
def api_config_analysis(unit_date: str = None, work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None):
    where, params = _build_where(unit_date, work_order, year, month, week, day)
    extra = (" AND result IN ('FAIL','STOP') AND fail_step_name IS NULL"
             if where else
             "WHERE result IN ('FAIL','STOP') AND fail_step_name IS NULL")
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"""
        SELECT result, COUNT(*) AS count
        FROM module_test {where} {extra}
        GROUP BY result
        ORDER BY count DESC
    """, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


# ─── Monthly test count (full Jan-Dec axis) ──────────────────────────────────
@app.get("/api/monthly-count")
def api_monthly_count(work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if not year:
        y_where, y_params = _build_where(work_order=work_order, month=month, week=week, day=day)
        cur.execute(f"SELECT EXTRACT(YEAR FROM MAX(COALESCE(unit_date, start_time::date)))::int AS y FROM module_test {y_where}", y_params)
        r = cur.fetchone()
        year = r['y'] if r and r['y'] else 2024

    where, params = _build_where(work_order=work_order, year=year, month=month, week=week, day=day)
    cur.execute(f"""
        WITH months AS (
            SELECT generate_series(1, 12) AS m
        ),
        latest AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY COALESCE(work_order, ''), mac1 ORDER BY CASE WHEN result = 'PASS' THEN 1 ELSE 2 END ASC, start_time DESC) AS rn
            FROM module_test {where}
        ),
        data AS (
            SELECT EXTRACT(MONTH FROM COALESCE(unit_date, start_time::date))::int AS m,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE result = 'PASS') AS passed,
                   COUNT(*) FILTER (WHERE result = 'FAIL') AS failed,
                   COUNT(*) FILTER (WHERE result = 'STOP') AS stopped
            FROM latest
            WHERE rn = 1
            GROUP BY 1
        )
        SELECT months.m AS month, COALESCE(d.total, 0) AS total,
               COALESCE(d.passed, 0) AS passed,
               COALESCE(d.failed, 0) AS failed,
               COALESCE(d.stopped, 0) AS stopped
        FROM months LEFT JOIN data d ON months.m = d.m
        ORDER BY months.m
    """, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"year": year, "months": [dict(r) for r in rows]}


# ─── Public raw log download (read-only, no auth) ───────────────────────────
@app.get("/api/raw-log/{record_id}")
def api_raw_log(record_id: int):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT raw_log, source_file FROM module_test WHERE id = %s", (record_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row or not row['raw_log']:
        return JSONResponse({"error": "Raw log not available"}, status_code=404)
    filename = row['source_file'] or f"record_{record_id}.txt"
    return PlainTextResponse(
        content=row['raw_log'],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# ─── BT metrics ─────────────────────────────────────────────────────────────
@app.get("/api/bt-metrics")
def api_bt_metrics(unit_date: str = None, work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None):
    where, params = _build_where(unit_date, work_order, year, month, week, day)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # BDR Power distribution
    cur.execute(f"""
        SELECT bdr_power FROM module_test {where}
        {'AND' if where else 'WHERE'} bdr_power IS NOT NULL
        ORDER BY bdr_power
    """, params)
    bdr_powers = [r['bdr_power'] for r in cur.fetchall()]

    # EDR DEVM
    cur.execute(f"""
        SELECT edr1_devm_avg, edr2_devm_avg FROM module_test {where}
        {'AND' if where else 'WHERE'} edr1_devm_avg IS NOT NULL
    """, params)
    edr_rows = cur.fetchall()
    edr1_devm = [r['edr1_devm_avg'] for r in edr_rows]
    edr2_devm = [r['edr2_devm_avg'] for r in edr_rows]

    # BER/PER summary
    cur.execute(f"""
        SELECT
            COUNT(*) FILTER (WHERE bt_rx_pass = true)  AS bt_rx_passed,
            COUNT(*) FILTER (WHERE bt_rx_pass = false) AS bt_rx_failed,
            COUNT(*) FILTER (WHERE bdr_pass = true)    AS bdr_passed,
            COUNT(*) FILTER (WHERE bdr_pass = false)   AS bdr_failed,
            COUNT(*) FILTER (WHERE le_pass = true)     AS le_passed,
            COUNT(*) FILTER (WHERE le_pass = false)    AS le_failed,
            AVG(ber_2441) AS avg_ber_2441,
            AVG(ber_2480) AS avg_ber_2480,
            AVG(per_le) AS avg_per_le
        FROM module_test {where}
    """, params)
    bt_summary = dict(cur.fetchone())

    cur.close()
    conn.close()
    return {
        "bdr_powers": bdr_powers,
        "edr1_devm": edr1_devm,
        "edr2_devm": edr2_devm,
        "bt_summary": bt_summary,
    }


# ─── WiFi metrics ───────────────────────────────────────────────────────────
@app.get("/api/wifi-metrics")
def api_wifi_metrics(unit_date: str = None, work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None):
    where, params = _build_where(unit_date, work_order, year, month, week, day)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    not_null = "AND" if where else "WHERE"

    # 2.4G EVM by rate
    cur.execute(f"""
        SELECT
            AVG(wifi24_cck11_evm) AS cck11, AVG(wifi24_ofdm54_evm) AS ofdm54,
            AVG(wifi24_ht20_evm) AS ht20, AVG(wifi24_ht40_evm) AS ht40
        FROM module_test {where} {not_null} wifi24_cck11_evm IS NOT NULL
    """, params)
    avg_24 = dict(cur.fetchone())

    # 5G EVM by rate
    cur.execute(f"""
        SELECT
            AVG(wifi5_ofdm54_evm) AS ofdm54, AVG(wifi5_ht20_evm) AS ht20,
            AVG(wifi5_ht40_evm) AS ht40, AVG(wifi5_vht80_evm) AS vht80
        FROM module_test {where} {not_null} wifi5_ofdm54_evm IS NOT NULL
    """, params)
    avg_5 = dict(cur.fetchone())

    # EVM distributions (all values for histograms)
    cur.execute(f"""
        SELECT wifi24_cck11_evm, wifi24_ofdm54_evm, wifi24_ht20_evm, wifi24_ht40_evm,
               wifi5_ofdm54_evm, wifi5_ht20_evm, wifi5_ht40_evm, wifi5_vht80_evm,
               wifi24_per_max, wifi5_per_max
        FROM module_test {where} {not_null} wifi24_cck11_evm IS NOT NULL
    """, params)
    evm_rows = [dict(r) for r in cur.fetchall()]

    # Xtal calibration
    cur.execute(f"""
        SELECT xtal_cap, xtal_freq_error_ppm
        FROM module_test {where} {not_null} xtal_cap IS NOT NULL
    """, params)
    xtal_rows = [dict(r) for r in cur.fetchall()]

    cur.close()
    conn.close()
    return {
        "avg_evm_24g": avg_24,
        "avg_evm_5g": avg_5,
        "evm_data": evm_rows,
        "xtal_data": xtal_rows,
    }


# ─── Calibration endpoint ───────────────────────────────────────────────────
@app.get("/api/calibration")
def api_calibration(unit_date: str = None, work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None):
    where, params = _build_where(unit_date, work_order, year, month, week, day)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    not_null = "AND" if where else "WHERE"
    cur.execute(f"""
        SELECT xtal_cap, xtal_freq_error_ppm
        FROM module_test {where} {not_null} xtal_cap IS NOT NULL
        ORDER BY start_time
    """, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# ─── Work Orders ─────────────────────────────────────────────────────────────
@app.get("/api/date-units")
@app.get("/api/work-orders")
def api_work_orders(unit_date: str = None, work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None):
    where, params = _build_where(unit_date=unit_date, work_order=work_order, year=year, month=month, week=week, day=day)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"""
        WITH filtered AS (
            SELECT *, COALESCE(unit_date, start_time::date) AS effective_unit_date
            FROM module_test {where}
        ),
        latest AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY COALESCE(work_order, ''), mac1 ORDER BY CASE WHEN result = 'PASS' THEN 1 ELSE 2 END ASC, start_time DESC) AS rn
            FROM filtered
        ),
        latest_agg AS (
            SELECT
                effective_unit_date AS unit_date,
                COUNT(*)                                              AS total,
                COUNT(*) FILTER (WHERE result = 'PASS')               AS passed,
                COUNT(*) FILTER (WHERE result = 'FAIL')               AS failed,
                COUNT(*) FILTER (WHERE result = 'STOP')               AS stopped,
                ROUND(COUNT(*) FILTER (WHERE result = 'PASS') * 100.0
                      / NULLIF(COUNT(*), 0), 2)                       AS yield_pct
            FROM latest
            WHERE rn = 1
            GROUP BY effective_unit_date
        ),
        attempts AS (
            SELECT
                effective_unit_date AS unit_date,
                COUNT(*)                                              AS test_attempts,
                MIN(start_time)                                       AS first_test,
                MAX(start_time)                                       AS last_test,
                COUNT(DISTINCT (COALESCE(work_order, ''), mac1))      AS total_pairs
            FROM filtered
            GROUP BY effective_unit_date
        ),
        retry AS (
            SELECT
                effective_unit_date AS unit_date,
                COUNT(*) AS retry_pairs
            FROM (
                SELECT effective_unit_date, COALESCE(work_order, '') AS work_order_key, mac1
                FROM filtered
                GROUP BY effective_unit_date, COALESCE(work_order, ''), mac1
                HAVING COUNT(*) > 1
            ) grouped_retry
            GROUP BY effective_unit_date
        )
        SELECT
            l.unit_date,
            l.total,
            a.test_attempts,
            l.passed,
            l.failed,
            l.stopped,
            l.yield_pct,
            ROUND(COALESCE(r.retry_pairs, 0) * 100.0 / NULLIF(a.total_pairs, 0), 2) AS retry_rate,
            a.first_test,
            a.last_test
        FROM latest_agg l
        JOIN attempts a ON a.unit_date = l.unit_date
        LEFT JOIN retry r ON r.unit_date = l.unit_date
        ORDER BY a.last_test DESC
    """, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# ─── Work Order Summary (grouped by work_order) ──────────────────────────────
@app.get("/api/work-order-summary")
def api_work_order_summary(unit_date: str = None, work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None):
    where, params = _build_where(unit_date=unit_date, work_order=work_order, year=year, month=month, week=week, day=day)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"""
        WITH filtered AS (
            SELECT *, COALESCE(work_order, '(unknown)') AS effective_wo
            FROM module_test {where}
        ),
        latest AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY effective_wo, mac1 ORDER BY CASE WHEN result = 'PASS' THEN 1 ELSE 2 END ASC, start_time DESC) AS rn
            FROM filtered
        ),
        latest_agg AS (
            SELECT
                effective_wo                                          AS work_order,
                COUNT(*)                                              AS total,
                COUNT(*) FILTER (WHERE result = 'PASS')               AS passed,
                COUNT(*) FILTER (WHERE result = 'FAIL')               AS failed,
                COUNT(*) FILTER (WHERE result = 'STOP')               AS stopped,
                ROUND(COUNT(*) FILTER (WHERE result = 'PASS') * 100.0
                      / NULLIF(COUNT(*), 0), 2)                       AS yield_pct
            FROM latest
            WHERE rn = 1
            GROUP BY effective_wo
        ),
        attempts AS (
            SELECT
                effective_wo                                          AS work_order,
                COUNT(*)                                              AS test_attempts,
                ROUND(AVG(test_duration_sec)::numeric, 2)             AS avg_duration_sec,
                MIN(start_time)                                       AS first_test,
                MAX(start_time)                                       AS last_test,
                COUNT(DISTINCT mac1)                                  AS total_pairs
            FROM filtered
            GROUP BY effective_wo
        ),
        retry AS (
            SELECT
                effective_wo AS work_order,
                COUNT(*) AS retry_pairs
            FROM (
                SELECT effective_wo, mac1
                FROM filtered
                GROUP BY effective_wo, mac1
                HAVING COUNT(*) > 1
            ) grouped_retry
            GROUP BY effective_wo
        )
        SELECT
            l.work_order,
            l.total,
            a.test_attempts,
            l.passed,
            l.failed,
            l.stopped,
            l.yield_pct,
            ROUND(COALESCE(r.retry_pairs, 0) * 100.0 / NULLIF(a.total_pairs, 0), 2) AS retry_rate,
            a.avg_duration_sec,
            a.first_test,
            a.last_test
        FROM latest_agg l
        JOIN attempts a ON a.work_order = l.work_order
        LEFT JOIN retry r ON r.work_order = l.work_order
        ORDER BY a.last_test DESC
    """, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# ─── Fails (FAIL + STOP) ────────────────────────────────────────────────────
@app.get("/api/fails")
def api_fails(unit_date: str = None, work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None, limit: int = 50):
    where, params = _build_where(unit_date, work_order, year, month, week, day)
    extra = " AND result IN ('FAIL','STOP')" if where else "WHERE result IN ('FAIL','STOP')"
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"""
        SELECT id, work_order, mac1, mac2, COALESCE(unit_date, start_time::date) AS unit_date, start_time, test_duration_sec,
               result, fail_step_num, fail_step_name, fail_message,
               fail_error_code, fail_category,
               bdr_pass, edr1_pass, le_pass, bt_rx_pass, cal_pass,
               wifi24_tx_pass, wifi5_tx_pass
        FROM module_test {where} {extra}
        ORDER BY start_time DESC LIMIT %s
    """, params + [limit])
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# ─── Retries ─────────────────────────────────────────────────────────────────
@app.get("/api/retries")
def api_retries(unit_date: str = None, work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None):
    where, params = _build_where(unit_date, work_order, year, month, week, day)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"""
        WITH filtered AS (
            SELECT *
            FROM module_test {where}
        ),
        grouped AS (
            SELECT
                COALESCE(work_order, '') AS work_order_key,
                mac1,
                COUNT(*) AS attempts,
                COUNT(*) FILTER (WHERE result = 'PASS') AS pass_count,
                COUNT(*) FILTER (WHERE result = 'FAIL') AS fail_count,
                COUNT(*) FILTER (WHERE result = 'STOP') AS stop_count,
                MIN(start_time) AS first_attempt,
                MAX(start_time) AS last_attempt
            FROM filtered
            GROUP BY COALESCE(work_order, ''), mac1
            HAVING COUNT(*) > 1
        ),
        latest_meta AS (
            SELECT DISTINCT ON (COALESCE(work_order, ''), mac1)
                COALESCE(work_order, '') AS work_order_key,
                NULLIF(work_order, '') AS work_order,
                mac1,
                mac2,
                COALESCE(unit_date, start_time::date) AS unit_date
            FROM filtered
            ORDER BY COALESCE(work_order, ''), mac1, start_time DESC
        )
        SELECT
            m.work_order,
            g.mac1,
            m.mac2,
            m.unit_date,
            g.attempts,
            g.pass_count,
            g.fail_count,
            g.stop_count,
            g.first_attempt,
            g.last_attempt,
            CASE
                WHEN g.attempts >= 4 THEN 'high'
                WHEN g.attempts >= 3 THEN 'medium'
                ELSE 'low'
            END AS retry_risk
        FROM grouped g
        JOIN latest_meta m
          ON m.work_order_key = g.work_order_key AND m.mac1 = g.mac1
        ORDER BY g.attempts DESC, g.last_attempt DESC
    """, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# ─── MAC1 Range Analysis ──────────────────────────────────────────────────────
@app.get("/api/mac-range")
def api_mac_range(unit_date: str = None, work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None):
    where, params = _build_where(unit_date, work_order, year, month, week, day)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"""
        WITH mac_summary AS (
            SELECT
                mac1,
                COUNT(*) FILTER (WHERE result = 'PASS') AS pass_count,
                COUNT(*) FILTER (WHERE result = 'FAIL') AS fail_count,
                COUNT(*) FILTER (WHERE result = 'STOP') AS stop_count
            FROM module_test {where}
            GROUP BY mac1
        )
        SELECT
            MIN(mac1)                                                          AS mac1_min,
            MAX(mac1)                                                          AS mac1_max,
            COUNT(*)                                                           AS unique_mac1,
            COUNT(*) FILTER (WHERE pass_count > 0)                            AS pass_mac1,
            COUNT(*) FILTER (WHERE pass_count = 0 AND fail_count > 0)         AS true_fail_mac1,
            COUNT(*) FILTER (WHERE pass_count = 0 AND stop_count > 0)         AS true_stop_mac1
        FROM mac_summary
    """, params)
    summary = dict(cur.fetchone())
    cur.execute(f"""
        WITH mac_summary AS (
            SELECT
                mac1,
                MAX(COALESCE(work_order, ''))                      AS work_order,
                COUNT(*) FILTER (WHERE result = 'PASS')            AS pass_count,
                COUNT(*) FILTER (WHERE result = 'FAIL')            AS fail_count,
                COUNT(*) FILTER (WHERE result = 'STOP')            AS stop_count,
                MAX(start_time)                                    AS last_attempt
            FROM module_test {where}
            GROUP BY mac1
        )
        SELECT mac1, work_order, fail_count, stop_count, last_attempt,
               CASE WHEN fail_count > 0 THEN 'FAIL' ELSE 'STOP' END AS true_status
        FROM mac_summary
        WHERE pass_count = 0 AND (fail_count > 0 OR stop_count > 0)
        ORDER BY mac1
    """, params)
    incidents = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return {**summary, "true_incidents": incidents}


# ─── Test duration distribution ──────────────────────────────────────────────
@app.get("/api/test-duration")
def api_test_duration(unit_date: str = None, work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None):
    where, params = _build_where(unit_date, work_order, year, month, week, day)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    not_null = "AND" if where else "WHERE"
    cur.execute(f"""
        SELECT test_duration_sec, result
        FROM module_test {where} {not_null} test_duration_sec IS NOT NULL
        ORDER BY start_time
    """, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# ─── Filter options (cascading) ──────────────────────────────────────────────
@app.get("/api/filter-options")
def api_filter_options(work_order: str = None, year: int = None, month: int = None, week: int = None, day: str = None):
    """Return available filter values for cascading dropdowns."""
    return _get_filter_options(work_order=work_order, year=year, month=month, week=week, day=day)


# ═══════════════════════════════════════════════════════════════════════════════
# Accounts & sessions
# The X-Tweak-Token header carries a random session token (only its SHA-256 is
# stored). role 'admin' = TWEAK_USER/TWEAK_PASS (DB Tweak, accounts, every upload);
# role 'uploader' = app_users row (Log Uploader, own batches only).
# ═══════════════════════════════════════════════════════════════════════════════

def _client_ip(request):
    return request.headers.get("x-real-ip") or (request.client.host if request.client else None)


def _admin_fingerprint():
    """Bound into admin sessions so changing TWEAK_USER/TWEAK_PASS invalidates them."""
    return hashlib.sha256(f"{TWEAK_USER}\0{TWEAK_PASS}".encode()).hexdigest()[:16]


def _session_identity(token):
    """Session token → {username, role, display_name, expires_at}, or None when missing/expired/revoked."""
    if not token or len(token) > 128:
        return None
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT s.username, s.role, s.cred_fp, s.expires_at, u.display_name, u.enabled
            FROM app_sessions s LEFT JOIN app_users u ON u.username = s.username
            WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > NOW()
        """, (auth_utils.token_hash(token),))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    if row["role"] == "admin":
        if row["username"] != TWEAK_USER or row["cred_fp"] != _admin_fingerprint():
            return None
    elif not row["enabled"]:
        return None
    return {"username": row["username"], "role": row["role"],
            "display_name": row["display_name"] or row["username"], "expires_at": row["expires_at"]}


def _authenticate(token, admin=False):
    """Returns (identity, None) or (None, error response): 401 = sign in again, 403 = not allowed."""
    identity = _session_identity(token)
    if not identity:
        return None, JSONResponse({"error": "Unauthorized"}, status_code=401)
    if admin and identity["role"] != "admin":
        return None, JSONResponse({"error": "此功能僅限管理者使用"}, status_code=403)
    return identity, None


def _auth_log(cur, request, username, event, success, reason=None):
    cur.execute("""
        INSERT INTO auth_log (username, event, success, reason, client_ip, user_agent)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, ((username or "")[:64], event, success, reason, _client_ip(request),
          (request.headers.get("user-agent") or "")[:200]))


def _audit(cur, request, actor, action, target, detail=None):
    cur.execute("""
        INSERT INTO account_audit (actor, action, target, detail, client_ip)
        VALUES (%s, %s, %s, %s, %s)
    """, (actor, action, target, psycopg2.extras.Json(detail) if detail else None, _client_ip(request)))


def _revoke_sessions(cur, username, keep_token_hash=None):
    cur.execute("""
        UPDATE app_sessions SET revoked_at = NOW()
        WHERE username = %s AND revoked_at IS NULL AND token_hash IS DISTINCT FROM %s
    """, (username, keep_token_hash))


@app.post("/api/auth/login")
@app.post("/api/tweak/login")
def auth_login(request: Request, body: dict = Body(...)):
    """Sign in as the administrator or an uploader. Returns a session token valid for SESSION_TTL_HOURS."""
    raw_user = str(body.get("username") or "").strip()
    password = body.get("password") if isinstance(body.get("password"), str) else ""
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if raw_user == TWEAK_USER:
            username, role, display_name = TWEAK_USER, "admin", TWEAK_USER
            ok = hmac.compare_digest(password.encode(), TWEAK_PASS.encode())
            reason = None if ok else "bad_password"
        else:
            username, role = auth_utils.normalize_username(raw_user), "uploader"
            cur.execute("SELECT display_name, password_hash, enabled FROM app_users WHERE username = %s", (username,))
            user = cur.fetchone()
            ok = auth_utils.verify_password(password, user["password_hash"] if user else auth_utils.dummy_hash())
            display_name = user["display_name"] if user else None
            if not user:
                ok, reason = False, "unknown_user"
            elif not ok:
                reason = "bad_password"
            elif not user["enabled"]:
                ok, reason = False, "disabled"
            else:
                reason = None
        if not ok:
            _auth_log(cur, request, raw_user, "login", False, reason)
            conn.commit()
            if reason == "disabled":
                return JSONResponse({"ok": False, "message": "此帳號已停用，請聯絡管理者"}, status_code=403)
            return JSONResponse({"ok": False, "message": "帳號或密碼錯誤"}, status_code=401)

        cur.execute("DELETE FROM app_sessions WHERE expires_at < NOW() - INTERVAL '7 days'")
        token = auth_utils.new_session_token()
        cur.execute("""
            INSERT INTO app_sessions (token_hash, username, role, cred_fp, expires_at, client_ip)
            VALUES (%s, %s, %s, %s, NOW() + make_interval(secs => %s), %s)
            RETURNING expires_at
        """, (auth_utils.token_hash(token), username, role,
              _admin_fingerprint() if role == "admin" else None, SESSION_TTL_HOURS * 3600, _client_ip(request)))
        expires_at = cur.fetchone()["expires_at"]
        if role == "uploader":
            cur.execute("UPDATE app_users SET last_login_at = NOW(), last_login_ip = %s WHERE username = %s",
                        (_client_ip(request), username))
        _auth_log(cur, request, username, "login", True)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "token": token, "role": role, "username": username,
            "display_name": display_name, "expires_at": expires_at}


@app.post("/api/auth/logout")
def auth_logout(request: Request, x_tweak_token: str = Header(None)):
    identity = _session_identity(x_tweak_token)
    if not identity:
        return {"ok": True}
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE app_sessions SET revoked_at = NOW() WHERE token_hash = %s",
                    (auth_utils.token_hash(x_tweak_token),))
        _auth_log(cur, request, identity["username"], "logout", True)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(x_tweak_token: str = Header(None)):
    identity, denied = _authenticate(x_tweak_token)
    return denied or identity


@app.post("/api/auth/change-password")
def auth_change_password(request: Request, body: dict = Body(...), x_tweak_token: str = Header(None)):
    """Uploaders change their own password; their other sessions are signed out, this one stays."""
    identity, denied = _authenticate(x_tweak_token)
    if denied:
        return denied
    if identity["role"] != "uploader":
        return JSONResponse({"error": "管理者密碼由伺服器環境變數設定，無法在網頁上修改"}, status_code=400)
    old = body.get("old_password") if isinstance(body.get("old_password"), str) else ""
    new = body.get("new_password")
    error = auth_utils.password_error(new)
    if error:
        return JSONResponse({"error": error}, status_code=400)
    if new == old:
        return JSONResponse({"error": "新密碼不可以和舊密碼相同。"}, status_code=400)
    username = identity["username"]
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT password_hash FROM app_users WHERE username = %s", (username,))
        row = cur.fetchone()
        if not row or not auth_utils.verify_password(old, row[0]):
            _auth_log(cur, request, username, "change_password", False, "bad_password")
            conn.commit()
            return JSONResponse({"error": "舊密碼不正確。"}, status_code=400)
        cur.execute("""
            UPDATE app_users SET password_hash = %s, password_changed_at = NOW(), updated_at = NOW()
            WHERE username = %s
        """, (auth_utils.hash_password(new), username))
        _revoke_sessions(cur, username, keep_token_hash=auth_utils.token_hash(x_tweak_token))
        _audit(cur, request, username, "change_password", username)
        _auth_log(cur, request, username, "change_password", True)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ─── Account management (administrator only) ────────────────────────────────

USER_FIELDS = ("username", "display_name", "department", "email", "enabled", "created_at", "created_by",
               "updated_at", "password_changed_at", "last_login_at", "last_login_ip")


def _upload_stats_sql(where):
    return f"""
        SELECT username,
               COUNT(*) FILTER (WHERE status = 'done')                       AS uploads,
               COALESCE(SUM(uploaded) FILTER (WHERE status = 'done'), 0)      AS uploaded_rows,
               COUNT(*) FILTER (WHERE status = 'reverted')                   AS reverted_batches,
               MAX(created_at) FILTER (WHERE status IN ('done', 'failed', 'reverted')) AS last_upload_at
        FROM upload_log {where} GROUP BY username
    """


@app.get("/api/admin/users")
def admin_list_users(x_tweak_token: str = Header(None)):
    """Uploader accounts with their upload statistics; `admin` carries the administrator's own totals."""
    _, denied = _authenticate(x_tweak_token, admin=True)
    if denied:
        return denied
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"""
            SELECT {', '.join('u.' + f for f in USER_FIELDS)},
                   COALESCE(s.uploads, 0) AS uploads, COALESCE(s.uploaded_rows, 0) AS uploaded_rows,
                   COALESCE(s.reverted_batches, 0) AS reverted_batches, s.last_upload_at
            FROM app_users u
            LEFT JOIN ({_upload_stats_sql('')}) s ON s.username = u.username
            ORDER BY u.enabled DESC, u.username
        """)
        items = [dict(r) for r in cur.fetchall()]
        cur.execute(_upload_stats_sql("WHERE username = %s"), (TWEAK_USER,))
        admin_stats = cur.fetchone()
        cur.execute("""
            SELECT MAX(at) AS last_login_at FROM auth_log
            WHERE username = %s AND event = 'login' AND success
        """, (TWEAK_USER,))
        admin_login = cur.fetchone()["last_login_at"]
    finally:
        conn.close()
    admin = {"username": TWEAK_USER, "uploads": 0, "uploaded_rows": 0, "reverted_batches": 0,
             "last_upload_at": None, **(dict(admin_stats) if admin_stats else {}), "last_login_at": admin_login}
    return {"items": items, "admin": admin}


@app.post("/api/admin/users")
def admin_create_user(request: Request, body: dict = Body(...), x_tweak_token: str = Header(None)):
    identity, denied = _authenticate(x_tweak_token, admin=True)
    if denied:
        return denied
    username = auth_utils.normalize_username(body.get("username"))
    profile, error = auth_utils.clean_profile(body.get("display_name"), body.get("department"), body.get("email"))
    error = auth_utils.username_error(username, reserved=[TWEAK_USER]) or error \
        or auth_utils.password_error(body.get("password"))
    if error:
        return JSONResponse({"error": error}, status_code=400)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO app_users (username, display_name, department, email, password_hash, created_by,
                                   password_changed_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (username) DO NOTHING
        """, (username, profile["display_name"], profile["department"], profile["email"],
              auth_utils.hash_password(body["password"]), identity["username"]))
        if cur.rowcount == 0:
            return JSONResponse({"error": f"帳號 {username} 已存在。"}, status_code=400)
        _audit(cur, request, identity["username"], "create", username, profile)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "username": username}


@app.patch("/api/admin/users/{username}")
def admin_update_user(username: str, request: Request, body: dict = Body(...), x_tweak_token: str = Header(None)):
    """Edit the profile and/or enable/disable. Disabling signs the account out everywhere."""
    identity, denied = _authenticate(x_tweak_token, admin=True)
    if denied:
        return denied
    username = auth_utils.normalize_username(username)
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT display_name, department, email, enabled FROM app_users WHERE username = %s", (username,))
        before = cur.fetchone()
        if not before:
            return JSONResponse({"error": "找不到這個帳號。"}, status_code=404)
        merged = {k: body.get(k, before[k]) for k in ("display_name", "department", "email")}
        profile, error = auth_utils.clean_profile(merged["display_name"], merged["department"], merged["email"])
        if error:
            return JSONResponse({"error": error}, status_code=400)
        enabled = before["enabled"] if body.get("enabled") is None else bool(body.get("enabled"))
        changed = {k: (before[k], profile[k]) for k in profile if before[k] != profile[k]}
        if changed:
            _audit(cur, request, identity["username"], "update", username,
                   {"before": {k: v[0] for k, v in changed.items()}, "after": {k: v[1] for k, v in changed.items()}})
        if enabled != before["enabled"]:
            _audit(cur, request, identity["username"], "enable" if enabled else "disable", username)
            if not enabled:
                _revoke_sessions(cur, username)
        cur.execute("""
            UPDATE app_users SET display_name = %s, department = %s, email = %s, enabled = %s, updated_at = NOW()
            WHERE username = %s
        """, (profile["display_name"], profile["department"], profile["email"], enabled, username))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "username": username, "enabled": enabled}


@app.post("/api/admin/users/{username}/reset-password")
def admin_reset_password(username: str, request: Request, body: dict = Body(...), x_tweak_token: str = Header(None)):
    identity, denied = _authenticate(x_tweak_token, admin=True)
    if denied:
        return denied
    username = auth_utils.normalize_username(username)
    error = auth_utils.password_error(body.get("password"))
    if error:
        return JSONResponse({"error": error}, status_code=400)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE app_users SET password_hash = %s, password_changed_at = NOW(), updated_at = NOW()
            WHERE username = %s
        """, (auth_utils.hash_password(body["password"]), username))
        if cur.rowcount == 0:
            return JSONResponse({"error": "找不到這個帳號。"}, status_code=404)
        _revoke_sessions(cur, username)
        _audit(cur, request, identity["username"], "reset_password", username)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def _paged_query(cur, base_sql, where, params, order, limit, offset):
    limit = min(max(int(limit or 50), 1), 200)
    offset = max(int(offset or 0), 0)
    cur.execute(f"SELECT COUNT(*) AS n FROM ({base_sql} {where}) t", params)
    total = cur.fetchone()["n"]
    cur.execute(f"{base_sql} {where} ORDER BY {order} LIMIT %s OFFSET %s", list(params) + [limit, offset])
    return {"items": [dict(r) for r in cur.fetchall()], "total": total, "limit": limit, "offset": offset}


@app.get("/api/admin/audit")
def admin_audit(username: str = None, limit: int = 50, offset: int = 0, x_tweak_token: str = Header(None)):
    """Account changes; `username` matches either the actor or the target."""
    _, denied = _authenticate(x_tweak_token, admin=True)
    if denied:
        return denied
    where, params = "", []
    if username:
        where, params = "WHERE actor = %s OR target = %s", [username, username]
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return _paged_query(cur, "SELECT id, at, actor, action, target, detail, client_ip FROM account_audit",
                            where, params, "at DESC, id DESC", limit, offset)
    finally:
        conn.close()


@app.get("/api/admin/login-log")
def admin_login_log(username: str = None, success: str = None, limit: int = 50, offset: int = 0,
                    x_tweak_token: str = Header(None)):
    _, denied = _authenticate(x_tweak_token, admin=True)
    if denied:
        return denied
    clauses, params = [], []
    if username:
        clauses.append("username = %s")
        params.append(username)
    if success in ("true", "false"):
        clauses.append("success = %s")
        params.append(success == "true")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return _paged_query(cur, "SELECT id, at, username, event, success, reason, client_ip, user_agent FROM auth_log",
                            where, params, "at DESC, id DESC", limit, offset)
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# DB Tweak endpoints (administrator only)
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/api/tweak/records")
def tweak_records(
    unit_date: str = None, work_order: str = None, year: int = None, month: int = None,
    week: int = None, day: str = None, page: int = 1, page_size: str = "50",
    x_tweak_token: str = Header(None),
):
    _, denied = _authenticate(x_tweak_token, admin=True)
    if denied:
        return denied
    try:
        normalized_page_size = _normalize_page_size(page_size)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    page = max(int(page or 1), 1)
    where, params = _build_where(unit_date, work_order, year, month, week, day)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(f"SELECT COUNT(*) AS total FROM module_test {where}", params)
    total = int(cur.fetchone()["total"] or 0)

    if normalized_page_size is None:
        total_pages = 1 if total else 0
        current_page = 1
        limit_clause = ""
        query_params = list(params)
    else:
        total_pages = (total + normalized_page_size - 1) // normalized_page_size if total else 0
        current_page = min(page, max(total_pages, 1))
        offset = (current_page - 1) * normalized_page_size
        limit_clause = "LIMIT %s OFFSET %s"
        query_params = list(params) + [normalized_page_size, offset]

    cur.execute(f"""
        SELECT id, mac1, mac2, work_order, COALESCE(unit_date, start_time::date) AS unit_date, start_time, end_time,
               test_duration_sec, result, source_file,
               fail_step_name, fail_message,
               (raw_log IS NOT NULL AND raw_log != '') AS has_raw_log
        FROM module_test {where}
        ORDER BY start_time DESC, id DESC {limit_clause}
    """, query_params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return {
        "items": rows,
        "total": total,
        "page": current_page,
        "page_size": "All" if normalized_page_size is None else normalized_page_size,
        "total_pages": total_pages,
        "has_prev": current_page > 1,
        "has_next": total_pages > 0 and current_page < total_pages,
    }


@app.get("/api/tweak/filter-options")
def tweak_filter_options(
    work_order: str = None,
    year: int = None,
    month: int = None,
    week: int = None,
    day: str = None,
    x_tweak_token: str = Header(None),
):
    _, denied = _authenticate(x_tweak_token, admin=True)
    if denied:
        return denied
    return _get_filter_options(work_order=work_order, year=year, month=month, week=week, day=day)


@app.delete("/api/tweak/delete-scope")
def tweak_delete_scope(
    work_order: str = None,
    year: int = None,
    month: int = None,
    week: int = None,
    day: str = None,
    x_tweak_token: str = Header(None),
):
    _, denied = _authenticate(x_tweak_token, admin=True)
    if denied:
        return denied

    if not any([work_order, year, month, week, day]):
        return JSONResponse({"error": "At least one filter is required for scoped delete"}, status_code=400)

    where, params = _build_where(work_order=work_order, year=year, month=month, week=week, day=day)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"DELETE FROM module_test {where}", params)
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return {
        "deleted": deleted,
        "filters": {
            "year": year,
            "month": month,
            "week": week,
            "day": day,
            "work_order": work_order,
        },
    }


@app.delete("/api/tweak/records/{record_id}")
def tweak_delete_record(record_id: int, x_tweak_token: str = Header(None)):
    _, denied = _authenticate(x_tweak_token, admin=True)
    if denied:
        return denied
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM module_test WHERE id = %s", (record_id,))
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return {"deleted": deleted, "id": record_id}


@app.delete("/api/tweak/date-unit/{unit_date}")
def tweak_delete_date_unit(unit_date: str, x_tweak_token: str = Header(None)):
    _, denied = _authenticate(x_tweak_token, admin=True)
    if denied:
        return denied
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM module_test WHERE COALESCE(unit_date, start_time::date) = %s", (unit_date,))
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return {"deleted": deleted, "unit_date": unit_date}


@app.delete("/api/tweak/work-order/{wo}")
def tweak_delete_work_order(wo: str, x_tweak_token: str = Header(None)):
    _, denied = _authenticate(x_tweak_token, admin=True)
    if denied:
        return denied
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM module_test WHERE work_order = %s", (wo,))
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return {"deleted": deleted, "work_order": wo}


@app.delete("/api/tweak/by-date")
def tweak_delete_by_date(
    year: int = Query(...),
    month: int = Query(None),
    x_tweak_token: str = Header(None),
):
    _, denied = _authenticate(x_tweak_token, admin=True)
    if denied:
        return denied
    clauses = ["EXTRACT(YEAR FROM COALESCE(unit_date, start_time::date)) = %s"]
    params = [year]
    label = str(year)
    if month:
        clauses.append("EXTRACT(MONTH FROM COALESCE(unit_date, start_time::date)) = %s")
        params.append(month)
        label += f"-{month:02d}"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"DELETE FROM module_test WHERE {' AND '.join(clauses)}", params)
    deleted = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return {"deleted": deleted, "year": year, "month": month, "label": label}


@app.get("/api/tweak/date-options")
def tweak_date_options(x_tweak_token: str = Header(None)):
    _, denied = _authenticate(x_tweak_token, admin=True)
    if denied:
        return denied
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
         SELECT DISTINCT EXTRACT(YEAR FROM COALESCE(unit_date, start_time::date))::int AS year,
             EXTRACT(MONTH FROM COALESCE(unit_date, start_time::date))::int AS month,
               COUNT(*) AS cnt
        FROM module_test
        GROUP BY year, month
        ORDER BY year DESC, month DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    years = sorted({r['year'] for r in rows}, reverse=True)
    return {"years": years, "year_months": rows}


@app.get("/api/tweak/raw-log/{record_id}")
def tweak_raw_log(record_id: int, x_tweak_token: str = Header(None), token: str = None):
    _, denied = _authenticate(x_tweak_token or token, admin=True)
    if denied:
        return denied
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT raw_log, source_file FROM module_test WHERE id = %s", (record_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row or not row['raw_log']:
        return JSONResponse({"error": "Raw log not available"}, status_code=404)
    filename = row['source_file'] or f"record_{record_id}.txt"
    return PlainTextResponse(
        content=row['raw_log'],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


_PARSER = None


def _load_parser():
    """Load module_log_parser once (mounted next to app.py in the container)."""
    global _PARSER
    if _PARSER is None:
        import importlib.util
        parser_path = os.path.join(os.path.dirname(__file__), "..", "module_log_parser.py")
        if not os.path.exists(parser_path):
            parser_path = os.path.join(os.path.dirname(__file__), "module_log_parser.py")
        spec = importlib.util.spec_from_file_location("module_log_parser", parser_path)
        parser = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(parser)
        _PARSER = parser
    return _PARSER


# ─── Reparse existing records (extract fail_error_code/fail_category from raw_log) ──
@app.post("/api/reparse")
def api_reparse(x_tweak_token: str = Header(None)):
    _, denied = _authenticate(x_tweak_token, admin=True)
    if denied:
        return denied
    import re as _re
    import tempfile

    parser = _load_parser()

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, raw_log, source_file FROM module_test WHERE raw_log IS NOT NULL AND raw_log != ''")
    rows = cur.fetchall()

    updated = 0
    for row in rows:
        # Write raw_log to temp file and reparse
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                          prefix=row['source_file'] or 'reparse_') as tmp:
            tmp.write(row['raw_log'])
            tmp_path = tmp.name
        try:
            rec = parser.parse_log_file(tmp_path)
            cur2 = conn.cursor()
            cur2.execute("""
                UPDATE module_test
                SET fail_step_num = %s, fail_step_name = %s, fail_message = %s,
                    fail_error_code = %s, fail_category = %s
                WHERE id = %s
            """, (rec.get('fail_step_num'), rec.get('fail_step_name'), rec.get('fail_message'),
                  rec.get('fail_error_code'), rec.get('fail_category'), row['id']))
            if cur2.rowcount:
                updated += 1
            cur2.close()
        finally:
            os.unlink(tmp_path)

    conn.commit()
    cur.close()
    conn.close()
    return {"updated": updated, "total": len(rows)}


@app.get("/api/llm-status")
def llm_status():
    return LLM_STATUS_CACHE



AI_SUMMARY_CACHE = {}
AI_SUMMARY_LOCKS = {}
_cache_lock = threading.Lock()

@app.get("/api/workorders/{wo}/ai-summary")
def ai_summary(wo: str, product_model: str = None, lang: str = "zh", mode: str = "normal"):
    cache_key = f"{wo}_{lang}_{mode}"
    
    with _cache_lock:
        if cache_key in AI_SUMMARY_CACHE:
            entry = AI_SUMMARY_CACHE[cache_key]
            if time.time() - entry['timestamp'] < 600:  # 10 minute cache TTL
                return {"summary": entry['summary']}
        
        if cache_key not in AI_SUMMARY_LOCKS:
            AI_SUMMARY_LOCKS[cache_key] = threading.Lock()
        wo_lock = AI_SUMMARY_LOCKS[cache_key]
        
    with wo_lock:
        # Double-check inside the lock
        if cache_key in AI_SUMMARY_CACHE:
            entry = AI_SUMMARY_CACHE[cache_key]
            if time.time() - entry['timestamp'] < 600:
                return {"summary": entry['summary']}

        where, params = _build_where(work_order=wo)

        conn = get_conn()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            
            # 1. Get stats
            sql_stats = f"""
            {LATEST_CTE.format(where=where)}
            SELECT
                COUNT(*)                                           AS total,
                COUNT(*) FILTER (WHERE result = 'PASS')            AS passed,
                COUNT(*) FILTER (WHERE result = 'FAIL')            AS failed,
                COUNT(*) FILTER (WHERE result = 'STOP')            AS stopped,
                ROUND(COUNT(*) FILTER (WHERE result = 'PASS') * 100.0 / NULLIF(COUNT(*), 0), 2) AS yield_pct
            FROM latest WHERE rn = 1
            """
            cur.execute(sql_stats, params)
            stats_row = cur.fetchone()
            if not stats_row or stats_row['total'] == 0:
                return JSONResponse({"error": "Work order not found or has no data"}, status_code=404)
                
            stats = dict(stats_row)
            
            # Calculate retry rate
            cur.execute(f"""
                WITH filtered AS (SELECT * FROM module_test {where}),
                grouped_pairs AS (SELECT COALESCE(work_order, '') AS work_order_key, mac1, COUNT(*) AS attempts FROM filtered GROUP BY COALESCE(work_order, ''), mac1)
                SELECT COUNT(*) FILTER (WHERE attempts > 1) AS retry_macs, COUNT(*) AS total_macs FROM grouped_pairs
            """, params)
            rr = cur.fetchone()
            stats["retry_rate"] = round((rr['retry_macs'] or 0) * 100.0 / max(rr['total_macs'] or 0, 1), 2)
            
            # 2. Get top fails
            cur.execute(f"""
            {LATEST_CTE.format(where=where)}
            SELECT
                fail_step_name AS fail_reason,
                COUNT(*) as count
            FROM latest
            WHERE rn = 1 AND result IN ('FAIL','STOP') AND fail_step_name IS NOT NULL
            GROUP BY fail_step_name
            ORDER BY count DESC
            LIMIT 3
            """, params)
            rows_fails = cur.fetchall()
            
        finally:
            if 'cur' in locals(): cur.close()
            conn.close()

        if lang == "en":
            fails_text = ", ".join([f"{r['fail_reason']}({r['count']} units)" for r in rows_fails]) if rows_fails else "No specific failures (all passed)"
        else:
            fails_text = ", ".join([f"{r['fail_reason']}({r['count']} units)" for r in rows_fails]) if rows_fails else "無特定異常(或全數Pass)"

        messages = build_summary_messages(stats, fails_text, wo, lang, mode)
        data = {
            "model": LLM_MODEL,
            "messages": messages,
            "max_tokens": LLM_REPORT_MAX_TOKENS,
            "chat_template_kwargs": {"enable_thinking": LLM_ENABLE_THINKING},
        }

        try:
            req = urllib.request.Request(
                f"{LLM_API_BASE}/chat/completions",
                data=json.dumps(data).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {LLM_API_KEY}"}
            )
            with urllib.request.urlopen(req, timeout=180) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                llm_content = res_data["choices"][0]["message"]["content"]
                
                # Save to cache
                with _cache_lock:
                    AI_SUMMARY_CACHE[cache_key] = {
                        'summary': llm_content,
                        'timestamp': time.time()
                    }
                
                return {"summary": llm_content}
        except Exception as e:
            print(f"LLM summary failed: {e}")
            return JSONResponse({"error": f"LLM request failed: {e}"}, status_code=500)
# ═══════════════════════════════════════════════════════════════════════════════
# Log Uploader — work-order zip → split per station → parse → module_test
# Flow: analyze (facts, no DB writes) → validate (rules) → commit (NDJSON stream)
# ═══════════════════════════════════════════════════════════════════════════════

def _valid_upload_id(upload_id):
    return isinstance(upload_id, str) and len(upload_id) == 32 and all(c in "0123456789abcdef" for c in upload_id)


def _upload_paths(upload_id):
    base = os.path.join(UPLOAD_TMP_DIR, upload_id)
    return base + ".zip", base + ".hashes.json"


def _remove_upload_files(upload_id):
    for path in _upload_paths(upload_id):
        try:
            os.remove(path)
        except OSError:
            pass


def _cleanup_stale_uploads():
    if not os.path.isdir(UPLOAD_TMP_DIR):
        return
    cutoff = time.time() - UPLOAD_TTL_SEC
    for name in os.listdir(UPLOAD_TMP_DIR):
        path = os.path.join(UPLOAD_TMP_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def _existing_by_wo(cur, hashes):
    """{work_order: count} of hashes already in module_test ('' = no work order)."""
    if not hashes:
        return {}
    cur.execute("""
        SELECT COALESCE(work_order, '') AS wo, COUNT(*) AS n
        FROM module_test WHERE file_hash = ANY(%s) GROUP BY 1
    """, (hashes,))
    return {row[0]: int(row[1]) for row in cur.fetchall()}


def _current_target(cur, work_order):
    if not log_splitter.is_valid_work_order(work_order):
        return None
    cur.execute("SELECT target_total FROM alignment_targets WHERE work_order = %s", (work_order,))
    row = cur.fetchone()
    return row[0] if row else None


def _fetch_upload(conn, upload_id):
    if not _valid_upload_id(upload_id):
        return None
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM upload_log WHERE id = %s", (upload_id,))
    row = cur.fetchone()
    cur.close()
    return row


def _owned_upload(conn, upload_id, identity, not_found="找不到這個上傳批次"):
    """(row, None) or (None, 404/403 response). Uploaders reach only the batches they analyzed."""
    row = _fetch_upload(conn, upload_id)
    if not row:
        return None, JSONResponse({"error": not_found}, status_code=404)
    if not auth_utils.can_access_upload(identity, row["username"]):
        return None, JSONResponse({"error": "這不是你的上傳批次"}, status_code=403)
    return row, None


def _validation_payload(issues):
    return {
        "issues": issues,
        "can_upload": not log_splitter.has_errors(issues),
        "needs_ack": log_splitter.has_warnings(issues),
    }


@app.post("/api/log-upload/analyze")
def log_upload_analyze(request: Request, file: UploadFile = File(...), x_tweak_token: str = Header(None)):
    """Store the zip, split every station's Log_All.txt in memory and report facts. No module_test writes."""
    identity, denied = _authenticate(x_tweak_token)
    if denied:
        return denied
    zip_name = os.path.basename(file.filename or "upload.zip")
    if not zip_name.lower().endswith(".zip"):
        return JSONResponse({"error": "請選擇 .zip 壓縮檔"}, status_code=400)

    _cleanup_stale_uploads()
    os.makedirs(UPLOAD_TMP_DIR, exist_ok=True)
    upload_id = uuid.uuid4().hex
    zip_path, hashes_path = _upload_paths(upload_id)
    with open(zip_path, "wb") as out:
        shutil.copyfileobj(file.file, out, 1024 * 1024)
    zip_size = os.path.getsize(zip_path)

    try:
        with zipfile.ZipFile(zip_path) as zf:
            entries = log_splitter.find_log_all_entries(zf)
            if not entries:
                raise ValueError("壓縮檔內找不到站別資料夾（例如 10/20/30/40）下的 Log_All.txt")
            if sum(info.file_size for _, info in entries) > UPLOAD_MAX_LOG_ALL_BYTES:
                raise ValueError("Log_All.txt 解壓後總大小超過 500 MB 上限")
            facts, hashes = log_splitter.analyze_zip(zf, zip_name)
    except zipfile.BadZipFile:
        _remove_upload_files(upload_id)
        return JSONResponse({"error": "檔案不是有效的 zip 壓縮檔"}, status_code=400)
    except ValueError as exc:
        _remove_upload_files(upload_id)
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:  # corrupt member, unsupported compression, ...
        _remove_upload_files(upload_id)
        return JSONResponse({"error": f"無法讀取壓縮檔內容：{exc}"}, status_code=400)

    with open(hashes_path, "w") as f:
        json.dump(hashes, f)

    conn = get_conn()
    try:
        cur = conn.cursor()
        facts["existing_by_wo"] = _existing_by_wo(cur, hashes)
        cur.execute("""
            INSERT INTO upload_log (id, client_ip, username, user_role, zip_name, zip_size, inferred_wo,
                                    unique_mac1, total_records, facts, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'analyzed')
        """, (upload_id, _client_ip(request), identity["username"], identity["role"], zip_name, zip_size,
              facts["inferred_wo"], facts["unique_mac1"], facts["total_records"],
              psycopg2.extras.Json(facts)))
        conn.commit()
    finally:
        conn.close()
    return {"upload_id": upload_id, "facts": facts, "chat_opening": upload_chat.opening_message(facts)}


@app.post("/api/log-upload/validate")
def log_upload_validate(body: dict = Body(...), x_tweak_token: str = Header(None)):
    """Run the upload rules (R1–R8) for the entered work order / planned quantity."""
    identity, denied = _authenticate(x_tweak_token)
    if denied:
        return denied
    work_order = (body.get("work_order") or "").strip()
    conn = get_conn()
    try:
        row, denied = _owned_upload(conn, body.get("upload_id"), identity, "找不到這個上傳批次，請重新選擇 zip")
        if denied:
            return denied
        cur = conn.cursor()
        facts = row["facts"]
        issues = log_splitter.validate_upload(facts, work_order, body.get("planned_qty"),
                                              facts.get("existing_by_wo"), _current_target(cur, work_order))
    finally:
        conn.close()
    return _validation_payload(issues)


def _llm_request(payload, stream=False):
    data = {"model": LLM_MODEL, "chat_template_kwargs": {"enable_thinking": False}, "stream": stream, **payload}
    req = urllib.request.Request(
        f"{LLM_API_BASE}/chat/completions",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LLM_API_KEY}"},
    )
    return urllib.request.urlopen(req, timeout=LLM_CHAT_TIMEOUT)


def _llm_stream_text(payload):
    """Yield content deltas from an OpenAI-compatible SSE stream."""
    with _llm_request(payload, stream=True) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                break
            choices = json.loads(chunk).get("choices") or []
            delta = (choices[0].get("delta") or {}).get("content") if choices else None
            if delta:
                yield delta


def _chat_extract(messages):
    """(work_order, planned_qty, status) from the latest user message; status: ok | bad_output | unreachable."""
    try:
        with _llm_request({
            "messages": upload_chat.build_extract_messages(messages),
            "temperature": 0,
            "max_tokens": upload_chat.EXTRACT_MAX_TOKENS,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "upload_fields", "schema": upload_chat.EXTRACT_SCHEMA}},
        }) as resp:
            content = json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"]
    except Exception as exc:
        print(f"Upload chat extract: LLM unreachable: {exc}")
        return None, None, "unreachable"
    try:
        wo, qty = upload_chat.parse_extract(content)
    except (ValueError, TypeError) as exc:
        print(f"Upload chat extract: unusable output {content!r}: {exc}")
        return None, None, "bad_output"
    return wo, qty, "ok"


@app.post("/api/log-upload/chat")
def log_upload_chat(body: dict = Body(...), x_tweak_token: str = Header(None)):
    """One AI-assistant turn → NDJSON: extract, validation, delta…, done | fallback.

    The rules (R1–R8) decide; the LLM only phrases their result. `known` carries the values
    already confirmed in the form or earlier turns, so switching modes keeps them."""
    identity, denied = _authenticate(x_tweak_token)
    if denied:
        return denied
    try:
        messages = upload_chat.normalize_messages(body.get("messages"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    upload_id = body.get("upload_id")
    conn = get_conn()
    try:
        row, denied = _owned_upload(conn, upload_id, identity, "找不到這個上傳批次，請重新選擇 zip")
        if denied:
            return denied
        if row["status"] not in ("analyzed", "failed"):
            return JSONResponse({"error": f"這個批次已經處理過（狀態：{row['status']}）"}, status_code=409)
        facts = row["facts"]
        llm_wo, llm_qty, status = _chat_extract(messages)
        fields = upload_chat.merge_fields(body.get("known"), messages[-1]["content"], llm_wo, llm_qty,
                                          llm_ok=(status == "ok"))
        cur = conn.cursor()
        issues = log_splitter.validate_upload(facts, fields["work_order"] or "", fields["planned_qty"],
                                              facts.get("existing_by_wo"), _current_target(cur, fields["work_order"]))
    finally:
        conn.close()

    def events():
        yield _ndjson({"type": "extract", **fields, "extract_ok": status == "ok"})
        yield _ndjson({"type": "validation", **_validation_payload(issues)})
        if status == "unreachable":
            yield _ndjson({"type": "fallback", "reason": "AI 助理目前無法連線"})
            return
        parts = []
        try:
            for text in _llm_stream_text({
                "messages": upload_chat.build_reply_messages(facts, fields, issues, messages),
                "temperature": 0.3,
                "max_tokens": upload_chat.REPLY_MAX_TOKENS,
            }):
                parts.append(text)
                yield _ndjson({"type": "delta", "text": text})
        except Exception as exc:
            print(f"Upload chat reply failed: {exc}")
            yield _ndjson({"type": "fallback", "reason": "AI 助理回覆逾時或中斷"})
            return
        reply = "".join(parts).strip()
        if not reply:
            reply = upload_chat.fallback_reply(fields)
            yield _ndjson({"type": "delta", "text": reply})
        _save_chat_transcript(upload_id, messages + [{"role": "assistant", "content": reply}])
        yield _ndjson({"type": "done", "reply": reply})

    return StreamingResponse(events(), media_type="application/x-ndjson")


def _ndjson(event):
    return json.dumps(event, ensure_ascii=False, default=str) + "\n"


def _save_chat_transcript(upload_id, transcript):
    """Keep the server's copy of the conversation even if the batch is never committed."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE upload_log SET chat_transcript = %s
            WHERE id = %s AND status IN ('analyzed', 'failed')
        """, (psycopg2.extras.Json(transcript), upload_id))
        conn.commit()
    except Exception as exc:
        print(f"Upload chat transcript not saved: {exc}")
    finally:
        conn.close()


@app.post("/api/log-upload/commit")
def log_upload_commit(body: dict = Body(...), x_tweak_token: str = Header(None)):
    """Re-validate against the current DB, then ingest in a background thread and stream progress as NDJSON.
    The ingest keeps running if the browser disconnects; the outcome is recorded in upload_log."""
    identity, denied = _authenticate(x_tweak_token)
    if denied:
        return denied
    upload_id = body.get("upload_id")
    work_order = (body.get("work_order") or "").strip()
    planned_qty = body.get("planned_qty")
    mode = "chat" if body.get("mode") == "chat" else "form"

    conn = get_conn()
    handed_off = False
    try:
        row, denied = _owned_upload(conn, upload_id, identity, "找不到這個上傳批次，請重新選擇 zip")
        if denied:
            return denied
        if row["status"] not in ("analyzed", "failed"):
            return JSONResponse({"error": f"這個批次已經處理過（狀態：{row['status']}）"}, status_code=409)
        zip_path, hashes_path = _upload_paths(upload_id)
        if not (os.path.exists(zip_path) and os.path.exists(hashes_path)):
            return JSONResponse({"error": "暫存檔已過期，請重新選擇 zip"}, status_code=410)

        cur = conn.cursor()
        # Session-level lock; released when this connection closes (early return or ingest end).
        cur.execute("SELECT pg_try_advisory_lock(%s)", (UPLOAD_LOCK_KEY,))
        if not cur.fetchone()[0]:
            return JSONResponse({"error": "已有其他上傳正在進行，請稍後再試"}, status_code=409)

        with open(hashes_path) as f:
            hashes = json.load(f)
        facts = row["facts"]
        facts["existing_by_wo"] = _existing_by_wo(cur, hashes)
        prev_target = _current_target(cur, work_order)
        issues = log_splitter.validate_upload(facts, work_order, planned_qty, facts["existing_by_wo"], prev_target)
        payload = _validation_payload(issues)
        if not payload["can_upload"]:
            return JSONResponse({**payload, "error": "輸入資料有誤，請修正後再上傳"}, status_code=400)
        if payload["needs_ack"] and not body.get("ack_warnings"):
            return JSONResponse({**payload, "error": "有警告尚未確認"}, status_code=400)

        qty = log_splitter.parse_quantity(planned_qty)
        # chat_transcript is already stored turn by turn by /chat (server copy, not the client's).
        cur.execute("""
            UPDATE upload_log
            SET work_order = %s, planned_qty = %s, prev_target = %s, facts = %s, validation = %s,
                mode = %s, status = 'running', error = NULL
            WHERE id = %s
        """, (work_order, qty, prev_target, psycopg2.extras.Json(facts), psycopg2.extras.Json(issues),
              mode, upload_id))
        conn.commit()

        events = queue.Queue()
        threading.Thread(
            target=_run_ingest,
            args=(conn, events, upload_id, zip_path, work_order, qty, facts["total_records"]),
            daemon=True,
        ).start()
        handed_off = True
        return StreamingResponse(_ndjson_events(events), media_type="application/x-ndjson")
    finally:
        if not handed_off:
            conn.close()


def _ndjson_events(events):
    while True:
        try:
            event = events.get(timeout=15)
        except queue.Empty:
            yield _ndjson({"type": "heartbeat"})
            continue
        yield _ndjson(event)
        if event["type"] in ("done", "error"):
            break


def _run_ingest(conn, events, upload_id, zip_path, work_order, planned_qty, total):
    """Insert every split record with upload_id; commit every 50. Owns `conn` (and its advisory lock)."""
    st = {"total": total, "done": 0, "uploaded": 0, "skipped": 0, "failed": 0, "invalid": 0}
    failures = []
    t0 = time.time()
    try:
        parser = _load_parser()
        cur = conn.cursor()
        with zipfile.ZipFile(zip_path) as zf:
            entries = log_splitter.find_log_all_entries(zf)
            events.put({"type": "start", "upload_id": upload_id, "work_order": work_order,
                        "total": total, "stations": [station for station, _ in entries]})
            for station, records, invalid in log_splitter.iter_station_records(zf, entries, work_order):
                st["invalid"] += len(invalid)
                events.put({"type": "split", "station": station, "records": len(records), "invalid": len(invalid)})
                for item in records:
                    cur.execute("SAVEPOINT rec")
                    try:
                        rec = parser.parse_log_content(item["content"], item["filename"])
                        if parser.insert_record(conn, rec, extra={"upload_id": upload_id}):
                            st["uploaded"] += 1
                        else:
                            st["skipped"] += 1
                        cur.execute("RELEASE SAVEPOINT rec")
                    except Exception as exc:
                        cur.execute("ROLLBACK TO SAVEPOINT rec")
                        st["failed"] += 1
                        if len(failures) < 50:
                            failures.append({"station": station, "filename": item["filename"], "error": str(exc)[:300]})
                    st["done"] += 1
                    if st["done"] % 50 == 0:
                        conn.commit()
                    if st["done"] % 25 == 0:
                        events.put({"type": "progress", **st})
        if planned_qty:
            cur.execute("""
                INSERT INTO alignment_targets (work_order, target_total, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (work_order) DO UPDATE
                SET target_total = EXCLUDED.target_total, updated_at = NOW()
            """, (work_order, planned_qty))
        elapsed = round(time.time() - t0, 1)
        cur.execute("""
            UPDATE upload_log
            SET status = 'done', finished_at = NOW(), uploaded = %s, skipped = %s, failed = %s,
                failures = %s, elapsed_sec = %s
            WHERE id = %s
        """, (st["uploaded"], st["skipped"], st["failed"], psycopg2.extras.Json(failures), elapsed, upload_id))
        conn.commit()
        _remove_upload_files(upload_id)
        with _cache_lock:
            for key in [k for k in AI_SUMMARY_CACHE if k.startswith(f"{work_order}_")]:
                AI_SUMMARY_CACHE.pop(key, None)
        events.put({"type": "done", **st, "elapsed_sec": elapsed, "failures": failures})
    except Exception as exc:
        print(f"Log upload {upload_id} failed: {exc}")
        try:
            conn.rollback()
            cur = conn.cursor()
            cur.execute("""
                UPDATE upload_log
                SET status = 'failed', finished_at = NOW(), uploaded = %s, skipped = %s, failed = %s,
                    failures = %s, elapsed_sec = %s, error = %s
                WHERE id = %s
            """, (st["uploaded"], st["skipped"], st["failed"], psycopg2.extras.Json(failures),
                  round(time.time() - t0, 1), str(exc)[:500], upload_id))
            conn.commit()
        except Exception:
            pass
        events.put({"type": "error", "message": f"上傳中斷：{exc}", **st})
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.get("/api/log-upload/history")
def log_upload_history(limit: int = 20, username: str = None, x_tweak_token: str = Header(None)):
    """Recent batches. Uploaders always get only their own; the administrator may filter by uploader."""
    identity, denied = _authenticate(x_tweak_token)
    if denied:
        return denied
    limit = min(max(int(limit or 20), 1), 200)
    if identity["role"] != "admin":
        username = identity["username"]
    where, params = "WHERE l.status <> 'analyzed'", []
    if username:
        where += " AND l.username = %s"
        params.append(username)
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"""
            SELECT l.id, l.created_at, l.finished_at, l.username, l.user_role,
                   COALESCE(u.display_name, l.username) AS display_name, l.client_ip, l.zip_name,
                   l.work_order, l.planned_qty, l.unique_mac1, l.total_records, l.mode, l.status,
                   l.uploaded, l.skipped, l.failed, l.reverted, l.reverted_by, l.reverted_at,
                   l.elapsed_sec, l.error
            FROM upload_log l LEFT JOIN app_users u ON u.username = l.username
            {where}
            ORDER BY l.created_at DESC
            LIMIT %s
        """, params + [limit])
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
    return {"items": rows}


@app.get("/api/log-upload/{upload_id}")
def log_upload_detail(upload_id: str, x_tweak_token: str = Header(None)):
    identity, denied = _authenticate(x_tweak_token)
    if denied:
        return denied
    conn = get_conn()
    try:
        row, denied = _owned_upload(conn, upload_id, identity)
    finally:
        conn.close()
    return denied or dict(row)


@app.delete("/api/log-upload/{upload_id}")
def log_upload_revert(upload_id: str, x_tweak_token: str = Header(None)):
    """Undo one web upload: delete only the rows it inserted and restore the alignment target it set."""
    identity, denied = _authenticate(x_tweak_token)
    if denied:
        return denied
    conn = get_conn()
    try:
        row, denied = _owned_upload(conn, upload_id, identity)
        if denied:
            return denied
        if row["status"] not in ("done", "failed"):
            return JSONResponse({"error": f"只有已完成或失敗的批次可以撤銷（目前狀態：{row['status']}）"}, status_code=400)
        cur = conn.cursor()
        cur.execute("DELETE FROM module_test WHERE upload_id = %s", (upload_id,))
        reverted = cur.rowcount
        wo, qty = row["work_order"], row["planned_qty"]
        if wo and qty and _current_target(cur, wo) == qty:
            if row["prev_target"] is None:
                cur.execute("DELETE FROM alignment_targets WHERE work_order = %s", (wo,))
            else:
                cur.execute("UPDATE alignment_targets SET target_total = %s, updated_at = NOW() WHERE work_order = %s",
                            (row["prev_target"], wo))
        cur.execute("""
            UPDATE upload_log SET status = 'reverted', reverted = %s, reverted_by = %s, reverted_at = NOW()
            WHERE id = %s
        """, (reverted, identity["username"], upload_id))
        conn.commit()
    finally:
        conn.close()
    return {"reverted": reverted, "upload_id": upload_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
