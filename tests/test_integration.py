"""
Integration tests against a running stack (default http://localhost:8001).

    PIXI_BASE_URL=http://localhost:8001 python tests/test_integration.py

Needs: the stack up (docker compose), admin credentials (TWEAK_USER / TWEAK_PASS, default
pixi / pixipass), and `docker` on PATH for direct DB checks (container PIXI_DB_CONTAINER,
default pixi-test-db; for a stack on another host set PIXI_DB_SSH, e.g. "ssh sit@10.20.31.40"). Upload tests use rawlogs/*.zip and are skipped without them; AI tests
are skipped when the LLM is offline.

The tests create and then remove their own data: a temporary uploader account, uploads of
the two sample work orders (reverted at the end), and AI reports for one work order.
"""
import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
import uuid

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

BASE = os.environ.get('PIXI_BASE_URL', 'http://localhost:8001').rstrip('/')
ADMIN_USER = os.environ.get('TWEAK_USER', 'pixi')
ADMIN_PASS = os.environ.get('TWEAK_PASS', 'pixipass')
DB_CONTAINER = os.environ.get('PIXI_DB_CONTAINER', 'pixi-test-db')
DB_SSH = os.environ.get('PIXI_DB_SSH', '')   # e.g. "ssh sit@10.20.31.40": DB checks run on that host
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP_005 = os.path.join(ROOT, 'rawlogs', '5101-260630005.zip')
ZIP_007 = os.path.join(ROOT, 'rawlogs', 'log5101-260630007.zip')
WO_005, WO_007 = '5101-260630005', '5101-260630007'
TEST_USER, TEST_PASS = 'it-uploader', 'it-pass-123'


# ─── HTTP helpers ────────────────────────────────────────────────────────────

def call(method, path, token=None, body=None, raw=False, timeout=300):
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    if token:
        headers['X-Tweak-Token'] = token
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        status, payload, hdrs = resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        status, payload, hdrs = e.code, e.read(), e.headers
    ms = (time.perf_counter() - t0) * 1000
    if raw:
        return status, payload, hdrs, ms
    try:
        return status, json.loads(payload or b'{}')
    except json.JSONDecodeError:
        return status, {'_raw': payload.decode('utf-8', 'replace')[:200]}


def ndjson(path, token=None, body=None):
    status, payload, _, _ = call('POST', path, token, body, raw=True)
    text = payload.decode()
    if status != 200:
        return status, [json.loads(text)] if text.startswith('{') else []
    return status, [json.loads(line) for line in text.splitlines() if line.strip()]


def upload_zip(path, token):
    boundary = uuid.uuid4().hex
    with open(path, 'rb') as f:
        content = f.read()
    name = os.path.basename(path)
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f'Content-Type: application/zip\r\n\r\n').encode() + content + f'\r\n--{boundary}--\r\n'.encode()
    headers = {'Content-Type': f'multipart/form-data; boundary={boundary}'}
    if token:
        headers['X-Tweak-Token'] = token
    req = urllib.request.Request(BASE + '/api/log-upload/analyze', data=body, method='POST', headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=300)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def login(user, pw):
    return call('POST', '/api/auth/login', body={'username': user, 'password': pw})


