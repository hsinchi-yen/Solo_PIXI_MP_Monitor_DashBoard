"""AI quality reports per work order.

1. build_facts(): every number the report may mention, computed in SQL.
2. The LLM writes only sentences, as fixed-field JSON (summary / 3 findings / 3
   recommendations), in Traditional Chinese and English (two parallel calls).
3. check_numbers(): every number in those sentences must appear in the facts;
   on a mismatch the language is regenerated once, then flagged.
4. Rows in ai_reports hold both languages; a report is reused while the facts hash
   is unchanged, so every worker and every viewer sees the same report.

Reports are generated on demand, after a web upload, and in the daily batch.
"""
import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import config
import db
from errors import Msg, not_found
from services import llm, stats
from services.filters import Scope
from services.report_text import (ASK_PROMPT, LANGS, REPORT_SCHEMA, SYSTEM_PROMPT, check_numbers,  # noqa: F401
                                  compare, facts_hash, level, plain, to_markdown)

log = logging.getLogger("pixi.reports")

# ─── Facts ───────────────────────────────────────────────────────────────────

def build_facts(conn, work_order):
    scope = Scope(work_order=work_order)
    s = stats.summary(conn, scope)
    if not s["total"]:
        raise not_found(Msg(f"找不到工單 {work_order} 的測試資料", f"No test data for work order {work_order}."), "no_data")
    days = db.fetch_one(conn, "SELECT COUNT(DISTINCT eff_date) AS n FROM mv_unit_day WHERE wo_key = %s",
                        (work_order,))["n"]
    dur = db.fetch_one(conn, """
        SELECT ROUND((SUM(dur_sum) / NULLIF(SUM(dur_n), 0))::numeric, 1) AS avg FROM mv_unit_day WHERE wo_key = %s
    """, (work_order,))["avg"]
    causes = stats.top_fail_causes(conn, scope, 5)
    categories = db.fetch_all(conn, """
        SELECT COALESCE(NULLIF(fail_category, ''), '未分類') AS category, COUNT(*) AS count
        FROM module_test WHERE work_order = %s AND result IN ('FAIL','STOP')
        GROUP BY 1 ORDER BY count DESC LIMIT 5
    """, (work_order,))
    target = db.fetch_one(conn, "SELECT target_total FROM alignment_targets WHERE work_order = %s", (work_order,))
    planned = target["target_total"] if target else None

    all_wo = stats.work_order_summary(conn, Scope())
    names = [r["work_order"] for r in all_wo]
    idx = names.index(work_order) if work_order in names else -1
    previous = []
    if idx >= 0:
        for r in all_wo[idx + 1: idx + 4]:
            previous.append({"work_order": r["work_order"], "units": r["total"],
                             "yield_pct": r["yield_pct"], "retry_rate": r["retry_rate"]})

    yield_level = level(s["yield_pct"], config.YIELD_GOOD, config.YIELD_WARN)
    retry_level = level(s["retry_rate"], config.RETRY_GOOD, config.RETRY_WARN, higher_is_better=False)
    levels = {yield_level, retry_level}
    risk = "high" if "bad" in levels else "medium" if "warn" in levels else "low"

    facts = {
        "work_order": work_order,
        "units": s["total"], "pass": s["pass"], "fail": s["fail"], "stop": s["stop"],
        "yield_pct": s["yield_pct"], "test_attempts": s["test_attempts"],
        "retry_units": s["retry_units"], "retry_rate": s["retry_rate"], "stop_to_pass": s["stop_to_pass"],
        "period": {"first_test": s["first_test"], "last_test": s["last_test"], "days": days,
                   "avg_test_sec": dur},
        "top_causes": [{"error_code": c["error_code"] or "—", "step": c["step"] or "—",
                        "category": c["category"] or "—", "count": c["count"]} for c in causes],
        "categories": categories,
        "planned": None,
        "previous": previous,
        "thresholds": {"yield_good": config.YIELD_GOOD, "yield_warn": config.YIELD_WARN,
                       "retry_good": config.RETRY_GOOD, "retry_warn": config.RETRY_WARN},
        "risk": {"level": risk, "yield": yield_level, "retry": retry_level},
    }
    if planned:
        gap = max(0, planned - s["total"])
        facts["planned"] = {
            "planned_qty": planned, "tested_units": s["total"], "gap": gap,
            "completion_pct": round(s["total"] * 100.0 / planned, 2),
            "aligned_yield_pct": round(s["pass"] * 100.0 / (s["total"] + gap), 2),
        }
    if previous:
        ys = [p["yield_pct"] for p in previous if p["yield_pct"] is not None]
        rs = [p["retry_rate"] for p in previous if p["retry_rate"] is not None]
        facts["previous_avg"] = {
            "yield_pct": round(sum(ys) / len(ys), 2) if ys else None,
            "retry_rate": round(sum(rs) / len(rs), 2) if rs else None,
        }
        if facts["previous_avg"]["yield_pct"] is not None:
            facts["previous_avg"]["yield_change"] = round(s["yield_pct"] - facts["previous_avg"]["yield_pct"], 2)
        if facts["previous_avg"]["retry_rate"] is not None:
            facts["previous_avg"]["retry_change"] = round(s["retry_rate"] - facts["previous_avg"]["retry_rate"], 2)
    return plain(facts)


