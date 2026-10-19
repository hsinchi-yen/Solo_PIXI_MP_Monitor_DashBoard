"""Schema setup at API startup.

Rule: existing tables keep their columns. module_test is only ever *read* here, except
for the columns earlier releases added (checked first, so startup never takes an
ALTER lock on a populated table), one expression index, and the one-time unit_date
back-fill. Everything new lives in separate objects: upload_log, app_users,
app_sessions, auth_log, account_audit, app_meta, ai_reports, job_runs, mv_unit_day.
"""
import logging

import config
import db

log = logging.getLogger("pixi.migrations")

# Bump when the view definition changes; startup then rebuilds it.
MV_VERSION = "3"

MV_UNIT_DAY_SQL = """
CREATE MATERIALIZED VIEW mv_unit_day AS
SELECT
    COALESCE(unit_date, start_time::date)                             AS eff_date,
    COALESCE(work_order, '')                                          AS wo_key,
    mac1,
    COUNT(*)::int                                                     AS n,
    (COUNT(*) FILTER (WHERE result = 'PASS'))::int                    AS n_pass,
    (COUNT(*) FILTER (WHERE result = 'FAIL'))::int                    AS n_fail,
    (COUNT(*) FILTER (WHERE result = 'STOP'))::int                    AS n_stop,
    MIN(start_time)                                                   AS first_time,
    MAX(start_time)                                                   AS last_time,
    MAX(start_time) FILTER (WHERE result = 'PASS')                    AS last_pass_time,
    (ARRAY_AGG(result ORDER BY start_time DESC, id DESC))[1]          AS last_result,
    (ARRAY_AGG(mac2 ORDER BY start_time DESC, id DESC))[1]            AS last_mac2,
    SUM(test_duration_sec)                                            AS dur_sum,
    COUNT(test_duration_sec)::int                                     AS dur_n
FROM module_test
GROUP BY 1, 2, 3
"""

# One row per unit (work order, MAC1) over all dates, built from mv_unit_day. Statistics
# without a date filter (all data, or one work order — the default view) read it directly.
MV_UNIT_SQL = """
CREATE MATERIALIZED VIEW mv_unit AS
SELECT u.*,
       CASE WHEN has_pass THEN 'PASS' ELSE latest_result END      AS result,
       CASE WHEN has_pass THEN pass_date ELSE latest_date END     AS chosen_date,
       CASE WHEN has_pass THEN last_pass_time ELSE last_time END  AS chosen_time
FROM (
    SELECT wo_key, mac1,
           SUM(n)::int                                                      AS attempts,
           SUM(n_pass)::int                                                 AS pass_count,
           SUM(n_fail)::int                                                 AS fail_count,
           SUM(n_stop)::int                                                 AS stop_count,
           BOOL_OR(n_pass > 0)                                              AS has_pass,
           MIN(first_time)                                                  AS first_time,
           MAX(last_time)                                                   AS last_time,
           MAX(last_pass_time)                                              AS last_pass_time,
           (ARRAY_AGG(last_result ORDER BY last_time DESC))[1]              AS latest_result,
           (ARRAY_AGG(eff_date ORDER BY last_time DESC))[1]                 AS latest_date,
           (ARRAY_AGG(eff_date ORDER BY last_pass_time DESC NULLS LAST))[1] AS pass_date,
           (ARRAY_AGG(last_mac2 ORDER BY last_time DESC))[1]                AS last_mac2,
           SUM(dur_sum)                                                     AS dur_sum,
           SUM(dur_n)::int                                                  AS dur_n
    FROM mv_unit_day GROUP BY wo_key, mac1
) u
"""