def psql(sql):
    cmd = ['docker', 'exec', DB_CONTAINER, 'psql', '-U', 'pixi', '-d', 'pixi_test', '-At', '-c', sql]
    if DB_SSH:   # stack on another host: run the same command there
        cmd = shlex.split(DB_SSH) + [shlex.join(cmd)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def llm_online():
    return call('GET', '/api/llm-status')[1].get('connected', False)


class Base(unittest.TestCase):
    admin = None

    @classmethod
    def setUpClass(cls):
        st, body = login(ADMIN_USER, ADMIN_PASS)
        assert st == 200, f'admin login failed: {body}'
        Base.admin = body['token']

    def refresh_stats(self):
        st, body = call('POST', '/api/stats/refresh')
        if st == 429:
            time.sleep(10)
            st, body = call('POST', '/api/stats/refresh')
        self.assertEqual(st, 200, body)


# ═══ System, schema, filters, bugs ═══════════════════════════════════════════

class T1System(Base):

    def test_health_reports_db_llm_version(self):
        st, h = call('GET', '/health')
        self.assertEqual(st, 200)
        self.assertEqual(h['status'], 'ok')
        for k in ('version', 'db_ms', 'stats_refreshed_at', 'llm'):
            self.assertIn(k, h)

    def test_module_test_columns_unchanged(self):
        # Rule: existing tables keep their columns (desktop uploader + old data). Only the
        # four nullable columns earlier releases added may exist beyond schema.sql.
        with open(os.path.join(ROOT, 'solo-pixi-essential', 'schema.sql'), encoding='utf-8') as f:
            schema = f.read()
        block = schema[schema.index('CREATE TABLE IF NOT EXISTS module_test'):]
        block = block[:block.index(');')]
        expected = {line.split()[0] for line in block.splitlines()[1:]
                    if line.strip() and not line.strip().startswith('--') and line.split()[0].islower()}
        actual = set(psql("SELECT string_agg(column_name, ',') FROM information_schema.columns "
                          "WHERE table_schema = current_schema() AND table_name = 'module_test'").split(','))
        self.assertEqual(actual - expected, {'fail_error_code', 'fail_category', 'upload_id'} - expected)
        self.assertEqual(expected - actual, set())

    def test_new_objects_are_separate(self):
        objs = psql("SELECT string_agg(c.relname, ',') FROM pg_class c WHERE c.relname IN "
                    "('mv_unit_day','mv_unit','ai_reports','job_runs','app_meta','idx_mt_eff_date')").split(',')
        self.assertEqual(set(objs), {'mv_unit_day', 'mv_unit', 'ai_reports', 'job_runs', 'app_meta', 'idx_mt_eff_date'})

    def test_error_shape_and_no_exception_text(self):
        st, body = call('GET', '/api/summary?date_from=nope')
        self.assertEqual(st, 400)
        self.assertEqual(set(body), {'error', 'code', 'error_en'})

    def test_b8_bad_requests_do_not_leak_connections(self):
        before = int(psql("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = 'pixi_test'"))
        for _ in range(60):
            call('GET', '/api/fails?day=2026-02-30')
            call('GET', '/api/summary?year=2026&week=99')
        after = int(psql("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = 'pixi_test'"))
        self.assertLessEqual(after, before + 4, (before, after))
        self.assertEqual(call('GET', '/health')[0], 200)

    def test_b3_iso_week(self):
        st, a = call('GET', '/api/summary?year=2026&week=34')
        st2, b = call('GET', '/api/summary?date_from=2026-08-17&date_to=2026-08-23')
        self.assertEqual((st, st2), (200, 200))
        self.assertEqual(a['total'], b['total'])

    def test_legacy_params_equal_ranges(self):
        for legacy, rng in (('year=2026&month=8', 'date_from=2026-08-01&date_to=2026-08-31'),
                            ('day=2026-08-21', 'date_from=2026-08-21&date_to=2026-08-21')):
            for ep in ('/api/summary', '/api/pass-fail-split', '/api/work-order-summary'):
                self.assertEqual(call('GET', f'{ep}?{legacy}')[1], call('GET', f'{ep}?{rng}')[1], (ep, legacy))

    def test_internal_consistency(self):
        s = call('GET', '/api/summary')[1]
        self.assertEqual(s['pass'] + s['fail'] + s['stop'], s['total'])
        split = {r['result']: r['count'] for r in call('GET', '/api/pass-fail-split')[1]}
        self.assertEqual(sum(split.values()), s['total'])
        wo_total = sum(r['total'] for r in call('GET', '/api/work-order-summary')[1])
        self.assertEqual(wo_total, s['total'])
        ov = call('GET', '/api/overview')[1]
        self.assertEqual(ov['summary']['total'], s['total'])

    def test_first_pass_yield(self):
        # First pass = the unit's first attempt (start_time, then id) is PASS — the IW61x rule (Q70).
        wo = call('GET', '/api/filter-options')[1]['work_orders'][0]['work_order']
        expected = psql("SELECT COUNT(*) FILTER (WHERE r = 'PASS') || '/' || COUNT(*) FROM ("
                        "SELECT (ARRAY_AGG(result ORDER BY start_time, id))[1] AS r FROM module_test "
                        f"WHERE work_order = '{wo}' GROUP BY mac1) u")
        for qs in (f'work_order={wo}', f'work_order={wo}&date_from=2000-01-01&date_to=2100-12-31'):
            s = call('GET', f'/api/summary?{qs}')[1]
            self.assertEqual(f"{s['first_pass']}/{s['total']}", expected, qs)
            self.assertLessEqual(s['first_pass'], s['pass'])
            tr = call('GET', f'/api/trend?{qs}')[1]
            self.assertEqual(sum(p['first_pass'] for p in tr['points']), s['first_pass'], qs)
            self.assertTrue(all('fpy_pct' in p for p in tr['points']))

    def test_hourly_output(self):
        # Q69: PASS / FAIL / STOP runs per hour, 07:00–19:00 of the work order's latest day with runs then.
        wo = call('GET', '/api/filter-options')[1]['work_orders'][0]['work_order']
        h = call('GET', f'/api/hourly-output?work_order={wo}')[1]
        self.assertEqual([p['hour'] for p in h['points']], list(range(7, 19)))
        day = psql(f"SELECT MAX(start_time::date) FROM module_test WHERE work_order = '{wo}' "
                   "AND start_time::time >= '07:00' AND start_time::time < '19:00'")
        self.assertEqual(h['date'], day)
        out = psql("SELECT EXTRACT(HOUR FROM start_time)::int, COUNT(*) FILTER (WHERE result = 'PASS'), "
                   "COUNT(*) FILTER (WHERE result = 'FAIL'), COUNT(*) FILTER (WHERE result = 'STOP') FROM module_test "
                   f"WHERE work_order = '{wo}' AND start_time >= '{day} 07:00' AND start_time < '{day} 19:00' GROUP BY 1")
        expected = {int(a): (int(b), int(c), int(d)) for a, b, c, d in (line.split('|') for line in out.splitlines())}
        got = {p['hour']: (p['pass'], p['fail'], p['stop']) for p in h['points'] if p['pass'] + p['fail'] + p['stop']}
        self.assertEqual(got, expected)
        outside = psql(f"SELECT COUNT(*) FROM module_test WHERE work_order = '{wo}' AND start_time::date = '{day}' "
                       "AND (start_time::time < '07:00' OR start_time::time >= '19:00')")
        self.assertEqual(h['outside'], int(outside))
        # No work order selected: the latest one; the dashboard gets the same in /api/overview.
        self.assertEqual(call('GET', '/api/hourly-output')[1], h)
        self.assertEqual(call('GET', '/api/overview')[1]['hourly'], h)
        self.assertEqual(call('GET', '/api/hourly-output?work_order=NOPE-1')[1]['points'], [])

    def test_b9_alignment_targets_need_login(self):
        self.assertEqual(call('POST', '/api/alignment-targets', body={'X-1': 5})[0], 401)

    def test_b1_meta_and_filter_options(self):
        st, opts = call('GET', '/api/filter-options')
        self.assertEqual(st, 200)
        self.assertTrue(opts['work_orders'])
        st, meta = call('GET', '/api/meta')
        self.assertEqual(meta['thresholds'], {'yield_good': 98.0, 'yield_warn': 95.0, 'retry_good': 3.0, 'retry_warn': 5.0})


class T2Performance(Base):
    """Plan stage-2 acceptance: 量測 < 0.5 s and < 50 KB; 儀表板 < 0.2 s (warm)."""

    def test_measurements_small_and_fast(self):
        call('GET', '/api/measurements')                      # warm the per-version cache
        st, payload, _, ms = call('GET', '/api/measurements', raw=True)
        self.assertEqual(st, 200)
        self.assertLess(len(payload), 50 * 1024)
        self.assertLess(ms, 500)

    def test_overview_fast(self):
        wo = call('GET', '/api/filter-options')[1]['work_orders'][0]['work_order']
        call('GET', f'/api/overview?work_order={wo}')
        times = [call('GET', f'/api/overview?work_order={wo}', raw=True)[3] for _ in range(5)]
        self.assertLess(min(times), 200, times)

    def test_gzip(self):
        req = urllib.request.Request(BASE + '/js/core/i18n.js', headers={'Accept-Encoding': 'gzip'})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.headers.get('Content-Encoding'), 'gzip')