# ─── Generation ──────────────────────────────────────────────────────────────

def _messages(facts, lang, feedback=None):
    user = ("事實表（JSON）：\n" if lang == "zh" else "Facts table (JSON):\n") + json.dumps(facts, ensure_ascii=False)
    if feedback:
        user += ("\n\n上一版用了事實表沒有的數字：" if lang == "zh" else "\n\nThe previous draft used numbers that are not in the facts: ") \
                + ", ".join(feedback) + ("。請移除或改用事實表中的數字。" if lang == "zh" else ". Remove them or use numbers from the facts.")
    return [{"role": "system", "content": SYSTEM_PROMPT[lang]}, {"role": "user", "content": user}]


def write_language(facts, lang):
    """One language: LLM → schema-checked JSON → number check (one retry)."""
    report = None
    check = None
    feedback = None
    for _ in range(2):
        report = llm.complete_json(_messages(facts, lang, feedback), REPORT_SCHEMA, name="quality_report",
                                   timeout=config.LLM_REPORT_TIMEOUT, temperature=0.2,
                                   max_tokens=config.LLM_REPORT_MAX_TOKENS)
        report = {"summary": str(report.get("summary", "")).strip(),
                  "findings": [str(x).strip() for x in report.get("findings", [])][:3],
                  "recommendations": [str(x).strip() for x in report.get("recommendations", [])][:3]}
        check = check_numbers(report, facts)
        if check["ok"]:
            break
        feedback = check["unknown"]
    return report, check


def _wo_lock_key(work_order):
    return int(hashlib.sha256(f"ai_report:{work_order}".encode()).hexdigest()[:15], 16)


def latest_done(conn, work_order):
    return db.fetch_one(conn, """
        SELECT * FROM ai_reports WHERE work_order = %s AND status = 'done'
        ORDER BY finished_at DESC NULLS LAST, id DESC LIMIT 1
    """, (work_order,))


def pending(conn, work_order):
    return db.fetch_one(conn, """
        SELECT id, status, trigger, created_at, started_at FROM ai_reports
        WHERE work_order = %s AND status IN ('queued', 'running')
        ORDER BY id DESC LIMIT 1
    """, (work_order,))


def generate(work_order, trigger="manual", requested_by=None, on_event=None, row_id=None, force=True):
    """Generate (or reuse) the report for one work order. Returns the ai_reports row id.

    Holds a per-work-order advisory lock for the whole run, so a manual request and the
    background worker never generate the same work order twice at once. Raises
    RuntimeError('busy') when another generation for this work order is running."""
    emit = on_event or (lambda ev: None)
    conn = db.connect()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (_wo_lock_key(work_order),))
        if not cur.fetchone()["ok"]:
            raise RuntimeError("busy")
        t0 = time.time()
        try:
            facts = build_facts(conn, work_order)
            digest = facts_hash(facts)
            emit({"type": "facts", "facts": facts, "data_hash": digest})
            prev = latest_done(conn, work_order)
            if prev and prev["data_hash"] == digest and not force:
                if row_id:
                    cur.execute("UPDATE ai_reports SET status = 'skipped', finished_at = NOW(), error = 'unchanged' WHERE id = %s",
                                (row_id,))
                emit({"type": "cached", "id": prev["id"]})
                return prev["id"]
            if row_id:
                cur.execute("""
                    UPDATE ai_reports SET status = 'running', started_at = NOW(), facts = %s, data_hash = %s, model = %s
                    WHERE id = %s
                """, (json.dumps(facts, ensure_ascii=False), digest, config.LLM_MODEL, row_id))
            else:
                cur.execute("""
                    INSERT INTO ai_reports (work_order, status, trigger, requested_by, data_hash, facts, model, started_at)
                    VALUES (%s, 'running', %s, %s, %s, %s, %s, NOW()) RETURNING id
                """, (work_order, trigger, requested_by, digest, json.dumps(facts, ensure_ascii=False), config.LLM_MODEL))
                row_id = cur.fetchone()["id"]
            emit({"type": "started", "id": row_id})

            results, checks, errors = {}, {}, {}
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {pool.submit(write_language, facts, lang): lang for lang in LANGS}
                for fut in as_completed(futures):
                    lang = futures[fut]
                    try:
                        report, check = fut.result()
                        results[lang], checks[lang] = report, check
                        emit({"type": "report", "lang": lang, "report": report, "check": check})
                    except Exception as exc:
                        errors[lang] = str(exc)[:300]
                        emit({"type": "lang_error", "lang": lang, "message": "AI 無法產生這個語言的報告",
                              "message_en": "The AI could not write the report in this language."})
            status = "done" if results else "failed"
            cur.execute("""
                UPDATE ai_reports SET status = %s, report_zh = %s, report_en = %s, checks = %s, error = %s,
                       finished_at = NOW(), elapsed_sec = %s
                WHERE id = %s
            """, (status,
                  json.dumps(results.get("zh"), ensure_ascii=False) if "zh" in results else None,
                  json.dumps(results.get("en"), ensure_ascii=False) if "en" in results else None,
                  json.dumps(checks), json.dumps(errors) if errors else None,
                  round(time.time() - t0, 1), row_id))
            log.info("report %s for %s: %s in %.1fs", row_id, work_order, status, time.time() - t0)
            return row_id
        except Exception as exc:
            if row_id:
                cur.execute("UPDATE ai_reports SET status = 'failed', error = %s, finished_at = NOW() WHERE id = %s",
                            (str(exc)[:300], row_id))
            raise
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_wo_lock_key(work_order),))
    finally:
        conn.close()


