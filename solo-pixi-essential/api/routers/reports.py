"""AI quality reports: latest / generate (streamed) / history / compare / markdown / follow-up."""
import queue
import threading

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse

import config
import db
from errors import ApiError, bad_request, conflict, not_found
from schemas import AskBody
from services import auth, llm, reports, upload_chat
from services.ingest import ndjson

router = APIRouter(prefix="/api/reports", tags=["ai-report"])

ROW_FIELDS = ("id", "work_order", "status", "trigger", "requested_by", "data_hash", "facts", "report_zh",
              "report_en", "checks", "model", "error", "created_at", "finished_at", "elapsed_sec")


def _row(row):
    return {k: row.get(k) for k in ROW_FIELDS} if row else None


def _get_row(conn, work_order, report_id):
    row = db.fetch_one(conn, "SELECT * FROM ai_reports WHERE id = %s AND work_order = %s", (report_id, work_order))
    if not row:
        raise not_found("找不到這份報告", "no_report")
    return row


@router.get("/{work_order}")
def latest(work_order: str):
    """Latest finished report for a work order, whether the data changed since, and any job in progress."""
    with db.db() as conn:
        row = reports.latest_done(conn, work_order)
        busy = reports.pending(conn, work_order)
        try:
            current = reports.facts_hash(reports.build_facts(conn, work_order))
        except ApiError:
            current = None
        count = db.fetch_one(conn, "SELECT COUNT(*) AS n FROM ai_reports WHERE work_order = %s AND status = 'done'",
                             (work_order,))["n"]
    return {
        "report": _row(row),
        "stale": bool(row and current and row["data_hash"] != current),
        "has_data": current is not None,
        "pending": busy,
        "history_count": count,
        "llm": llm.status(),
    }


@router.post("/{work_order}/generate")
def generate(work_order: str, force: bool = Query(True), user=Depends(auth.optional_user)):
    """Generate both languages now. NDJSON: facts → started → report (zh/en, as each finishes) → done | error."""
    events = queue.Queue()

    def run():
        try:
            row_id = reports.generate(work_order, "manual", user["username"] if user else None,
                                      on_event=events.put, force=force)
            events.put({"type": "done", "id": row_id})
        except RuntimeError:
            events.put({"type": "error", "code": "busy", "message": "這張工單的報告正在產生中，請稍候再開啟"})
        except ApiError as exc:
            events.put({"type": "error", "code": exc.code, "message": exc.message})
        except Exception:
            events.put({"type": "error", "code": "llm", "message": "AI 報告產生失敗，請確認 LLM 狀態後重試"})

    threading.Thread(target=run, daemon=True, name=f"report-{work_order}").start()

    def stream():
        while True:
            try:
                ev = events.get(timeout=15)
            except queue.Empty:
                yield ndjson({"type": "heartbeat"})
                continue
            yield ndjson(ev)
            if ev["type"] in ("done", "error"):
                break

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@router.get("/{work_order}/history")
def history(work_order: str, limit: int = Query(20, ge=1, le=100)):
    with db.db() as conn:
        rows = db.fetch_all(conn, """
            SELECT id, status, trigger, requested_by, created_at, finished_at, elapsed_sec,
                   facts->'risk'->>'level' AS risk, (facts->>'yield_pct')::float AS yield_pct,
                   (facts->>'units')::int AS units, error
            FROM ai_reports WHERE work_order = %s AND status IN ('done', 'failed', 'queued', 'running')
            ORDER BY id DESC LIMIT %s
        """, (work_order, limit))
    return {"items": rows}


@router.get("/{work_order}/compare")
def compare(work_order: str, base: int = Query(None), target: int = Query(None)):
    """Facts difference between two reports (default: the latest two finished ones)."""
    with db.db() as conn:
        if base is None or target is None:
            rows = db.fetch_all(conn, """
                SELECT * FROM ai_reports WHERE work_order = %s AND status = 'done' ORDER BY id DESC LIMIT 2
            """, (work_order,))
            if len(rows) < 2:
                raise bad_request("需要至少兩份報告才能比較", "not_enough_reports")
            t_row, b_row = rows[0], rows[1]
        else:
            b_row, t_row = _get_row(conn, work_order, base), _get_row(conn, work_order, target)
    return {"base": {"id": b_row["id"], "finished_at": b_row["finished_at"]},
            "target": {"id": t_row["id"], "finished_at": t_row["finished_at"]},
            **reports.compare(b_row["facts"] or {}, t_row["facts"] or {})}


@router.get("/{work_order}/markdown")
def markdown(work_order: str, lang: str = Query("zh", pattern="^(zh|en)$"), id: int = Query(None)):
    with db.db() as conn:
        row = _get_row(conn, work_order, id) if id else reports.latest_done(conn, work_order)
    if not row:
        raise not_found("這張工單還沒有報告", "no_report")
    body = reports.to_markdown(row, lang, config.APP_TZ)
    filename = f"AI_Report_{work_order}_{lang}.md"
    return Response(body, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/{work_order}/ask")
def ask(work_order: str, body: AskBody):
    """Follow-up question about this work order, answered only from its facts and report (NDJSON stream)."""
    try:
        messages = upload_chat.normalize_messages(body.messages)
    except ValueError as exc:
        raise bad_request(str(exc), "bad_messages")
    with db.db() as conn:
        row = reports.latest_done(conn, work_order)
    if not row:
        raise conflict("請先產生這張工單的報告", "no_report")
    prompt = reports.ask_messages(row, messages)

    def stream():
        parts = []
        try:
            for text in llm.stream_text(prompt, temperature=0.2, max_tokens=512):
                parts.append(text)
                yield ndjson({"type": "delta", "text": text})
            yield ndjson({"type": "done", "reply": "".join(parts).strip()})
        except Exception:
            yield ndjson({"type": "error", "message": "AI 助理目前無法回覆，請稍後再試"})

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@router.post("/run-daily", tags=["ai-report"])
def run_daily(_admin=Depends(auth.require_admin)):
    """Queue today's daily batch now (normally runs at REPORT_DAILY_AT)."""
    queued = reports.run_daily_batch(force=True)
    return {"queued": queued}
