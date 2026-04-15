"""
Solo PIXI Module Log Parser
Parses QCA9377 BT+WiFi production test log files and inserts into PostgreSQL.
"""
import re
import os
import hashlib
from datetime import datetime

# ─── Filename patterns ───────────────────────────────────────────────────────
# Canonical: {WO}_{YYYYMMDD}_{HHMMSS}_{MAC1}_{MAC2}_{RESULT}.txt
# Legacy:    {YYYYMMDD}_{HHMMSS}_{MAC1}_{MAC2}_{RESULT}.txt
FILENAME_RE = re.compile(
    r'^([A-Za-z0-9]+-[A-Za-z0-9]{8,})_(\d{8})_(\d{6})_([0-9A-Fa-f]+)_([0-9A-Fa-f]+)_(PASS|FAIL|STOP)\.txt$'
)
LEGACY_FILENAME_RE = re.compile(
    r'^(\d{8})_(\d{6})_([0-9A-Fa-f]+)_([0-9A-Fa-f]+)_(PASS|FAIL|STOP)\.txt$'
)

# ─── Step header ─────────────────────────────────────────────────────────────
STEP_RE = re.compile(r'^(\d+)\.\s+(\S+)')

# ─── Metric line:  Label  Value Unit  (Hi ~ Lo)  <-- pass/fail ──────────────
METRIC_RE = re.compile(
    r'^\s+(.+?)\s{2,}(-?[\d.]+)\s+(\S+)\s+\((-?[\d.]+)\s*~\s*(-?[\d.]+)\)\s+<--\s*(pass|fail)',
    re.IGNORECASE
)

# ─── WiFi TX/RX header ──────────────────────────────────────────────────────
WIFI_HDR_RE = re.compile(
    r'Frequency:\s*(\d+).*?Data Rate:\s*(\S+).*?Bandwidth:\s*(\S+)',
    re.IGNORECASE
)

# ─── Xtal calibration ───────────────────────────────────────────────────────
XTAL_RE = re.compile(r'Xtal_cap:(\d+)\s+Xtal_freqerrppm:(-?[\d.]+)')

# ─── Fail line ───────────────────────────────────────────────────────────────
FAIL_RE = re.compile(r'(?:DUT\s+)?failed\s+at\s+(\S+?)!?', re.IGNORECASE)

# ─── Result line ─────────────────────────────────────────────────────────────
RESULT_RE = re.compile(r'\*{4}\s*(P\s*A\s*S\s*S|F\s*A\s*I\s*L|S\s*T\s*O\s*P)\s*\*{4}')

# ─── BDR metric name mapping ────────────────────────────────────────────────
BDR_MAP = {
    'ini freq error':       'bdr_freq_error',
    'freq drift':           'bdr_freq_drift',
    'delta f2 max':         'bdr_delta_f2_max',
    'power':                'bdr_power',
    'freq delta f1 avg':    'bdr_delta_f1_avg',
    'delta_f2_f1_av_ratio': 'bdr_delta_f2_f1_ratio',
}

EDR_MAP = {
    'devm avg':     'devm_avg',
    'devm peakg':   'devm_peak',
    'power diff':   'power_diff',
    'edr omega i':  'omega_i',
    'edr omega 0':  'omega_0',
    'edr omega i0': 'omega_i0',
    'devm 99pct':   'devm_99pct',
    'power':        'power',
}

LE_MAP = {
    'ini freq error':    'le_freq_error',
    'delta f2 avg':      'le_delta_f2_avg',
    'delta f2 max':      'le_delta_f2_max',
    'delta f0 fn max':   'le_delta_f0_fn_max',
    'delta f1 f0':       'le_delta_f1_f0',
    'delta fn fn5 max':  'le_delta_fn_fn5_max',
    'power':             'le_power',
    'delta f1 avg':      'le_delta_f1_avg',
    'ratio of f2 to f1': 'le_f2_f1_ratio',
}