# ═══ Accounts & permissions (A1–A11) ═════════════════════════════════════════

class T3Accounts(Base):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        psql(f"DELETE FROM app_sessions WHERE username = '{TEST_USER}'; DELETE FROM app_users WHERE username = '{TEST_USER}';")

    @classmethod
    def tearDownClass(cls):
        psql(f"DELETE FROM app_sessions WHERE username = '{TEST_USER}'; DELETE FROM app_users WHERE username = '{TEST_USER}';"
             f"DELETE FROM account_audit WHERE target = '{TEST_USER}' OR actor = '{TEST_USER}';"
             f"DELETE FROM auth_log WHERE username = '{TEST_USER}';")

    def test_a1_to_a11(self):
        A = self.admin
        old = base64.b64encode(f'{ADMIN_USER}:{ADMIN_PASS}'.encode()).decode()
        self.assertEqual(call('GET', '/api/tweak/records', old)[0], 401, 'A1 old base64 token rejected')
        self.assertEqual(login(ADMIN_USER, 'wrong')[0], 401)
        st, r = call('POST', '/api/admin/users', A, {'username': TEST_USER.upper(), 'display_name': '整合測試',
                                                      'department': '測試', 'email': 'it@example.com', 'password': TEST_PASS})
        self.assertEqual((st, r.get('username')), (200, TEST_USER), 'A2 create')
        for bad in ({'username': TEST_USER, 'display_name': 'x', 'password': '12345678'},
                    {'username': ADMIN_USER.upper(), 'display_name': 'x', 'password': '12345678'},
                    {'username': 'shorty', 'display_name': 'x', 'password': '1234567'},
                    {'username': 'a b', 'display_name': 'x', 'password': '12345678'}):
            self.assertEqual(call('POST', '/api/admin/users', A, bad)[0], 400, bad)
        st, u = login(TEST_USER, TEST_PASS)
        self.assertEqual((st, u['role']), (200, 'uploader'), 'A3 uploader login')
        U = u['token']
        for method, path in (('GET', '/api/tweak/records'), ('POST', '/api/reparse'), ('GET', '/api/admin/users'),
                             ('POST', '/api/reports/run-daily')):
            self.assertEqual(call(method, path, U)[0], 403, f'A4 {path}')
        self.assertEqual(call('POST', '/api/alignment-targets', U, {'IT-WO': None})[0], 200, 'uploader may save targets')
        # A5 / A6: an uploader can neither see nor undo someone else's batch (only analyzed: nothing is written).
        uid = upload_zip(ZIP_007, A)[1]['upload_id']
        try:
            psql(f"UPDATE upload_log SET status = 'reverted' WHERE id = '{uid}'")   # listed in history from now on
            self.assertEqual(call('GET', f'/api/log-upload/{uid}', U)[0], 403, 'A5 detail')
            self.assertEqual(call('DELETE', f'/api/log-upload/{uid}', U)[0], 403, 'A6 undo')
            ids = lambda tok: {h['id'] for h in call('GET', f'/api/log-upload/history?username={ADMIN_USER}', tok)[1]['items']}
            self.assertIn(uid, ids(A), 'administrator sees every batch')
            self.assertNotIn(uid, ids(U), 'A5 uploader history is their own only')
        finally:
            psql(f"DELETE FROM upload_log WHERE id = '{uid}'")
        st, r = call('PATCH', f'/api/admin/users/{TEST_USER}', A, {'enabled': False})
        self.assertEqual(r['enabled'], False)
        self.assertEqual(call('GET', '/api/auth/me', U)[0], 401, 'A7 disabled → session revoked')
        st, r = login(TEST_USER, TEST_PASS)
        self.assertEqual((st, r['code']), (403, 'disabled'))
        call('PATCH', f'/api/admin/users/{TEST_USER}', A, {'enabled': True})
        self.assertEqual(call('POST', f'/api/admin/users/{TEST_USER}/reset-password', A, {'password': 'reset-5678'})[0], 200)
        self.assertEqual(login(TEST_USER, TEST_PASS)[0], 401, 'A8 old password fails')
        U = login(TEST_USER, 'reset-5678')[1]['token']
        other = login(TEST_USER, 'reset-5678')[1]['token']
        self.assertEqual(call('POST', '/api/auth/change-password', U, {'old_password': 'nope', 'new_password': 'newpass-99'})[0], 400)
        self.assertEqual(call('POST', '/api/auth/change-password', U, {'old_password': 'reset-5678', 'new_password': 'newpass-99'})[0], 200)
        self.assertEqual(call('GET', '/api/auth/me', U)[0], 200, 'A9 current session kept')
        self.assertEqual(call('GET', '/api/auth/me', other)[0], 401, 'A9 other session revoked')
        digest = hashlib.sha256(U.encode()).hexdigest()
        psql(f"UPDATE app_sessions SET expires_at = NOW() - INTERVAL '1 second' WHERE token_hash = '{digest}'")
        self.assertEqual(call('GET', '/api/auth/me', U)[0], 401, 'A10 expired')
        actions = {r['action'] for r in call('GET', f'/api/admin/audit?username={TEST_USER}&limit=200', A)[1]['items']}
        self.assertTrue({'create', 'disable', 'enable', 'reset_password', 'change_password'} <= actions, actions)
        logs = call('GET', f'/api/admin/login-log?username={TEST_USER}&limit=200', A)[1]['items']
        self.assertTrue(any(not r['success'] and r['reason'] == 'disabled' for r in logs), 'A11 login log')


