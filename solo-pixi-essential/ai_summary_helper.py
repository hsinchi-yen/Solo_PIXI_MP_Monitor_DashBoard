"""
Pure helper: builds the LLM messages list for ai_summary.
Extracted so it can be unit-tested without touching DB or network.

mode="normal"   : full report with Markdown, ZH or EN
mode="carousel" : concise plain-text English for terminal typewriter display
"""
from typing import Any


def _alert_labels(yield_pct: float):
    if yield_pct >= 99.2:
        return "正常 (NORMAL)", "NORMAL"
    if yield_pct >= 98.5:
        return "警告 (WARNING)", "WARNING"
    return "告警 (ALARM)", "ALARM"


def _retest_alert_labels(retry_rate: float):
    """ICT Retest Rate thresholds for wireless module MP stable phase."""
    if retry_rate <= 3.0:
        return "正常 (NORMAL)", "NORMAL", "ok"
    if retry_rate <= 5.0:
        return "警告 (WARNING)", "WARNING", "warn"
    if retry_rate <= 8.0:
        return "告警 (ALARM)", "ALARM", "err"
    return "嚴重告警 (CRITICAL 🛑)", "CRITICAL", "err"


def build_summary_messages(
    stats: dict[str, Any],
    fails_text: str,
    wo: str,
    lang: str,
    mode: str = "normal",
) -> list[dict]:
    yield_pct = float(stats["yield_pct"])
    alert_zh, alert_en = _alert_labels(yield_pct)
    alert_tag = "ok" if yield_pct >= 99.2 else "warn" if yield_pct >= 98.5 else "err"

    retry_rate = float(stats.get("retry_rate", 0))
    retest_zh, retest_en, retest_tag = _retest_alert_labels(retry_rate)

    if mode == "carousel":
        # Structured English report for terminal typewriter display.
        # Must follow the exact section-header format so parseTerminalSegments()
        # can classify lines into report-title / section-header / content.
        fails_display = fails_text if fails_text else "No specific failures (all passed)"
        system_msg = (
            "You are a professional manufacturing test engineer. "
            "Respond in English only. Do NOT use markdown symbols (no **, no #, no -). "
            "Use plain text with the exact numbered-section structure shown in the template. "
            "注意：请完全使用英文回复，不要使用中文字符，不要使用markdown符号。"
        )
        prompt = (
            f"[IMPORTANT] 注意：请完全用英文回复，不使用markdown符号，严格按照模板格式输出。\n\n"
            f"Generate a PIXI Module test quality report for work order {wo}.\n"
            f"You MUST follow this EXACT template structure (plain text, no markdown):\n\n"
            f"PIXI Module Test Summary Report\n\n"
            f"1. General Information\n"
            f"Work Order: {wo}\n"
            f"Report Status: Completed\n"
            f"Test Type: Module Test & Calibration\n\n"
            f"2. Test Statistics\n"
            f"Total Units Tested: <num>{stats['total']}</num>\n"
            f"Total Passed: <ok>{stats['passed']}</ok>\n"
            f"Total Failed: <err>{stats['failed']}</err>\n"
            f"Yield Rate: <num>{yield_pct}%</num>\n"
            f"Yield Alert: <{alert_tag}>{alert_en}</{alert_tag}> (Threshold: >=99.2% Normal, 98.5-99.19% Warning, <98.5% Alarm)\n"
            f"Retry Rate: <{retest_tag}>{retry_rate}%</{retest_tag}> [{retest_en}]\n"
            f"Retry Rate Thresholds: <=3% Normal, 3-5% Warning, 5-8% Alarm, >8% Critical(STOP LINE)\n\n"
            f"3. Yield Analysis\n"
            f"[Write 2 sentences: assess yield vs alert threshold, production status.]\n\n"
            f"4. Failure Analysis & Recommendations\n"
            f"Failure Root Cause: <err>{fails_display}</err>\n"
            f"Retest Rate Assessment: <{retest_tag}>{retest_en}</{retest_tag}> — "
            f"[Write 1-2 sentences on retest rate status. If WARNING: recommend probe cleaning and fixture check. "
            f"If ALARM: recommend immediate PE investigation, suspend station, perform RCA. "
            f"If CRITICAL: recommend line stop, initiate 8D/CAPA. "
            f"If NORMAL: confirm retest rate is within acceptable range.]\n"
            f"[Write 1-2 additional sentences of follow-up recommendations based on the failure data. "
            f"Enclose any specific numbers, percentages, or units in <num>...</num> tags, "
            f"any positive statuses in <ok>...</ok> tags, "
            f"and any negative statuses or warnings in <err>...</err> tags.]\n\n"
            f"Fill in the bracketed placeholders with your analysis. "
            f"Keep section headers exactly as shown (e.g. '1. General Information'). "
            f"ENGLISH ONLY. No markdown. 请用纯英文。 Use the xml-like tags (<num>, <ok>, <err>, <warn>) strictly for highlighting."
        )
        return [
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": prompt},
        ]

    if lang == "en":
        system_msg = (
            "You are a professional manufacturing test engineer and data analysis expert. "
            "You must always respond in English only. Never use Chinese characters in your response. "
            "注意：请完全使用英文回复，绝对不要使用中文字符。"
        )
        prompt = (
            f"[IMPORTANT] 注意：请完全使用英文回复，不要使用任何中文字符。\n"
            f"You MUST write this entire response in English. Do NOT use Chinese characters.\n\n"
            f"Generate a concise PIXI Module test summary report for work order {wo} "
            f"as a professional manufacturing test engineer.\n\n"
            f"Work order data:\n"
            f"- Total tested: {stats['total']}\n"
            f"- Pass: {stats['passed']}\n"
            f"- Fail: {stats['failed']}\n"
            f"- Yield: {yield_pct}%\n"
            f"- Yield Alert: {alert_en} (thresholds: >=99.2% Normal, 98.5%-99.19% Warning, <98.5% Alarm)\n"
            f"- Retry Rate: {retry_rate}%  → {retest_en} (Thresholds: ≤3% Normal | 3-5% Warning | 5-8% Alarm | >8% Critical-STOP)\n"
            f"- Retest Alert: {retest_en} — if not Normal, include recommended action: "
            f"Warning=probe cleaning & fixture check; Alarm=suspend station & PE RCA; Critical=line stop & 8D/CAPA\n"
            f"- Main failure reasons: {fails_text}\n\n"
            f"Requirements:\n"
            f"1. Analyze whether the yield meets the target based on the yield alert level above.\n"
            f"2. Analyze the retest rate status and provide appropriate action recommendation based on its alert level.\n"
            f"3. Provide brief follow-up recommendations for any failure causes.\n"
            f"4. Use Markdown format with bullet lists and section headers.\n"
            f"5. Do NOT output JSON.\n"
            f"6. 请用英文回复。WRITE IN ENGLISH ONLY. DO NOT USE CHINESE.\n"
        )
    else:
        system_msg = "你是一個專業的製造測試工程師與數據分析專家。請一律以繁體中文回答。"
        fails_display = fails_text if fails_text else "無特定異常(或全數Pass)"
        prompt = (
            f"請以繁體中文且專業的測試工程師口吻，為工單 {wo} 產出一份簡短的測試總結報告。\n"
            f"工單數據如下：\n"
            f"- 總測試數: {stats['total']}\n"
            f"- Pass: {stats['passed']}\n"
            f"- Fail: {stats['failed']}\n"
            f"- 良率: {yield_pct}%\n"
            f"- 良率告警: {alert_zh}（門檻：≥99.2% 正常，98.5%~99.19% 警告，<98.5% 告警）\n"
            f"- 重測率: {retry_rate}%  → {retest_zh}（門檻：≤3% 正常 | 3~5% 警告 | 5~8% 告警 | >8% 嚴重/停線）\n"
            f"- 重測率等級: {retest_zh} — 若非正常，請依等級給出應對措施建議：\n"
            f"  警告=治具探針清潔 & 治具狀態確認；告警=立即暫停機臺 & PE進行RCA；嚴重=停線處理 & 啟動8D/CAPA\n"
            f"- 主要失敗原因: {fails_display}\n\n"
            f"請重點分析：\n"
            f"1. 良率是否達標（依良率告警等級說明）。\n"
            f"2. 重測率狀況及依等級給予對應應對建議。\n"
            f"3. 針對失敗原因給予簡短的後續追蹤建議。\n"
            f"請使用 Markdown 格式，適度使用列表與重點標示。不要輸出JSON。"
        )

    return [
        {"role": "system", "content": system_msg},
        {"role": "user",   "content": prompt},
    ]