def parse_filename(filename):
    """Extract metadata from filename."""
    base = os.path.basename(filename)
    m = FILENAME_RE.match(base)
    if m:
        unit_date = datetime.strptime(m.group(2), '%Y%m%d').date()
        return {
            'work_order': m.group(1),
            'unit_date':  unit_date,
            'date_str':   m.group(2),
            'time_str':   m.group(3),
            'mac1':       m.group(4).upper(),
            'mac2':       m.group(5).upper(),
            'result':     m.group(6),
        }

    m = LEGACY_FILENAME_RE.match(base)
    if not m:
        return None

    unit_date = datetime.strptime(m.group(1), '%Y%m%d').date()
    return {
        'work_order': None,
        'unit_date':  unit_date,
        'date_str':   m.group(1),
        'time_str':   m.group(2),
        'mac1':       m.group(3).upper(),
        'mac2':       m.group(4).upper(),
        'result':     m.group(5),
    }


def _classify_wifi_rate(freq, data_rate, bandwidth):
    """Classify a WiFi TX/RX step into a rate category key."""
    freq = int(freq)
    dr = data_rate.upper()
    bw = bandwidth.upper()

    if freq <= 3000:  # 2.4 GHz
        if 'CCK' in dr:
            return 'wifi24_cck11'
        elif 'OFDM' in dr:
            return 'wifi24_ofdm54'
        elif 'MCS7' in dr or 'MCS-7' in dr:
            if 'BW-40' in bw:
                return 'wifi24_ht40'
            return 'wifi24_ht20'
    else:  # 5 GHz
        if 'OFDM' in dr:
            return 'wifi5_ofdm54'
        elif 'MCS9' in dr or 'MCS-9' in dr:
            return 'wifi5_vht80'
        elif 'MCS7' in dr or 'MCS-7' in dr:
            if 'BW-40' in bw:
                return 'wifi5_ht40'
            return 'wifi5_ht20'
    return None