# ═══ Log Uploader + statistics refresh + AI auto report ══════════════════════

@unittest.skipUnless(os.path.isfile(ZIP_005) and os.path.isfile(ZIP_007), 'rawlogs sample zips not present')
class T4Upload(Base):

    def setUp(self):
        for wo in (WO_005, WO_007):
            if int(psql(f"SELECT COUNT(*) FROM module_test WHERE work_order = '{wo}'")):
                self.skipTest(f'{wo} already has data in this database')

    def test_upload_refresh_report_and_revert(self):
        A = self.admin
        st, a = upload_zip(ZIP_007, A)
        self.assertEqual((st, a['facts']['total_records']), (200, 2041))
        st, events = ndjson('/api/log-upload/commit', A, {'upload_id': a['upload_id'], 'work_order': WO_007,
                                                          'planned_qty': 2000, 'mode': 'form'})
        done = events[-1]
        self.assertEqual((done['type'], done['uploaded']), ('done', 2041))
        # B3 (stats view) refreshed right after the upload: the new work order is visible at once.
        s = call('GET', f'/api/summary?work_order={WO_007}')[1]
        self.assertEqual(s['total'], 1893)
        opts = [w['work_order'] for w in call('GET', '/api/filter-options')[1]['work_orders']]
        self.assertIn(WO_007, opts)
        al = {r['work_order']: r for r in call('GET', f'/api/alignment?work_order={WO_007}')[1]['items']}
        self.assertEqual(al[WO_007]['target_total'], 2000)
        # C3: the upload queued an AI report for this work order.
        queued = psql(f"SELECT COUNT(*) FROM ai_reports WHERE work_order = '{WO_007}' AND trigger = 'upload'")
        self.assertGreaterEqual(int(queued), 1)
        # Re-upload: everything skipped (file_hash dedup, desktop-compatible).
        st, b = upload_zip(ZIP_007, A)
        st, events = ndjson('/api/log-upload/commit', A, {'upload_id': b['upload_id'], 'work_order': WO_007,
                                                          'planned_qty': 2000, 'mode': 'form'})
        self.assertEqual((events[-1]['uploaded'], events[-1]['skipped']), (0, 2041))
        # Undo removes the rows and refreshes the view.
        st, r = call('DELETE', f"/api/log-upload/{a['upload_id']}", A)
        self.assertEqual(r['reverted'], 2041)
        self.assertEqual(call('GET', f'/api/summary?work_order={WO_007}')[1]['total'], 0)
        psql(f"DELETE FROM ai_reports WHERE work_order = '{WO_007}'")

    def test_concurrent_commit_is_409(self):
        import threading
        A = self.admin
        a = upload_zip(ZIP_005, A)[1]
        b = upload_zip(ZIP_007, A)[1]
        out = {}
        t = threading.Thread(target=lambda: out.setdefault('a', ndjson('/api/log-upload/commit', A, {
            'upload_id': a['upload_id'], 'work_order': WO_005, 'planned_qty': 4000})))
        t.start()
        time.sleep(1.5)
        out['b'] = ndjson('/api/log-upload/commit', A, {'upload_id': b['upload_id'], 'work_order': WO_007, 'planned_qty': 2000})
        t.join()
        self.assertEqual(out['b'][0], 409)
        self.assertEqual(out['a'][1][-1]['type'], 'done')
        call('DELETE', f"/api/log-upload/{a['upload_id']}", A)
        psql(f"DELETE FROM ai_reports WHERE work_order = '{WO_005}'")


