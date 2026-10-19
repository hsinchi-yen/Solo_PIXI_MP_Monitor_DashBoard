"""
Solo PIXI Log Splitter — server-side core used by the web Log Uploader.

Splits a station's accumulated Log_All.txt into one record per test, exactly like
the desktop log_splitter_app.py (same segment boundaries and the same
`seg.rstrip() + "\\n\\n"` body), so file_hash values match records uploaded with
the desktop tools and duplicates are skipped.
"""
import hashlib
import re
from collections import Counter

# ─── Splitting (identical to log_splitter_app.py) ────────────────────────────
SEGMENT_PATTERN = re.compile(r'(?=^MAC1:\s*)', re.MULTILINE)
START_PATTERN = re.compile(r'(\d{4})/(\d{2})/(\d{2}).*?(\d{2}):(\d{2}):(\d{2})')

# Mirrors module_log_parser.FILENAME_RE — records whose generated name does not
# match are skipped, as the desktop uploader never picks them up.
SPLIT_FILENAME_RE = re.compile(
    r'^([A-Za-z0-9]+-[A-Za-z0-9]{8,})_(\d{8})_(\d{6})_([0-9A-Fa-f]+)_([0-9A-Fa-f]+)_(PASS|FAIL|STOP)\.txt$'
)

# ─── Work order ──────────────────────────────────────────────────────────────
WO_FORMAT_RE = re.compile(r'^\d{4}-\d{8,}$')
WO_SEARCH_RE = re.compile(r'(?<!\d)(\d{4}-\d{8,})')
# Placeholder used while analysing a zip before the user has entered the work
# order; it only affects filenames, never content hashes.
ANALYSIS_WO = '0000-00000000'

LOG_ALL_NAME = 'log_all.txt'


def split_log_content(content):
    normalized_content = content.replace('\r\n', '\n').replace('\r', '\n')
    return [segment for segment in SEGMENT_PATTERN.split(normalized_content) if segment.strip().startswith("MAC1:")]


def parse_segment(segment):
    mac1 = mac2 = date_str = time_str = result = None

    for line in segment.strip().split('\n'):
        line_clean = line.strip()
        if line_clean.startswith("MAC1:"):
            mac1 = line_clean.split("\t")[-1].strip()
        elif line_clean.startswith("MAC2:"):
            mac2 = line_clean.split("\t")[-1].strip()
        elif line_clean.startswith("Start:"):
            start_match = START_PATTERN.search(line_clean)
            if start_match:
                date_str = ''.join(start_match.group(1, 2, 3))
                time_str = ''.join(start_match.group(4, 5, 6))

        if "**** P A S S ****" in line:
            result = "PASS"
        elif "**** F A I L ****" in line:
            result = "FAIL"
        elif "**** S T O P ****" in line:
            result = "STOP"

    return {
        "mac1": mac1,
        "mac2": mac2,
        "date": date_str,
        "time": time_str,
        "result": result,
    }


def is_valid_work_order(work_order):
    return bool(work_order) and bool(WO_FORMAT_RE.match(work_order))


def infer_work_order(*names):
    """Return the first work order found in the given names (zip filename,
    folder names). Handles prefixes such as 'log5101-260630007'."""
    for name in names:
        if not name:
            continue
        for part in re.split(r'[\\/]', name):
            match = WO_SEARCH_RE.search(part)
            if match:
                return match.group(1)
    return None


def _missing_fields(parsed):
    labels = [('mac1', 'MAC1'), ('mac2', 'MAC2'), ('date', 'Start'), ('result', '測試結果')]
    return [label for key, label in labels if not parsed[key]]


def split_to_records(content, work_order):
    """Split one Log_All.txt into upload-ready records.

    Returns (records, invalid_items):
      records       — [{filename, content, result, mac1, start}]
      invalid_items — [{mac1, mac2, start, result, reason}] for segments that the
                      desktop tools would also never upload.
    """
    records, invalid_items = [], []
    for seg in split_log_content(content):
        parsed = parse_segment(seg)
        start = f"{parsed['date']}_{parsed['time']}" if parsed['date'] and parsed['time'] else None
        missing = _missing_fields(parsed)
        if missing:
            invalid_items.append({**_brief(parsed, start), 'reason': '缺少欄位：' + '、'.join(missing)})
            continue
        filename = f"{work_order}_{parsed['date']}_{parsed['time']}_{parsed['mac1']}_{parsed['mac2']}_{parsed['result']}.txt"
        if not SPLIT_FILENAME_RE.match(filename):
            invalid_items.append({**_brief(parsed, start), 'reason': 'MAC 格式無效（非十六進位）'})
            continue
        records.append({
            'filename': filename,
            'content': seg.rstrip() + "\n\n",
            'result': parsed['result'],
            'mac1': parsed['mac1'].upper(),
            'start': start,
        })
    return records, invalid_items


def _brief(parsed, start):
    return {'mac1': parsed['mac1'], 'mac2': parsed['mac2'], 'start': start, 'result': parsed['result']}


def content_hash(content):
    """Same digest module_log_parser stores as file_hash."""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


# ─── Zip handling ────────────────────────────────────────────────────────────
def find_log_all_entries(zf):
    """Return [(station, ZipInfo)] for every Log_All.txt (case-insensitive) that
    sits below a purely numeric station folder, ordered by station."""
    entries = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        parts = info.filename.replace('\\', '/').split('/')
        if parts[-1].lower() != LOG_ALL_NAME:
            continue
        station = next((p for p in parts[:-1] if p.isdigit()), None)
        if station is not None:
            entries.append((station, info))
    entries.sort(key=lambda e: (int(e[0]), e[1].filename))
    return entries


