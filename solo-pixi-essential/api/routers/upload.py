"""Web Log Uploader: zip → split per station → parse → module_test.

Administrators and uploaders can upload; uploaders only reach the batches they analyzed.
"""
import json
import os
import queue
import shutil
import threading
import uuid
import zipfile

import psycopg2.extras
from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

import config
import db
import log_splitter
from errors import ApiError, bad_request, conflict
from schemas import ChatBody, CommitBody, ValidateBody
from services import auth, ingest, llm, stats, upload_chat

router = APIRouter(prefix="/api/log-upload", tags=["log-upload"])


@router.post("/analyze")
def analyze(request: Request, file: UploadFile = File(...), user=Depends(auth.current_user)):
    """Store the zip, split every station's Log_All.txt in memory and report facts. No module_test writes."""
    zip_name = os.path.basename(file.filename or "upload.zip")
    if not zip_name.lower().endswith(".zip"):
        raise bad_request("請選擇 .zip 壓縮檔", "not_zip")

    ingest.cleanup_stale_uploads()
    os.makedirs(config.UPLOAD_TMP_DIR, exist_ok=True)
    upload_id = uuid.uuid4().hex
    zip_path, hashes_path = ingest.upload_paths(upload_id)
    with open(zip_path, "wb") as out:
        shutil.copyfileobj(file.file, out, 1024 * 1024)
    zip_size = os.path.getsize(zip_path)

    try:
        with zipfile.ZipFile(zip_path) as zf:
            entries = log_splitter.find_log_all_entries(zf)
            if not entries:
                raise ValueError("壓縮檔內找不到站別資料夾（例如 10/20/30/40）下的 Log_All.txt")
            if sum(info.file_size for _, info in entries) > config.UPLOAD_MAX_LOG_ALL_BYTES:
                raise ValueError("Log_All.txt 解壓後總大小超過 500 MB 上限")
            facts, hashes = log_splitter.analyze_zip(zf, zip_name)
    except zipfile.BadZipFile:
        ingest.remove_upload_files(upload_id)
        raise bad_request("檔案不是有效的 zip 壓縮檔", "bad_zip")
    except ValueError as exc:
        ingest.remove_upload_files(upload_id)
        raise bad_request(str(exc), "no_log_all")
    except Exception:  # corrupt member, unsupported compression, ...
        ingest.remove_upload_files(upload_id)
        raise bad_request("無法讀取壓縮檔內容，檔案可能已損壞", "bad_zip")

    with open(hashes_path, "w") as f:
        json.dump(hashes, f)

    with db.db() as conn:
        facts["existing_by_wo"] = ingest.existing_by_wo(conn, hashes)
        db.execute(conn, """
            INSERT INTO upload_log (id, client_ip, username, user_role, zip_name, zip_size, inferred_wo,
                                    unique_mac1, total_records, facts, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'analyzed')
        """, (upload_id, auth.client_ip(request), user["username"], user["role"], zip_name, zip_size,
              facts["inferred_wo"], facts["unique_mac1"], facts["total_records"], psycopg2.extras.Json(facts)))
    return {"upload_id": upload_id, "facts": facts, "chat_opening": upload_chat.opening_message(facts)}


@router.post("/validate")
def validate(body: ValidateBody, user=Depends(auth.current_user)):
    """Run the upload rules (R1–R8) for the entered work order / planned quantity."""
    work_order = (body.work_order or "").strip()
    with db.db() as conn:
        row = ingest.owned_upload(conn, body.upload_id, user, "找不到這個上傳批次，請重新選擇 zip")
        facts = row["facts"]
        issues = log_splitter.validate_upload(facts, work_order, body.planned_qty,
                                              facts.get("existing_by_wo"), ingest.current_target(conn, work_order))
    return ingest.validation_payload(issues)