# ═══ AI reports (C1–C4) ══════════════════════════════════════════════════════

class T5Reports(Base):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not llm_online():
            raise unittest.SkipTest('LLM offline')
        cls.wo = call('GET', '/api/filter-options')[1]['work_orders'][0]['work_order']

    def test_generate_stream_latest_markdown_compare_ask(self):
        st, events = ndjson(f'/api/reports/{self.wo}/generate?force=true')
        types = [e['type'] for e in events if e['type'] != 'heartbeat']
        self.assertEqual(types[0], 'facts', 'numbers arrive first')
        self.assertEqual(types[-1], 'done')
        reports = {e['lang']: e for e in events if e['type'] == 'report'}
        self.assertEqual(set(reports), {'zh', 'en'})
        for lang, ev in reports.items():
            self.assertEqual(len(ev['report']['findings']), 3, lang)
            self.assertEqual(len(ev['report']['recommendations']), 3, lang)
            self.assertIn('ok', ev['check'])
        latest = call('GET', f'/api/reports/{self.wo}')[1]
        self.assertFalse(latest['stale'])
        self.assertEqual(latest['report']['status'], 'done')
        st, md, hdrs, _ = call('GET', f'/api/reports/{self.wo}/markdown?lang=zh', raw=True)
        self.assertEqual(st, 200)
        self.assertIn('attachment', hdrs['Content-Disposition'])
        self.assertIn(f'# 工單品質報告 — {self.wo}', md.decode())
        call('POST', f'/api/reports/{self.wo}/generate?force=true', raw=True)
        cmp_ = call('GET', f'/api/reports/{self.wo}/compare')[1]
        self.assertEqual(cmp_['changes'], [], 'same data → no differences')
        st, events = ndjson(f'/api/reports/{self.wo}/ask', body={'messages': [{'role': 'user', 'content': '良率是多少？'}]})
        self.assertEqual(events[-1]['type'], 'done')
        self.assertTrue(events[-1]['reply'])

    def test_unchanged_data_reuses_report(self):
        st, events = ndjson(f'/api/reports/{self.wo}/generate?force=false')
        self.assertIn('cached', [e['type'] for e in events])

    def test_daily_batch_queue(self):
        st, r = call('POST', '/api/reports/run-daily', self.admin)
        self.assertEqual(st, 200)
        self.assertIn('queued', r)