def _column_exists(cur, table, column):
    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s
    """, (table, column))
    return cur.fetchone() is not None


def _meta_get(cur, key):
    cur.execute("SELECT value FROM app_meta WHERE key = %s", (key,))
    row = cur.fetchone()
    return row["value"] if row else None


def run():
    conn = db.connect()
    try:
        cur = conn.cursor()
        # Both uvicorn workers start together; serialise so CREATE … IF NOT EXISTS cannot race.
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (config.MIGRATION_LOCK_KEY,))

        # Columns added by earlier releases (nullable; the desktop uploader ignores them).
        for col, dtype in [("fail_error_code", "TEXT"), ("fail_category", "TEXT"),
                           ("unit_date", "DATE"), ("upload_id", "TEXT")]:
            if not _column_exists(cur, "module_test", col):
                cur.execute(f"ALTER TABLE module_test ADD COLUMN {col} {dtype}")
                log.info("added module_test.%s", col)
        cur.execute("""
            UPDATE module_test SET unit_date = start_time::date
            WHERE unit_date IS NULL AND start_time IS NOT NULL
        """)

        for ddl in (
            "CREATE INDEX IF NOT EXISTS idx_mt_unit_date ON module_test(unit_date)",
            "CREATE INDEX IF NOT EXISTS idx_mt_unit_date_time ON module_test(unit_date, start_time)",
            "CREATE INDEX IF NOT EXISTS idx_mt_workorder_mac1_time ON module_test(work_order, mac1, start_time)",
            "CREATE INDEX IF NOT EXISTS idx_module_test_upload_id ON module_test(upload_id)",
            # Date-range filters use this exact expression (services/filters.py).
            "CREATE INDEX IF NOT EXISTS idx_mt_eff_date ON module_test ((COALESCE(unit_date, start_time::date)))",
        ):
            cur.execute(ddl)

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
                reverted_by     TEXT,
                reverted_at     TIMESTAMPTZ,
                elapsed_sec     REAL,
                error           TEXT
            )
        """)
        # Phase-1 test databases named the uploader column tweak_user.
        if _column_exists(cur, "upload_log", "tweak_user") and not _column_exists(cur, "upload_log", "username"):
            cur.execute("ALTER TABLE upload_log RENAME COLUMN tweak_user TO username")
        for col, dtype in [("user_role", "TEXT"), ("reverted_by", "TEXT"), ("reverted_at", "TIMESTAMPTZ")]:
            if not _column_exists(cur, "upload_log", col):
                cur.execute(f"ALTER TABLE upload_log ADD COLUMN {col} {dtype}")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_upload_log_created ON upload_log(created_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_upload_log_username ON upload_log(username, created_at DESC)")

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

        # Small key/value store: view refresh time, daily-batch marker, schema versions.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_meta (
                key         TEXT PRIMARY KEY,
                value       TEXT,
                updated_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # AI quality reports: one row per generation, both languages.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_reports (
                id            BIGSERIAL PRIMARY KEY,
                work_order    TEXT NOT NULL,
                status        TEXT NOT NULL,          -- queued | running | done | failed
                trigger       TEXT,                   -- manual | upload | daily
                requested_by  TEXT,
                data_hash     TEXT,
                facts         JSONB,
                report_zh     JSONB,
                report_en     JSONB,
                checks        JSONB,                  -- number check result per language
                model         TEXT,
                error         TEXT,
                created_at    TIMESTAMPTZ DEFAULT NOW(),
                started_at    TIMESTAMPTZ,
                finished_at   TIMESTAMPTZ,
                elapsed_sec   REAL
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ai_reports_wo ON ai_reports(work_order, created_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ai_reports_status ON ai_reports(status, created_at)")
        # Background jobs (reparse): progress is shared by both workers through this table.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS job_runs (
                id            BIGSERIAL PRIMARY KEY,
                kind          TEXT NOT NULL,
                status        TEXT NOT NULL,          -- running | done | failed | cancelled
                total         INT,
                done          INT DEFAULT 0,
                updated       INT DEFAULT 0,
                failed        INT DEFAULT 0,
                cancel        BOOLEAN DEFAULT FALSE,
                requested_by  TEXT,
                error         TEXT,
                started_at    TIMESTAMPTZ DEFAULT NOW(),
                heartbeat_at  TIMESTAMPTZ DEFAULT NOW(),
                finished_at   TIMESTAMPTZ
            )
        """)

        # Statistics view: one row per (effective date, work order, MAC1).
        cur.execute("SELECT to_regclass('mv_unit_day') AS a, to_regclass('mv_unit') AS b")
        r = cur.fetchone()
        exists = r["a"] is not None and r["b"] is not None
        if exists and _meta_get(cur, "mv_unit_day_version") != MV_VERSION:
            exists = False
        if not exists:
            cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_unit")
            cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_unit_day")
            cur.execute(MV_UNIT_DAY_SQL)
            cur.execute("CREATE UNIQUE INDEX ux_mv_unit_day ON mv_unit_day (eff_date, wo_key, mac1)")
            cur.execute("CREATE INDEX ix_mv_unit_day_wo ON mv_unit_day (wo_key, eff_date)")
            cur.execute(MV_UNIT_SQL)
            cur.execute("CREATE UNIQUE INDEX ux_mv_unit ON mv_unit (wo_key, mac1)")
            cur.execute("CREATE INDEX ix_mv_unit_chosen ON mv_unit (chosen_date)")
            cur.execute("""
                INSERT INTO app_meta (key, value, updated_at) VALUES ('mv_unit_day_version', %s, NOW())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """, (MV_VERSION,))
            cur.execute("""
                INSERT INTO app_meta (key, value, updated_at) VALUES ('stats_refreshed_at', NOW()::text, NOW())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """)
            log.info("built mv_unit_day + mv_unit (version %s)", MV_VERSION)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
