"""Web Log Uploader back end: temp files, rule helpers and the ingest thread.

Flow: analyze (facts, no module_test writes) → validate / chat (rules R1–R8) →
commit (NDJSON progress stream from a background thread).
"""
import json
import logging
import os
import queue
import time
import zipfile

import psycopg2.extras

import config
import db
import log_splitter
from errors import ApiError, Msg
from services import auth_utils, reparse, reports, stats

log = logging.getLogger("pixi.upload")


def valid_upload_id(upload_id):
    return isinstance(upload_id, str) and len(upload_id) == 32 and all(c in "0123456789abcdef" for c in upload_id)


def upload_paths(upload_id):
    base = os.path.join(config.UPLOAD_TMP_DIR, upload_id)
    return base + ".zip", base + ".hashes.json"


def remove_upload_files(upload_id):
    for path in upload_paths(upload_id):
        try:
            os.remove(path)
        except OSError:
            pass


def cleanup_stale_uploads():
    if not os.path.isdir(config.UPLOAD_TMP_DIR):
        return
    cutoff = time.time() - config.UPLOAD_TTL_SEC
    for name in os.listdir(config.UPLOAD_TMP_DIR):
        path = os.path.join(config.UPLOAD_TMP_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


def existing_by_wo(conn, hashes):
    """{work_order: count} of hashes already in module_test ('' = no work order)."""
    if not hashes:
        return {}
    rows = db.fetch_all(conn, """
        SELECT COALESCE(work_order, '') AS wo, COUNT(*) AS n
        FROM module_test WHERE file_hash = ANY(%s) GROUP BY 1
    """, (hashes,))
    return {r["wo"]: int(r["n"]) for r in rows}


def current_target(conn, work_order):
    if not log_splitter.is_valid_work_order(work_order or ""):
        return None
    row = db.fetch_one(conn, "SELECT target_total FROM alignment_targets WHERE work_order = %s", (work_order,))
    return row["target_total"] if row else None


def fetch_upload(conn, upload_id):
    if not valid_upload_id(upload_id):
        return None
    return db.fetch_one(conn, "SELECT * FROM upload_log WHERE id = %s", (upload_id,))


def owned_upload(conn, upload_id, identity, not_found=Msg("找不到這個上傳批次", "Upload batch not found.")):
    """The upload_log row; 404 if missing, 403 if an uploader asks for someone else's batch."""
    row = fetch_upload(conn, upload_id)
    if not row:
        raise ApiError(404, not_found, "no_upload")
    if not auth_utils.can_access_upload(identity, row["username"]):
        raise ApiError(403, Msg("這不是你的上傳批次", "This upload batch belongs to another user."), "not_owner")
    return row


def validation_payload(issues):
    return {
        "issues": issues,
        "can_upload": not log_splitter.has_errors(issues),
        "needs_ack": log_splitter.has_warnings(issues),
    }


def ndjson(event):
    return json.dumps(event, ensure_ascii=False, default=str) + "\n"


def ndjson_events(events):
    while True:
        try:
            event = events.get(timeout=15)
        except queue.Empty:
            yield ndjson({"type": "heartbeat"})
            continue
        yield ndjson(event)
        if event["type"] in ("done", "error"):
            break


def run_ingest(conn, events, upload_id, zip_path, work_order, planned_qty, total):
    """Insert every split record with upload_id; commit every 50. Owns `conn` (and its advisory lock)."""
    st = {"total": total, "done": 0, "uploaded": 0, "skipped": 0, "failed": 0, "invalid": 0}
    failures = []
    t0 = time.time()
    try:
        parser = reparse.load_parser()
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
        remove_upload_files(upload_id)
        _after_change(work_order, queue_report=st["uploaded"] > 0)
        events.put({"type": "done", **st, "elapsed_sec": elapsed, "failures": failures})
    except Exception as exc:
        log.exception("log upload %s failed", upload_id)
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
        _after_change(work_order, queue_report=False)
        events.put({"type": "error", "message": "上傳中斷，已寫入的資料可撤銷或重新上傳",
                    "message_en": "Upload interrupted. Rows already written can be undone or uploaded again.", **st})
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _after_change(work_order, queue_report):
    """Make the dashboard and AI report see the new rows right away."""
    try:
        stats.refresh(wait=True)
    except Exception:
        log.exception("statistics refresh after upload failed")
    if queue_report:
        try:
            reports.enqueue(work_order, "upload")
        except Exception:
            log.exception("could not queue the AI report for %s", work_order)