# ═══ English / Chinese (bilingual_ui_plan.md) ═════════════════════════════════

CJK = re.compile(r'[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]')


class T8Language(Base):
    """Every message the UI shows from the server exists in English; the AI answers in the UI language."""

    def assertEnglish(self, text):
        self.assertTrue(text)
        self.assertIsNone(CJK.search(text), text)

    def test_errors_carry_english(self):
        cases = [
            ('GET', '/api/summary?date_from=nope', None, None, 400),
            ('GET', '/api/summary?date_from=2026-10-10&date_to=2026-10-01', None, None, 400),
            ('POST', '/api/auth/login', None, {'username': ADMIN_USER, 'password': 'wrong'}, 401),
            ('GET', '/api/admin/users', None, None, 401),
            ('GET', '/api/reports/5101-999999999/markdown?lang=en', None, None, 404),
            ('POST', '/api/alignment-targets', self.admin, {'5101-260630006': 0}, 400),
            ('DELETE', '/api/tweak/delete-scope', self.admin, None, 400),
        ]
        for method, path, token, body, status in cases:
            st, resp = call(method, path, token, body)
            self.assertEqual(st, status, (path, resp))
            self.assertTrue(CJK.search(resp['error']), resp)
            self.assertEnglish(resp['error_en'])

    @unittest.skipUnless(os.path.isfile(ZIP_007), 'rawlogs/log5101-260630007.zip missing')
    def test_upload_checks_and_chat_in_english(self):
        st, a = upload_zip(ZIP_007, self.admin)
        self.assertEqual(st, 200, a)
        try:
            self.assertIn('Read 4 stations', a['chat_opening_en'])
            self.assertEnglish(a['chat_opening_en'])
            st, v = call('POST', '/api/log-upload/validate', self.admin,
                         {'upload_id': a['upload_id'], 'work_order': '5101-260630099', 'planned_qty': '2000'})
            self.assertEqual({i['code'] for i in v['issues']} >= {'R3', 'R5'}, True, v)
            for issue in v['issues']:
                self.assertEnglish(issue['message_en'])
            for item in a['facts']['invalid_items']:
                self.assertEnglish(item['reason_en'])
            if not llm_online():
                return
            st, events = ndjson('/api/log-upload/chat', self.admin, {
                'upload_id': a['upload_id'], 'lang': 'en',
                'messages': [{'role': 'assistant', 'content': a['chat_opening_en']},
                             {'role': 'user', 'content': 'Work order 5101-260630007, 2000 units'}]})
            done = [e for e in events if e['type'] == 'done']
            self.assertTrue(done, events)
            self.assertEnglish(done[0]['reply'])
        finally:
            psql(f"DELETE FROM upload_log WHERE id = '{a['upload_id']}'")

    def test_report_follow_up_in_english(self):
        if not llm_online():
            self.skipTest('LLM offline')
        wo = call('GET', '/api/filter-options')[1]['work_orders'][0]['work_order']
        if call('GET', f'/api/reports/{wo}')[1].get('report') is None:
            self.skipTest('no report for the latest work order')
        st, events = ndjson(f'/api/reports/{wo}/ask', body={'lang': 'en', 'messages': [
            {'role': 'user', 'content': 'What is the yield?'}]})
        self.assertEqual(events[-1]['type'], 'done', events[-3:])
        self.assertEnglish(events[-1]['reply'])