def parse_log_file(filepath):
    """Parse a single log file and return a record dict."""
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    lines = content.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    file_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()

    # Parse filename metadata
    fmeta = parse_filename(filepath)
    rec = {
        'work_order': fmeta['work_order'] if fmeta else None,
        'unit_date': fmeta['unit_date'] if fmeta else None,
        'mac1': None, 'mac2': None, 'tester_sn': None,
        'start_time': None, 'end_time': None, 'test_duration_sec': None,
        'result': fmeta['result'] if fmeta else 'UNKNOWN',
        'file_hash': file_hash,
        'source_file': os.path.basename(filepath),
        'raw_log': content,
    }

    # State
    current_step_name = None
    current_step_num = 0
    current_section = None  # 'bdr', 'edr1', 'edr2', 'le', 'ber1', 'ber2', 'ble_rx', 'wifi_cal', 'wifi_tx', 'wifi_rx'
    edr_index = 0  # to distinguish EDR1 vs EDR2

    # WiFi TX accumulators: rate_key -> {'evm': [list], 'power': [list], 'all_pass': bool}
    wifi_tx = {}
    # WiFi RX accumulators: 'wifi24' or 'wifi5' -> [per_values]
    wifi_rx_24 = []
    wifi_rx_5 = []
    wifi_rx_all_pass_24 = True
    wifi_rx_all_pass_5 = True
    wifi_tx_all_pass = True

    current_wifi_rate_key = None
    current_wifi_band = None  # for RX: '24' or '5'

    fail_step_num = None
    fail_step_name = None
    fail_message = None
    fail_error_code = None
    fail_category = None

    for line in lines:
        stripped = line.strip()

        # ── Header fields ──
        if stripped.startswith('MAC1:'):
            rec['mac1'] = stripped.split('\t')[-1].strip().upper()
            continue
        if stripped.startswith('MAC2:'):
            rec['mac2'] = stripped.split('\t')[-1].strip().upper()
            continue
        if stripped.startswith('Start:'):
            dt_raw = stripped.split('\t')[-1].strip()
            try:
                rec['start_time'] = datetime.strptime(dt_raw, '%Y/%m/%d %H:%M:%S')
                if rec['unit_date'] is None:
                    rec['unit_date'] = rec['start_time'].date()
            except ValueError:
                pass
            continue
        if stripped.startswith('End:'):
            dt_raw = stripped.split('\t')[-1].strip()
            try:
                rec['end_time'] = datetime.strptime(dt_raw, '%Y/%m/%d %H:%M:%S')
            except ValueError:
                pass
            continue
        if stripped.startswith('Test Time:'):
            tt = stripped.replace('Test Time:', '').strip()
            try:
                parts = tt.split(':')
                mins = float(parts[0])
                secs = float(parts[1])
                rec['test_duration_sec'] = mins * 60 + secs
            except (ValueError, IndexError):
                pass
            continue

        # ── Result detection ──
        rm = RESULT_RE.search(stripped)
        if rm:
            raw_result = rm.group(1).replace(' ', '')
            rec['result'] = raw_result
            continue

        # ── Fail detection ("DUT failed at X!") ──
        fm = FAIL_RE.search(stripped)
        if fm and fail_message is None:
            fail_message = stripped.strip()
            fail_step_name = current_step_name
            fail_step_num = current_step_num
            fail_error_code = f'ERR_{current_step_num}'
            fail_category = 'TestFail'
            continue

        # ── Step header ──
        sm = STEP_RE.match(stripped)
        if sm:
            current_step_num = int(sm.group(1))
            current_step_name = sm.group(2)

            # Classify section
            name_upper = current_step_name.upper()
            if name_upper == 'BT_TX_BDR':
                current_section = 'bdr'
            elif name_upper == 'BT_TX_EDR':
                edr_index += 1
                current_section = f'edr{edr_index}'
            elif name_upper == 'BT_TX_LE':
                current_section = 'le'
            elif name_upper == 'BT_RX_BER':
                current_section = 'ber'
            elif name_upper == 'BT_RX_LE':
                current_section = 'ble_rx'
            elif name_upper == 'WIFI_TX_CALIBRATION':
                current_section = 'wifi_cal'
            elif name_upper == 'WIFI_TX_VERIFY_ALL':
                current_section = 'wifi_tx'
                current_wifi_rate_key = None
            elif name_upper == 'WIFI_RX_VERIFY_PER':
                current_section = 'wifi_rx'
                current_wifi_rate_key = None
                current_wifi_band = None
            elif name_upper.startswith('ATC_'):
                current_section = 'atc'
            else:
                current_section = None
            continue

        # ── Tester serial number ──
        if 'Serial number:' in stripped:
            rec['tester_sn'] = stripped.split(':', 1)[1].strip()
            continue

        # ── Xtal calibration ──
        if current_section == 'wifi_cal':
            xm = XTAL_RE.search(stripped)
            if xm:
                rec['xtal_cap'] = int(xm.group(1))
                rec['xtal_freq_error_ppm'] = float(xm.group(2))
                rec['cal_pass'] = True
            if 'Calibration Done' in stripped:
                rec['cal_pass'] = True
            continue

        # ── WiFi TX/RX header line (Frequency / Data Rate / Bandwidth) ──
        if current_section in ('wifi_tx', 'wifi_rx'):
            wh = WIFI_HDR_RE.search(stripped)
            if wh:
                freq = int(wh.group(1))
                rate_key = _classify_wifi_rate(freq, wh.group(2), wh.group(3))
                if current_section == 'wifi_tx':
                    current_wifi_rate_key = rate_key
                    if rate_key and rate_key not in wifi_tx:
                        wifi_tx[rate_key] = {'evm': [], 'power': []}
                elif current_section == 'wifi_rx':
                    current_wifi_rate_key = rate_key
                    current_wifi_band = '24' if freq <= 3000 else '5'
                continue

        # ── BT RX BER/PER frequency ──
        if current_section == 'ber':
            if 'Frequency: 2441' in stripped:
                current_section = 'ber1'
            elif 'Frequency: 2480' in stripped:
                current_section = 'ber2'

        # ── Metric extraction ──
        mm = METRIC_RE.match(line)  # use original line (preserves leading tabs)
        if mm:
            label_raw = mm.group(1).strip().lower()
            value = float(mm.group(2))
            is_pass = mm.group(6).lower() == 'pass'

            # ── Capture first metric failure as fail source ──
            if not is_pass and fail_message is None:
                fail_step_num = current_step_num
                fail_step_name = current_step_name
                fail_message = f'{mm.group(1).strip()} fail'
                fail_error_code = f'ERR_{current_step_num}'
                fail_category = 'TestFail'

            if current_section == 'bdr':
                col = BDR_MAP.get(label_raw)
                if col:
                    rec[col] = value
                if not is_pass:
                    rec['bdr_pass'] = False
                elif rec.get('bdr_pass') is None:
                    rec['bdr_pass'] = True

            elif current_section in ('edr1', 'edr2'):
                prefix = current_section  # 'edr1' or 'edr2'
                col = EDR_MAP.get(label_raw)
                if col:
                    rec[f'{prefix}_{col}'] = value
                if not is_pass:
                    rec[f'{prefix}_pass'] = False
                elif rec.get(f'{prefix}_pass') is None:
                    rec[f'{prefix}_pass'] = True

            elif current_section == 'le':
                col = LE_MAP.get(label_raw)
                if col:
                    rec[col] = value
                if not is_pass:
                    rec['le_pass'] = False
                elif rec.get('le_pass') is None:
                    rec['le_pass'] = True

            elif current_section in ('ber1', 'ber2', 'ber'):
                if 'ber' in label_raw:
                    if current_section == 'ber1' or ('2441' in str(current_step_num)):
                        rec['ber_2441'] = value
                    else:
                        rec['ber_2480'] = value
                    if not is_pass:
                        rec['bt_rx_pass'] = False
                    elif rec.get('bt_rx_pass') is None:
                        rec['bt_rx_pass'] = True

            elif current_section == 'ble_rx':
                if 'per' in label_raw:
                    rec['per_le'] = value
                    if not is_pass:
                        rec['bt_rx_pass'] = False
                    elif rec.get('bt_rx_pass') is None:
                        rec['bt_rx_pass'] = True

            elif current_section == 'wifi_tx' and current_wifi_rate_key:
                if 'evm' in label_raw:
                    wifi_tx[current_wifi_rate_key]['evm'].append(value)
                elif label_raw == 'power':
                    wifi_tx[current_wifi_rate_key]['power'].append(value)
                if not is_pass:
                    wifi_tx_all_pass = False

            elif current_section == 'wifi_rx' and current_wifi_band:
                if 'per' in label_raw:
                    if current_wifi_band == '24':
                        wifi_rx_24.append(value)
                        if not is_pass:
                            wifi_rx_all_pass_24 = False
                    else:
                        wifi_rx_5.append(value)
                        if not is_pass:
                            wifi_rx_all_pass_5 = False

    # ── Aggregate WiFi TX metrics ──
    for rate_key, data in wifi_tx.items():
        if data['evm']:
            rec[f'{rate_key}_evm'] = max(data['evm'])  # worst = closest to 0
        if data['power']:
            rec[f'{rate_key}_power'] = round(sum(data['power']) / len(data['power']), 3)
    rec['wifi24_tx_pass'] = wifi_tx_all_pass if wifi_tx else None
    rec['wifi5_tx_pass'] = wifi_tx_all_pass if wifi_tx else None

    # ── Aggregate WiFi RX PER ──
    if wifi_rx_24:
        rec['wifi24_per_max'] = max(wifi_rx_24)
        rec['wifi24_rx_pass'] = wifi_rx_all_pass_24
    if wifi_rx_5:
        rec['wifi5_per_max'] = max(wifi_rx_5)
        rec['wifi5_rx_pass'] = wifi_rx_all_pass_5

    # ── Fail info ──
    if fail_message:
        rec['fail_step_num'] = fail_step_num
        rec['fail_step_name'] = fail_step_name
        rec['fail_message'] = fail_message
        rec['fail_error_code'] = fail_error_code
        rec['fail_category'] = fail_category
    elif rec['result'] in ('FAIL', 'STOP'):
        # No identified cause — classify as DeviceConfigure
        rec['fail_category'] = 'DeviceConfigure'

    return rec