def top_level_names(zf):
    return sorted({info.filename.replace('\\', '/').split('/')[0] for info in zf.infolist()})


def read_log_all(zf, info):
    return zf.read(info).decode('utf-8', errors='replace')


def iter_station_records(zf, entries, work_order):
    """Yield (station, records, invalid_items) one station at a time so only a
    single Log_All.txt is held in memory."""
    for station, info in entries:
        records, invalid_items = split_to_records(read_log_all(zf, info), work_order)
        yield station, records, invalid_items


def analyze_zip(zf, zip_name):
    """Collect the facts shown to the user before they enter the work order.

    Returns (facts, hashes). Hashes do not depend on the work order, so the
    caller can look up which of them already exist in the database.
    """
    entries = find_log_all_entries(zf)
    stations, invalid_items, hashes = [], [], []
    mac1s = set()
    starts = []
    totals = Counter()
    for station, records, invalid in iter_station_records(zf, entries, ANALYSIS_WO):
        counts = Counter(r['result'] for r in records)
        stations.append({
            'station': station,
            'records': len(records),
            'pass': counts.get('PASS', 0),
            'fail': counts.get('FAIL', 0),
            'stop': counts.get('STOP', 0),
            'invalid': len(invalid),
        })
        totals.update(counts)
        invalid_items.extend({**item, 'station': station} for item in invalid)
        for r in records:
            hashes.append(content_hash(r['content']))
            mac1s.add(r['mac1'])
            starts.append(r['start'])
    facts = {
        'zip_name': zip_name,
        'inferred_wo': infer_work_order(zip_name, *top_level_names(zf)),
        'stations': stations,
        'total_records': len(hashes),
        'pass': totals.get('PASS', 0),
        'fail': totals.get('FAIL', 0),
        'stop': totals.get('STOP', 0),
        'invalid': len(invalid_items),
        'invalid_items': invalid_items,
        'unique_mac1': len(mac1s),
        'first_test': _fmt_start(min(starts)) if starts else None,
        'last_test': _fmt_start(max(starts)) if starts else None,
    }
    return facts, hashes


def _fmt_start(start):
    d, t = start.split('_')
    return f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}:{t[4:]}"


# ─── Validation rules (shared by form mode and the AI assistant) ─────────────
def validate_upload(facts, work_order, planned_qty, existing_by_wo=None, current_target=None):
    """Return [{code, level, message}] with level 'error' | 'warn' | 'info'.

    error — must be fixed before upload; warn — user must acknowledge;
    info  — shown only.
    """
    issues = []
    existing_by_wo = existing_by_wo or {}
    work_order = (work_order or '').strip()

    if not work_order:
        issues.append(_issue('R1', 'error', '請輸入工單號。'))
    elif not is_valid_work_order(work_order):
        issues.append(_issue('R1', 'error', f'工單號「{work_order}」格式不正確，應為 4 碼-8 碼以上數字，例如 5101-260630005。'))

    qty = parse_quantity(planned_qty)
    if planned_qty in (None, ''):
        issues.append(_issue('R2', 'error', '請輸入工單計畫生產數。'))
    elif qty is None or qty <= 0:
        issues.append(_issue('R2', 'error', f'計畫生產數「{planned_qty}」必須是正整數。'))

    wo_ok = is_valid_work_order(work_order)
    inferred = facts.get('inferred_wo')
    if wo_ok and inferred and inferred != work_order:
        issues.append(_issue('R3', 'warn', f'輸入的工單號 {work_order} 與壓縮檔推斷的 {inferred} 不一致。'))

    other = {wo: n for wo, n in existing_by_wo.items() if wo != work_order}
    if wo_ok and other:
        detail = '、'.join(f'{wo or "(無工單)"} {n:,} 筆' for wo, n in sorted(other.items(), key=lambda kv: str(kv[0])))
        issues.append(_issue('R4', 'warn', f'有資料已存在於其他工單（{detail}），這些資料將略過，不會改掛到 {work_order}。'))

    if qty and qty > 0:
        unique = facts.get('unique_mac1', 0)
        diff = qty - unique
        issues.append(_issue('R5', 'info', f'計畫 {qty:,} 台，log 實測不重複 MAC1 {unique:,} 台，差 {diff:+,} 台（僅供參考）。'))

    same = existing_by_wo.get(work_order, 0) if wo_ok else 0
    if same:
        issues.append(_issue('R6', 'info', f'{same:,} 筆已存在於工單 {work_order}，將略過（視同重傳）。'))

    if wo_ok and qty and qty > 0:
        if current_target is None:
            issues.append(_issue('R7', 'info', f'上傳成功後，Data Alignment 目標數將設為 {qty:,}。'))
        elif int(current_target) != qty:
            issues.append(_issue('R7', 'info', f'Data Alignment 目標數將由 {int(current_target):,} 更新為 {qty:,}。'))

    if facts.get('invalid'):
        issues.append(_issue('R8', 'info', f'{facts["invalid"]} 筆無效段落將略過（見明細）。'))

    return issues


def has_errors(issues):
    return any(i['level'] == 'error' for i in issues)


def has_warnings(issues):
    return any(i['level'] == 'warn' for i in issues)


def _issue(code, level, message):
    return {'code': code, 'level': level, 'message': message}


def parse_quantity(value):
    """'4,000' / 4000 / ' 4000 ' -> 4000; anything else -> None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        text = str(value).replace(',', '').strip()
        return int(text) if re.fullmatch(r'-?\d+', text) else None
    except (TypeError, ValueError):
        return None