def _chat_extract(messages):
    """(work_order, planned_qty, status) from the latest user message; status: ok | bad_output | unreachable."""
    try:
        data = llm.complete_json(upload_chat.build_extract_messages(messages), upload_chat.EXTRACT_SCHEMA,
                                 name="upload_fields", temperature=0, max_tokens=upload_chat.EXTRACT_MAX_TOKENS)
        wo, qty = upload_chat.parse_extract(json.dumps(data))
    except (ValueError, TypeError):
        return None, None, "bad_output"
    except Exception:
        return None, None, "unreachable"
    return wo, qty, "ok"


@router.post("/chat")
def chat(body: ChatBody, user=Depends(auth.current_user)):
    """One AI-assistant turn → NDJSON: extract, validation, delta…, done | fallback.

    The rules (R1–R8) decide; the LLM only phrases their result. `known` carries the values
    already confirmed in the form or earlier turns, so switching modes keeps them."""
    try:
        messages = upload_chat.normalize_messages(body.messages)
    except ValueError as exc:
        raise bad_request(str(exc), "bad_messages")
    upload_id = body.upload_id
    with db.db() as conn:
        row = ingest.owned_upload(conn, upload_id, user, "找不到這個上傳批次，請重新選擇 zip")
        if row["status"] not in ("analyzed", "failed"):
            raise conflict(f"這個批次已經處理過（狀態：{row['status']}）", "already_processed")
        facts = row["facts"]
        llm_wo, llm_qty, status = _chat_extract(messages)
        fields = upload_chat.merge_fields(body.known, messages[-1]["content"], llm_wo, llm_qty, llm_ok=(status == "ok"))
        issues = log_splitter.validate_upload(facts, fields["work_order"] or "", fields["planned_qty"],
                                              facts.get("existing_by_wo"),
                                              ingest.current_target(conn, fields["work_order"]))

    def events():
        yield ingest.ndjson({"type": "extract", **fields, "extract_ok": status == "ok"})
        yield ingest.ndjson({"type": "validation", **ingest.validation_payload(issues)})
        if status == "unreachable":
            yield ingest.ndjson({"type": "fallback", "reason": "AI 助理目前無法連線"})
            return
        parts = []
        try:
            for text in llm.stream_text(upload_chat.build_reply_messages(facts, fields, issues, messages),
                                        temperature=0.3, max_tokens=upload_chat.REPLY_MAX_TOKENS):
                parts.append(text)
                yield ingest.ndjson({"type": "delta", "text": text})
        except Exception:
            yield ingest.ndjson({"type": "fallback", "reason": "AI 助理回覆逾時或中斷"})
            return
        reply = "".join(parts).strip()
        if not reply:
            reply = upload_chat.fallback_reply(fields)
            yield ingest.ndjson({"type": "delta", "text": reply})
        _save_chat_transcript(upload_id, messages + [{"role": "assistant", "content": reply}])
        yield ingest.ndjson({"type": "done", "reply": reply})

    return StreamingResponse(events(), media_type="application/x-ndjson")


def _save_chat_transcript(upload_id, transcript):
    """Keep the server's copy of the conversation even if the batch is never committed."""
    try:
        with db.db() as conn:
            db.execute(conn, """
                UPDATE upload_log SET chat_transcript = %s WHERE id = %s AND status IN ('analyzed', 'failed')
            """, (psycopg2.extras.Json(transcript), upload_id))
    except Exception:
        pass