# ─── Queue (upload / daily) ──────────────────────────────────────────────────

def enqueue(work_order, trigger, requested_by=None):
    """Queue a background generation unless one is already queued or running."""
    if not config.REPORT_AUTO:
        return None
    with db.db() as conn:
        if pending(conn, work_order):
            return None
        row = db.fetch_one(conn, """
            INSERT INTO ai_reports (work_order, status, trigger, requested_by) VALUES (%s, 'queued', %s, %s)
            RETURNING id
        """, (work_order, trigger, requested_by))
        return row["id"]


_worker_busy = threading.Lock()


def process_queue_once():
    """Run queued generations one at a time (one worker process at a time via advisory lock)."""
    if not _worker_busy.acquire(blocking=False):
        return 0
    conn = db.connect()
    done = 0
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (config.REPORT_WORKER_LOCK_KEY,))
        if not cur.fetchone()["ok"]:
            return 0
        try:
            # Jobs left 'running' by a stopped worker are failed after 15 minutes.
            cur.execute("""
                UPDATE ai_reports SET status = 'failed', error = 'worker stopped', finished_at = NOW()
                WHERE status = 'running' AND started_at < NOW() - INTERVAL '15 minutes'
            """)
            while True:
                cur.execute("""
                    SELECT id, work_order, trigger, requested_by FROM ai_reports
                    WHERE status = 'queued' ORDER BY created_at, id LIMIT 1
                """)
                job = cur.fetchone()
                if not job:
                    break
                try:
                    generate(job["work_order"], job["trigger"], job["requested_by"], row_id=job["id"], force=False)
                except RuntimeError:
                    cur.execute("UPDATE ai_reports SET status = 'skipped', error = 'busy', finished_at = NOW() WHERE id = %s",
                                (job["id"],))
                except Exception as exc:
                    log.warning("queued report %s for %s failed: %s", job["id"], job["work_order"], exc)
                    cur.execute("UPDATE ai_reports SET status = 'failed', error = %s, finished_at = NOW() WHERE id = %s AND status <> 'done'",
                                (str(exc)[:300], job["id"]))
                done += 1
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (config.REPORT_WORKER_LOCK_KEY,))
    finally:
        conn.close()
        _worker_busy.release()
    return done


def run_daily_batch(today=None, force=False):
    """Queue a report for every work order tested today (local date). Once per day unless forced."""
    from zoneinfo import ZoneInfo
    today = today or datetime.now(ZoneInfo(config.APP_TZ)).date()
    conn = db.connect()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (config.DAILY_BATCH_LOCK_KEY,))
        if not cur.fetchone()["ok"]:
            return []
        try:
            cur.execute("SELECT value FROM app_meta WHERE key = 'daily_reports_date'")
            row = cur.fetchone()
            if row and row["value"] == today.isoformat() and not force:
                return []
            cur.execute("""
                SELECT DISTINCT work_order FROM module_test
                WHERE COALESCE(unit_date, start_time::date) = %s AND work_order IS NOT NULL AND work_order <> ''
            """, (today,))
            wos = [r["work_order"] for r in cur.fetchall()]
            cur.execute("""
                INSERT INTO app_meta (key, value, updated_at) VALUES ('daily_reports_date', %s, NOW())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """, (today.isoformat(),))
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", (config.DAILY_BATCH_LOCK_KEY,))
    finally:
        conn.close()
    queued = [wo for wo in wos if enqueue(wo, "daily")]
    log.info("daily batch %s: %d work orders, %d queued", today, len(wos), len(queued))
    return queued


def daily_due(now=None):
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo(config.APP_TZ))
    hh, mm = (int(x) for x in config.REPORT_DAILY_AT.split(":"))
    return (now.hour, now.minute) >= (hh, mm)


def ask_messages(row, messages, lang="zh"):
    report = row.get("report_en" if lang == "en" else "report_zh") or row.get("report_zh") or row.get("report_en")
    context = {"facts": row["facts"], "report": report}
    system = (ASK_PROMPT.format(default="English" if lang == "en" else "繁體中文")
              + "\n事實表與報告（JSON）：\n" + json.dumps(context, ensure_ascii=False))
    return [{"role": "system", "content": system}] + messages[-10:]
