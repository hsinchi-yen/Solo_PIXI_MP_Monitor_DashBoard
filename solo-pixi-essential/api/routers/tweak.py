"""DB Tweak: browse / delete test records, download raw logs, reparse (administrator only)."""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse

import db
from errors import bad_request, not_found
from routers.analytics import scope_params
from services import auth, reparse, stats
from services.filters import Scope, raw_where

router = APIRouter(prefix="/api/tweak", tags=["tweak"])
jobs = APIRouter(prefix="/api/reparse", tags=["tweak"])


@router.get("/records")
def records(scope: Scope = Depends(scope_params), page: int = Query(1, ge=1),
            page_size: int = Query(50, ge=1, le=500), search: str = Query(None, max_length=64),
            _admin=Depends(auth.require_admin)):
    extra, s_params = [], []
    if search and search.strip():
        extra.append("(mac1 ILIKE %s OR mac2 ILIKE %s OR source_file ILIKE %s)")
        s_params = [f"%{search.strip()}%"] * 3
    where, params = raw_where(scope, extra)
    params += s_params
    with db.db() as conn:
        total = db.fetch_one(conn, f"SELECT COUNT(*) AS n FROM module_test {where}", params)["n"]
        pages = max(1, -(-total // page_size))
        page = min(page, pages)
        items = db.fetch_all(conn, f"""
            SELECT id, mac1, mac2, work_order, COALESCE(unit_date, start_time::date) AS unit_date, start_time,
                   test_duration_sec, result, source_file, fail_step_name, fail_message,
                   (raw_log IS NOT NULL AND raw_log <> '') AS has_raw_log, upload_id
            FROM module_test {where}
            ORDER BY start_time DESC, id DESC LIMIT %s OFFSET %s
        """, params + [page_size, (page - 1) * page_size])
    return {"items": items, "total": total, "page": page, "page_size": page_size, "pages": pages}


@router.delete("/delete-scope")
def delete_scope(scope: Scope = Depends(scope_params), _admin=Depends(auth.require_admin)):
    """Delete every record in the scope (needs a date range or a work order)."""
    if scope.is_empty:
        raise bad_request("請至少指定日期範圍或工單，才能批次刪除", "scope_required")
    where, params = raw_where(scope)
    with db.db() as conn:
        deleted = db.execute(conn, f"DELETE FROM module_test {where}", params)
    stats.refresh(wait=True)
    return {"deleted": deleted, "scope": scope.describe()}


@router.delete("/records/{record_id}")
def delete_record(record_id: int, _admin=Depends(auth.require_admin)):
    with db.db() as conn:
        deleted = db.execute(conn, "DELETE FROM module_test WHERE id = %s", (record_id,))
    if not deleted:
        raise not_found("找不到這筆紀錄", "no_record")
    stats.refresh(wait=True)
    return {"deleted": deleted, "id": record_id}


@router.get("/raw-log/{record_id}", response_class=PlainTextResponse)
def raw_log(record_id: int, _admin=Depends(auth.require_admin)):
    """Same as /api/raw-log but behind the admin session (token in the header, never in the URL)."""
    with db.db() as conn:
        row = db.fetch_one(conn, "SELECT raw_log, source_file FROM module_test WHERE id = %s", (record_id,))
    if not row or not row["raw_log"]:
        raise not_found("這筆紀錄沒有原始 log", "no_raw_log")
    filename = (row["source_file"] or f"record_{record_id}.txt").replace('"', "")
    return PlainTextResponse(row["raw_log"], headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@jobs.post("")
def start_reparse(admin=Depends(auth.require_admin)):
    """Start a background reparse of every raw log; poll GET /api/reparse for progress."""
    return reparse.start(admin["username"])


@jobs.get("")
def reparse_status(_admin=Depends(auth.require_admin)):
    with db.db() as conn:
        return reparse.latest_job(conn) or {}


@jobs.post("/{job_id}/cancel")
def cancel_reparse(job_id: int, _admin=Depends(auth.require_admin)):
    if not reparse.cancel(job_id):
        raise not_found("這個工作沒有在執行", "no_job")
    return {"ok": True}
