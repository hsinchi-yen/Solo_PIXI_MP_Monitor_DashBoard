"""Background reparse: refresh fail_step_* / fail_error_code / fail_category from raw_log.

Runs in batches of BATCH rows (ordered by id) so memory stays flat and the HTTP
request returns immediately; progress and cancel live in job_runs, which both uvicorn
workers can read. Only the five fail_* columns are updated, as before.
"""
import logging
import threading

import config
import db
from errors import conflict

log = logging.getLogger("pixi.reparse")

BATCH = 500
FIELDS = ("fail_step_num", "fail_step_name", "fail_message", "fail_error_code", "fail_category")

_parser = None


def load_parser():
    global _parser
    if _parser is None:
        import module_log_parser
        _parser = module_log_parser
    return _parser


def latest_job(conn):
    return db.fetch_one(conn, "SELECT * FROM job_runs WHERE kind = 'reparse' ORDER BY id DESC LIMIT 1")


def start(username):
    """Create a job row and run it in a daemon thread. 409 if one is already running."""
    with db.db() as conn:
        db.execute(conn, """
            UPDATE job_runs SET status = 'failed', error = 'worker stopped', finished_at = NOW()
            WHERE kind = 'reparse' AND status = 'running' AND heartbeat_at < NOW() - INTERVAL '5 minutes'
        """)
        running = db.fetch_one(conn, "SELECT id FROM job_runs WHERE kind = 'reparse' AND status = 'running'")
        if running:
            raise conflict("已有重新解析正在進行", "reparse_running", job_id=running["id"])
        total = db.fetch_one(conn, "SELECT COUNT(*) AS n FROM module_test WHERE raw_log IS NOT NULL AND raw_log <> ''")["n"]
        job = db.fetch_one(conn, """
            INSERT INTO job_runs (kind, status, total, requested_by) VALUES ('reparse', 'running', %s, %s)
            RETURNING *
        """, (total, username))
    threading.Thread(target=_run, args=(job["id"],), daemon=True, name=f"reparse-{job['id']}").start()
    return job


def cancel(job_id):
    with db.db() as conn:
        return db.execute(conn, "UPDATE job_runs SET cancel = TRUE WHERE id = %s AND status = 'running'", (job_id,))


def _run(job_id):
    parser = load_parser()
    conn = db.connect()
    done = updated = failed = 0
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (config.REPARSE_LOCK_KEY,))
        if not cur.fetchone()["ok"]:
            cur.execute("UPDATE job_runs SET status = 'failed', error = 'locked', finished_at = NOW() WHERE id = %s", (job_id,))
            conn.commit()
            return
        conn.commit()
        last_id = 0
        while True:
            cur.execute("SELECT cancel FROM job_runs WHERE id = %s", (job_id,))
            if cur.fetchone()["cancel"]:
                cur.execute("""UPDATE job_runs SET status = 'cancelled', done = %s, updated = %s, failed = %s,
                               finished_at = NOW() WHERE id = %s""", (done, updated, failed, job_id))
                conn.commit()
                return
            cur.execute("""
                SELECT id, raw_log, source_file FROM module_test
                WHERE id > %s AND raw_log IS NOT NULL AND raw_log <> ''
                ORDER BY id LIMIT %s
            """, (last_id, BATCH))
            rows = cur.fetchall()
            if not rows:
                break
            for row in rows:
                last_id = row["id"]
                try:
                    rec = parser.parse_log_content(row["raw_log"], row["source_file"] or f"record_{row['id']}.txt")
                    cur.execute(f"""
                        UPDATE module_test SET {', '.join(f + ' = %s' for f in FIELDS)} WHERE id = %s
                    """, [rec.get(f) for f in FIELDS] + [row["id"]])
                    updated += cur.rowcount
                except Exception as exc:  # one bad log must not stop the job
                    failed += 1
                    log.warning("reparse id %s failed: %s", row["id"], exc)
                done += 1
            cur.execute("""UPDATE job_runs SET done = %s, updated = %s, failed = %s, heartbeat_at = NOW()
                           WHERE id = %s""", (done, updated, failed, job_id))
            conn.commit()
        cur.execute("""UPDATE job_runs SET status = 'done', done = %s, updated = %s, failed = %s, finished_at = NOW()
                       WHERE id = %s""", (done, updated, failed, job_id))
        conn.commit()
        log.info("reparse job %s done: %s rows, %s updated, %s failed", job_id, done, updated, failed)
    except Exception as exc:
        log.exception("reparse job %s failed", job_id)
        try:
            conn.rollback()
            conn.cursor().execute("UPDATE job_runs SET status = 'failed', error = %s, finished_at = NOW() WHERE id = %s",
                                  (str(exc)[:300], job_id))
            conn.commit()
        except Exception:
            pass
    finally:
        try:
            conn.cursor().execute("SELECT pg_advisory_unlock(%s)", (config.REPARSE_LOCK_KEY,))
            conn.commit()
        except Exception:
            pass
        conn.close()