@router.post("/commit")
def commit(body: CommitBody, user=Depends(auth.current_user)):
    """Re-validate against the current DB, then ingest in a background thread and stream progress as NDJSON.
    The ingest keeps running if the browser disconnects; the outcome is recorded in upload_log."""
    upload_id = body.upload_id
    work_order = (body.work_order or "").strip()
    conn = db.connect()
    handed_off = False
    try:
        row = ingest.owned_upload(conn, upload_id, user, "找不到這個上傳批次，請重新選擇 zip")
        if row["status"] not in ("analyzed", "failed"):
            raise conflict(f"這個批次已經處理過（狀態：{row['status']}）", "already_processed")
        zip_path, hashes_path = ingest.upload_paths(upload_id)
        if not (os.path.exists(zip_path) and os.path.exists(hashes_path)):
            raise ApiError(410, "暫存檔已過期，請重新選擇 zip", "expired")

        # Session-level lock; released when this connection closes (early return or ingest end).
        if not db.fetch_one(conn, "SELECT pg_try_advisory_lock(%s) AS ok", (config.UPLOAD_LOCK_KEY,))["ok"]:
            raise conflict("已有其他上傳正在進行，請稍後再試", "upload_running")

        with open(hashes_path) as f:
            hashes = json.load(f)
        facts = row["facts"]
        facts["existing_by_wo"] = ingest.existing_by_wo(conn, hashes)
        prev_target = ingest.current_target(conn, work_order)
        issues = log_splitter.validate_upload(facts, work_order, body.planned_qty, facts["existing_by_wo"], prev_target)
        payload = ingest.validation_payload(issues)
        if not payload["can_upload"]:
            raise bad_request("輸入資料有誤，請修正後再上傳", "invalid", **payload)
        if payload["needs_ack"] and not body.ack_warnings:
            raise bad_request("有警告尚未確認", "needs_ack", **payload)

        qty = log_splitter.parse_quantity(body.planned_qty)
        # chat_transcript is already stored turn by turn by /chat (server copy, not the client's).
        db.execute(conn, """
            UPDATE upload_log
            SET work_order = %s, planned_qty = %s, prev_target = %s, facts = %s, validation = %s,
                mode = %s, status = 'running', error = NULL
            WHERE id = %s
        """, (work_order, qty, prev_target, psycopg2.extras.Json(facts), psycopg2.extras.Json(issues),
              body.mode, upload_id))
        conn.commit()

        events = queue.Queue()
        threading.Thread(target=ingest.run_ingest,
                         args=(conn, events, upload_id, zip_path, work_order, qty, facts["total_records"]),
                         daemon=True, name=f"ingest-{upload_id[:8]}").start()
        handed_off = True
        return StreamingResponse(ingest.ndjson_events(events), media_type="application/x-ndjson")
    finally:
        if not handed_off:
            conn.close()


@router.get("/history")
def history(limit: int = Query(20, ge=1, le=200), username: str = None, user=Depends(auth.current_user)):
    """Recent batches. Uploaders always get only their own; the administrator may filter by uploader."""
    if user["role"] != "admin":
        username = user["username"]
    where, params = "WHERE l.status <> 'analyzed'", []
    if username:
        where += " AND l.username = %s"
        params.append(username)
    with db.db() as conn:
        items = db.fetch_all(conn, f"""
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
    return {"items": items}


@router.get("/{upload_id}")
def detail(upload_id: str, user=Depends(auth.current_user)):
    with db.db() as conn:
        return ingest.owned_upload(conn, upload_id, user)


@router.delete("/{upload_id}")
def revert(upload_id: str, user=Depends(auth.current_user)):
    """Undo one web upload: delete only the rows it inserted and restore the alignment target it set."""
    with db.db() as conn:
        row = ingest.owned_upload(conn, upload_id, user)
        if row["status"] not in ("done", "failed"):
            raise bad_request(f"只有已完成或失敗的批次可以撤銷（目前狀態：{row['status']}）", "not_revertible")
        reverted = db.execute(conn, "DELETE FROM module_test WHERE upload_id = %s", (upload_id,))
        wo, qty = row["work_order"], row["planned_qty"]
        if wo and qty and ingest.current_target(conn, wo) == qty:
            if row["prev_target"] is None:
                db.execute(conn, "DELETE FROM alignment_targets WHERE work_order = %s", (wo,))
            else:
                db.execute(conn, "UPDATE alignment_targets SET target_total = %s, updated_at = NOW() WHERE work_order = %s",
                           (row["prev_target"], wo))
        db.execute(conn, """
            UPDATE upload_log SET status = 'reverted', reverted = %s, reverted_by = %s, reverted_at = NOW()
            WHERE id = %s
        """, (reverted, user["username"], upload_id))
    stats.refresh(wait=True)
    return {"reverted": reverted, "upload_id": upload_id}