# ─── DB columns (matching schema.sql) ───────────────────────────────────────
DB_COLUMNS = [
    'work_order', 'unit_date', 'mac1', 'mac2', 'tester_sn',
    'start_time', 'end_time', 'test_duration_sec', 'result',
    'bdr_freq_error', 'bdr_freq_drift', 'bdr_delta_f2_max', 'bdr_power',
    'bdr_delta_f1_avg', 'bdr_delta_f2_f1_ratio', 'bdr_pass',
    'edr1_devm_avg', 'edr1_devm_peak', 'edr1_power_diff',
    'edr1_omega_i', 'edr1_omega_0', 'edr1_omega_i0',
    'edr1_devm_99pct', 'edr1_power', 'edr1_pass',
    'edr2_devm_avg', 'edr2_devm_peak', 'edr2_power_diff',
    'edr2_omega_i', 'edr2_omega_0', 'edr2_omega_i0',
    'edr2_devm_99pct', 'edr2_power', 'edr2_pass',
    'le_freq_error', 'le_delta_f2_avg', 'le_delta_f2_max',
    'le_delta_f0_fn_max', 'le_delta_f1_f0', 'le_delta_fn_fn5_max',
    'le_power', 'le_delta_f1_avg', 'le_f2_f1_ratio', 'le_pass',
    'ber_2441', 'ber_2480', 'per_le', 'bt_rx_pass',
    'xtal_cap', 'xtal_freq_error_ppm', 'cal_pass',
    'wifi24_cck11_evm', 'wifi24_cck11_power',
    'wifi24_ofdm54_evm', 'wifi24_ofdm54_power',
    'wifi24_ht20_evm', 'wifi24_ht20_power',
    'wifi24_ht40_evm', 'wifi24_ht40_power', 'wifi24_tx_pass',
    'wifi5_ofdm54_evm', 'wifi5_ofdm54_power',
    'wifi5_ht20_evm', 'wifi5_ht20_power',
    'wifi5_ht40_evm', 'wifi5_ht40_power',
    'wifi5_vht80_evm', 'wifi5_vht80_power', 'wifi5_tx_pass',
    'wifi24_per_max', 'wifi24_rx_pass',
    'wifi5_per_max', 'wifi5_rx_pass',
    'fail_step_num', 'fail_step_name', 'fail_message',
    'fail_error_code', 'fail_category',
    'raw_log', 'file_hash', 'source_file',
]