# ═══ Desktop uploader path (direct DB writes) ════════════════════════════════

class T7DesktopPath(Base):
    """Rows written straight into module_test (as log_uploader_app.py does) appear after a refresh."""

    WO = 'IT-DESKTOP-0001'

    def tearDown(self):
        psql(f"DELETE FROM module_test WHERE work_order = '{self.WO}'")
        self.refresh_stats()

    def test_direct_insert_visible_after_refresh(self):
        psql(f"""INSERT INTO module_test (work_order, unit_date, mac1, mac2, start_time, end_time, test_duration_sec, result, file_hash, source_file)
                 SELECT '{self.WO}', unit_date, mac1 || '-IT', mac2, start_time, end_time, test_duration_sec, result,
                        md5(file_hash || 'it-desktop'), 'it_desktop.txt'
                 FROM module_test WHERE work_order IS NOT NULL ORDER BY id LIMIT 25""")
        self.assertEqual(call('GET', f'/api/summary?work_order={self.WO}')[1]['total'], 0, 'not yet: view not refreshed')
        self.refresh_stats()
        s = call('GET', f'/api/summary?work_order={self.WO}')[1]
        self.assertEqual(s['test_attempts'], 25)
        self.assertGreater(s['total'], 0)


# ═══ Background reparse ══════════════════════════════════════════════════════

class T6Reparse(Base):

    def test_start_progress_cancel(self):
        A = self.admin
        st, job = call('POST', '/api/reparse', A)
        if st == 409:
            self.skipTest('a reparse is already running')
        self.assertEqual((st, job['status']), (200, 'running'))
        self.assertEqual(call('POST', '/api/reparse', A)[0], 409, 'one at a time')
        time.sleep(3)
        self.assertEqual(call('POST', f"/api/reparse/{job['id']}/cancel", A)[0], 200)
        for _ in range(20):
            cur = call('GET', '/api/reparse', A)[1]
            if cur['status'] != 'running':
                break
            time.sleep(1)
        self.assertEqual(cur['status'], 'cancelled')
        self.assertGreater(cur['done'], 0)
        self.assertEqual(cur['failed'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
