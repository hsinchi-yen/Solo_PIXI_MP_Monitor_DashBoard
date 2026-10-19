"""AI report text: prompts, output schema, number check, Markdown, comparison.

Pure functions (no DB, no network) so they can be unit-tested anywhere.
"""
import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal

LANGS = ("zh", "en")
REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
        "recommendations": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
    },
    "required": ["summary", "findings", "recommendations"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = {
    "zh": (
        "你是無線模組（QCA9377 BT + WiFi）產線的測試品質工程師，依照系統提供的事實表撰寫工單品質報告。\n"
        "規則：\n"
        "1. 全部使用繁體中文。\n"
        "2. 只能引用事實表中出現過的數字，照原樣寫出；不可以自行計算新的數字（例如差值、比例、平均），也可以不寫數字。\n"
        "3. summary 是一句總評；findings 與 recommendations 各 3 點，每點一句。\n"
        "4. 提到失敗原因時，寫出錯誤碼與測試步驟名稱。\n"
        "5. 風險等級已由系統依門檻判定（risk.level），內容必須與它一致，不要自行改判。\n"
        "6. 不要使用 Markdown 符號。"
    ),
    "en": (
        "You are a test-quality engineer for a wireless module (QCA9377 BT + WiFi) production line. "
        "Write a work-order quality report from the facts table provided by the system.\n"
        "Rules:\n"
        "1. Write in English.\n"
        "2. Only use numbers that appear in the facts table, written as they appear. Do not compute new numbers "
        "(no differences, ratios or averages); you may also write no numbers at all.\n"
        "3. 'summary' is one sentence; 'findings' and 'recommendations' have exactly 3 items, one sentence each.\n"
        "4. When mentioning a failure cause, name its error code and test step.\n"
        "5. The risk level was decided by the system from thresholds (risk.level); stay consistent with it.\n"
        "6. Do not use Markdown symbols."
    ),
}

ASK_PROMPT = (
    "你是這張工單的測試品質助理。只能根據下方的事實表和已產生的報告回答使用者的問題。\n"
    "規則：1) 回答使用與問題相同的語言（預設繁體中文）。2) 只能引用事實表中的數字，不可推算或編造。"
    "3) 事實表沒有的資訊，直接說明目前資料沒有。4) 最多 5 句，不要使用 Markdown 標題或表格。\n"
)

NUM_RE = re.compile(r"(?<![A-Za-z_\d])-?\d[\d,]*(?:\.\d+)?")
IDENT_RE = re.compile(r"\d{4}-\d{6,}|[A-Za-z]+_[A-Za-z0-9_]*\d+[A-Za-z0-9_]*|\b\d+(?:\.\d+)?G\b|QCA\d+|PIXI-\w+", re.IGNORECASE)


def plain(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def level(value, good, warn, higher_is_better=True):
    if value is None:
        return "unknown"
    if higher_is_better:
        return "good" if value >= good else "warn" if value >= warn else "bad"
    return "good" if value <= good else "warn" if value <= warn else "bad"


def facts_hash(facts):
    return hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ─── Number check ────────────────────────────────────────────────────────────

def _fact_numbers(obj, out):
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _fact_numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _fact_numbers(v, out)
    elif isinstance(obj, str):
        for m in re.findall(r"\d+(?:\.\d+)?", obj):   # dates/times inside strings
            out.add(float(m))
    return out


def numbers_in(text):
    cleaned = IDENT_RE.sub(" ", text or "")
    values = []
    for m in NUM_RE.findall(cleaned):
        try:
            values.append((m, float(m.replace(",", ""))))
        except ValueError:
            pass
    return values


def check_numbers(report, facts):
    """→ {"ok": bool, "unknown": [numbers not found in the facts]}."""
    allowed = _fact_numbers(facts, set())
    allowed |= {abs(v) for v in allowed}
    allowed |= {1.0, 2.0, 3.0, 5.0}           # "3 張工單", "前 5 名"
    texts = [report.get("summary", "")] + list(report.get("findings", [])) + list(report.get("recommendations", []))
    unknown = []
    for text in texts:
        for raw, value in numbers_in(text):
            if not any(abs(value - a) < 1e-9 or any(abs(value - round(a, d)) < 1e-9 for d in (0, 1, 2))
                       for a in allowed):
                unknown.append(raw)
    return {"ok": not unknown, "unknown": sorted(set(unknown))}


# ─── Presentation ────────────────────────────────────────────────────────────

RISK_LABEL = {"zh": {"low": "低", "medium": "中", "high": "高"}, "en": {"low": "Low", "medium": "Medium", "high": "High"}}


def to_markdown(row, lang, tz="Asia/Taipei"):
    facts = row["facts"] or {}
    report = row.get(f"report_{lang}") or {}
    zh = lang == "zh"
    risk = (facts.get("risk") or {}).get("level", "")
    lines = [f"# {'工單品質報告' if zh else 'Work Order Quality Report'} — {facts.get('work_order', '')}", ""]
    gen = row.get("finished_at")
    if gen:
        from zoneinfo import ZoneInfo
        gen = gen.astimezone(ZoneInfo(tz)) if gen.tzinfo else gen
        lines.append(f"{'產生時間' if zh else 'Generated'}: {gen:%Y-%m-%d %H:%M}")
    lines.append(f"{'風險等級' if zh else 'Risk level'}: {RISK_LABEL[lang].get(risk, risk)}")
    lines += ["", f"## {'數據' if zh else 'Numbers'}", "",
              f"| {'項目' if zh else 'Item'} | {'數值' if zh else 'Value'} |", "|---|---|"]
    rows = [("測試台數" if zh else "Units", facts.get("units")), ("PASS", facts.get("pass")),
            ("FAIL", facts.get("fail")), ("STOP", facts.get("stop")),
            ("良率" if zh else "Yield", f"{facts.get('yield_pct')}%"),
            ("重測率" if zh else "Retry rate", f"{facts.get('retry_rate')}%")]
    planned = facts.get("planned")
    if planned:
        rows += [("計畫數" if zh else "Planned", planned.get("planned_qty")),
                 ("Aligned 良率" if zh else "Aligned yield", f"{planned.get('aligned_yield_pct')}%")]
    period = facts.get("period") or {}
    rows.append(("測試期間" if zh else "Period", f"{period.get('first_test', '')} ~ {period.get('last_test', '')}"))
    lines += [f"| {k} | {v} |" for k, v in rows]
    lines += ["", f"## {'總評' if zh else 'Summary'}", "", report.get("summary", ""), "",
              f"## {'發現' if zh else 'Findings'}", ""]
    lines += [f"{i}. {t}" for i, t in enumerate(report.get("findings", []), 1)]
    lines += ["", f"## {'建議' if zh else 'Recommendations'}", ""]
    lines += [f"{i}. {t}" for i, t in enumerate(report.get("recommendations", []), 1)]
    causes = facts.get("top_causes") or []
    if causes:
        lines += ["", f"## {'主要失敗原因' if zh else 'Top failure causes'}", "",
                  f"| {'錯誤碼' if zh else 'Error code'} | {'步驟' if zh else 'Step'} | {'次數' if zh else 'Count'} |", "|---|---|---|"]
        lines += [f"| {c['error_code']} | {c['step']} | {c['count']} |" for c in causes]
    return "\n".join(lines) + "\n"


def compare(base_facts, target_facts):
    """What changed between two reports' facts."""
    def g(f, *path):
        for p in path:
            f = (f or {}).get(p)
        return f
    keys = [("units", ("units",)), ("pass", ("pass",)), ("fail", ("fail",)), ("stop", ("stop",)),
            ("yield_pct", ("yield_pct",)), ("retry_rate", ("retry_rate",)),
            ("aligned_yield_pct", ("planned", "aligned_yield_pct")), ("risk", ("risk", "level"))]
    changes = []
    for name, path in keys:
        a, b = g(base_facts, *path), g(target_facts, *path)
        if a != b:
            delta = round(b - a, 2) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
            changes.append({"field": name, "before": a, "after": b, "delta": delta})
    a_codes = {c["error_code"]: c["count"] for c in base_facts.get("top_causes") or []}
    b_codes = {c["error_code"]: c["count"] for c in target_facts.get("top_causes") or []}
    return {
        "changes": changes,
        "causes_added": sorted(set(b_codes) - set(a_codes)),
        "causes_removed": sorted(set(a_codes) - set(b_codes)),
        "causes_changed": [{"error_code": k, "before": a_codes[k], "after": b_codes[k]}
                           for k in sorted(set(a_codes) & set(b_codes)) if a_codes[k] != b_codes[k]],
    }