def insert_record(conn, rec):
    """Insert a parsed record dict into the database. Returns True if inserted, False if duplicate."""
    cols = ', '.join(DB_COLUMNS)
    placeholders = ', '.join(['%s'] * len(DB_COLUMNS))
    values = [rec.get(c) for c in DB_COLUMNS]

    sql = f"INSERT INTO module_test ({cols}) VALUES ({placeholders}) ON CONFLICT (file_hash) DO NOTHING"
    cur = conn.cursor()
    cur.execute(sql, values)
    inserted = cur.rowcount > 0
    cur.close()
    return inserted


def scan_and_ingest(folder, conn, on_progress=None):
    """Scan a folder for log files and ingest them into the database.
    on_progress(filename, index, total, status) — callback for progress reporting.
    Returns dict with stats: total, uploaded, skipped, failed.
    """
    files = sorted([
        f for f in os.listdir(folder)
        if f.endswith('.txt') and f != 'summary.txt' and (FILENAME_RE.match(f) or LEGACY_FILENAME_RE.match(f))
    ])
    total = len(files)
    stats = {'total': total, 'uploaded': 0, 'skipped': 0, 'failed': 0}

    for i, fname in enumerate(files):
        fpath = os.path.join(folder, fname)
        try:
            rec = parse_log_file(fpath)
            inserted = insert_record(conn, rec)
            if inserted:
                stats['uploaded'] += 1
                status = 'uploaded'
            else:
                stats['skipped'] += 1
                status = 'skipped'
        except Exception as e:
            stats['failed'] += 1
            status = f'error: {e}'

        if (i + 1) % 10 == 0:
            conn.commit()

        if on_progress:
            on_progress(fname, i + 1, total, status)

    conn.commit()
    return stats


if __name__ == '__main__':
    import sys
    import argparse

    parser = argparse.ArgumentParser(description='Ingest Solo PIXI module test logs into PostgreSQL')
    parser.add_argument('path', help='Path to log folder')
    parser.add_argument('--dsn', default='postgresql://pixi:pixipass@localhost:5433/pixi_test',
                        help='PostgreSQL DSN')
    parser.add_argument('--dry-run', action='store_true', help='Parse only, do not write to DB')
    args = parser.parse_args()

    if args.dry_run:
        files = [f for f in os.listdir(args.path)
                 if f.endswith('.txt') and f != 'summary.txt' and (FILENAME_RE.match(f) or LEGACY_FILENAME_RE.match(f))]
        for fname in sorted(files)[:3]:
            rec = parse_log_file(os.path.join(args.path, fname))
            print(f"\n{'='*60}")
            print(f"File: {fname}")
            print(f"  work_order={rec.get('work_order')}  result={rec['result']}  mac1={rec['mac1']}  unit_date={rec['unit_date']}")
            print(f"  start={rec['start_time']}  end={rec['end_time']}  dur={rec['test_duration_sec']}s")
            print(f"  BDR: power={rec.get('bdr_power')}  pass={rec.get('bdr_pass')}")
            print(f"  EDR1: devm={rec.get('edr1_devm_avg')}  pass={rec.get('edr1_pass')}")
            print(f"  EDR2: devm={rec.get('edr2_devm_avg')}  pass={rec.get('edr2_pass')}")
            print(f"  LE: power={rec.get('le_power')}  pass={rec.get('le_pass')}")
            print(f"  BER: 2441={rec.get('ber_2441')} 2480={rec.get('ber_2480')}  pass={rec.get('bt_rx_pass')}")
            print(f"  Xtal: cap={rec.get('xtal_cap')} ppm={rec.get('xtal_freq_error_ppm')}")
            print(f"  WiFi24 CCK11: evm={rec.get('wifi24_cck11_evm')} pwr={rec.get('wifi24_cck11_power')}")
            print(f"  WiFi5 VHT80: evm={rec.get('wifi5_vht80_evm')} pwr={rec.get('wifi5_vht80_power')}")
            print(f"  WiFi24 PER max={rec.get('wifi24_per_max')}  WiFi5 PER max={rec.get('wifi5_per_max')}")
            if rec.get('fail_message'):
                print(f"  FAIL: step={rec.get('fail_step_name')} msg={rec.get('fail_message')}")
        print(f"\nTotal files: {len(files)} (showing first 3)")
    else:
        import psycopg2
        conn = psycopg2.connect(args.dsn)
        def progress(fname, idx, total, status):
            print(f"  [{idx}/{total}] {fname} -> {status}")
        stats = scan_and_ingest(args.path, conn, on_progress=progress)
        conn.close()
        print(f"\nDone! {stats}")
