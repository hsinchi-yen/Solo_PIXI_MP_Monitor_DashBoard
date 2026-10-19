"""
Browser end-to-end tests (Playwright) against a running stack; also writes the screenshots
used in docs/test-report/.

    pip install playwright && python -m playwright install chromium   # once
    PIXI_BASE_URL=http://localhost:8001 python tests/test_e2e.py [--shots DIR]

Uses Chrome if installed (PIXI_BROWSER_CHANNEL=chrome, default), else bundled Chromium.
Upload steps use rawlogs/log5101-260630007.zip and revert their batch at the end.
"""
import os
import subprocess
import sys
import time
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:   # pragma: no cover
    sync_playwright = None

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

BASE = os.environ.get('PIXI_BASE_URL', 'http://localhost:8001').rstrip('/')
ADMIN_USER = os.environ.get('TWEAK_USER', 'pixi')
ADMIN_PASS = os.environ.get('TWEAK_PASS', 'pixipass')
DB_CONTAINER = os.environ.get('PIXI_DB_CONTAINER', 'pixi-test-db')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP_007 = os.path.join(ROOT, 'rawlogs', 'log5101-260630007.zip')
WO_007 = '5101-260630007'
SHOTS = os.environ.get('PIXI_SHOTS', os.path.join(ROOT, 'docs', 'test-report', 'screenshots'))
E2E_USER, E2E_PASS = 'e2e-uploader', 'e2e-pass-123'


def psql(sql):
    return subprocess.run(['docker', 'exec', DB_CONTAINER, 'psql', '-U', 'pixi', '-d', 'pixi_test', '-At', '-c', sql],
                          capture_output=True, text=True, check=True).stdout.strip()


@unittest.skipUnless(sync_playwright, 'playwright not installed')
class E2E(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.makedirs(SHOTS, exist_ok=True)
        psql(f"DELETE FROM app_sessions WHERE username = '{E2E_USER}'; DELETE FROM app_users WHERE username = '{E2E_USER}';")
        cls.pw = sync_playwright().start()
        channel = os.environ.get('PIXI_BROWSER_CHANNEL', 'chrome')
        try:
            cls.browser = cls.pw.chromium.launch(channel=channel or None, headless=True)
        except Exception:
            cls.browser = cls.pw.chromium.launch(headless=True)
        cls.errors = []

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        psql(f"DELETE FROM app_sessions WHERE username = '{E2E_USER}'; DELETE FROM app_users WHERE username = '{E2E_USER}';"
             f"DELETE FROM account_audit WHERE target = '{E2E_USER}' OR actor = '{E2E_USER}';"
             f"DELETE FROM auth_log WHERE username = '{E2E_USER}';")

    def page(self, width=1920, height=1080):
        ctx = self.browser.new_context(viewport={'width': width, 'height': height}, accept_downloads=True)
        pg = ctx.new_page()
        pg.on('console', lambda m: self.errors.append(f'{m.type}: {m.text}') if m.type == 'error' else None)
        pg.on('pageerror', lambda e: self.errors.append(f'pageerror: {e}'))
        pg.on('dialog', lambda d: d.accept())
        return pg

    def shot(self, pg, name, full=False):
        pg.screenshot(path=os.path.join(SHOTS, f'{name}.png'), full_page=full)

    def goto(self, pg, page_id):
        pg.evaluate(f"location.hash = '{page_id}'")
        pg.wait_for_selector(f'#page-{page_id}:not([hidden])')
        pg.wait_for_load_state('networkidle')
        pg.wait_for_timeout(600)

    def login(self, pg, user, pw, page_id='logupload', prefix='lu'):
        self.goto(pg, page_id)
        pg.fill(f'#{prefix}User', user)
        pg.fill(f'#{prefix}Pass', pw)
        pg.click(f'#btn{prefix.capitalize()}Login')
        pg.wait_for_selector('#authBox:not([hidden])')

    # ── A1 / B1 / B6 / Q3 / Q12: overview ────────────────────────────────────
    def test_01_overview_fits_tv_and_defaults_to_latest_wo(self):
        pg = self.page()
        t0 = time.time()
        pg.goto(BASE + '/', wait_until='networkidle')
        pg.wait_for_function("document.getElementById('ovYield').textContent !== '—'")
        load_s = time.time() - t0
        self.assertLess(load_s, 3.0)
        latest = pg.eval_on_selector('#woSelect', 'el => el.options[1].value')
        self.assertEqual(pg.input_value('#woSelect'), latest, 'Q3 default = latest work order')
        self.assertIn(latest, pg.inner_text('#pageScope'))
        self.assertEqual(pg.evaluate('document.documentElement.scrollHeight <= window.innerHeight + 2'), True,
                         'Q12 overview fits 1920×1080 without scrolling')
        self.assertEqual(pg.locator('.ov-numbers > div').count(), 6, 'A1 seven numbers in six boxes (yield + aligned share one)')
        self.assertEqual(pg.locator('#page-overview canvas').count(), 4, 'Q2 four charts')
        self.assertIn('ok', pg.get_attribute('#apiDot', 'class'), 'B6 API light reflects requests')
        self.assertIn('資料更新於', pg.inner_text('#freshText'))
        self.shot(pg, '01_overview_dark')
        print(f'      overview first load {load_s:.2f}s')

    def test_02_ai_report_button_does_not_blank_page_b1_b2(self):
        pg = self.page()
        pg.goto(BASE + '/#overview', wait_until='networkidle')
        pg.click('#navAiReport')
        pg.wait_for_selector('#reportModal:not([hidden])')
        pg.wait_for_function("document.querySelector('#rpBody .rp-summary') || document.querySelector('#rpBody .state-msg')",
                             timeout=120000)
        self.assertTrue(pg.locator('#page-overview').is_visible(), 'B1 page still shown behind the report')
        # B2: clicking inside the report does not close it; text is selectable.
        pg.click('#rpFacts')
        pg.wait_for_timeout(300)
        self.assertTrue(pg.locator('#reportModal').is_visible())
        if pg.locator('#rpBody .rp-summary').count():
            pg.dblclick('#rpBody .rp-summary')
            self.assertTrue(pg.evaluate('window.getSelection().toString().length > 0'), 'B2 text selectable')
            self.shot(pg, '02_ai_report_zh')
            pg.click('#rpLang button[data-lang="en"]')
            pg.wait_for_timeout(300)
            self.shot(pg, '03_ai_report_en')
            with pg.expect_download() as dl:
                pg.click('#btnRpDownload')
            self.assertTrue(dl.value.suggested_filename.endswith('_en.md'))
            pg.click('#btnRpCompare')
            pg.wait_for_selector('#rpCompareBox:not([hidden]) h4, #rpCompareBox:not([hidden]) .muted')
            pg.fill('#rpAskInput', 'ERR_5 主要發生在哪個步驟？')
            pg.click('#btnRpAsk')
            pg.wait_for_function("document.querySelectorAll('#rpAskLog .msg.assistant:not(.typing)').length >= 1", timeout=90000)
            self.assertTrue(pg.inner_text('#rpAskLog .msg.assistant').strip())
            pg.locator('#rpAskLog').scroll_into_view_if_needed()
            self.shot(pg, '04_ai_report_compare_ask')
        pg.keyboard.press('Escape')
        self.assertTrue(pg.locator('#reportModal').is_hidden(), 'Esc closes')
        self.assertTrue(pg.locator('#page-overview').is_visible())

    def test_03_light_theme_persists(self):
        pg = self.page()
        pg.goto(BASE + '/', wait_until='networkidle')
        self.assertEqual(pg.get_attribute('html', 'data-theme'), 'dark', 'Q5 dark by default')
        pg.click('#btnTheme')
        pg.wait_for_timeout(500)
        self.assertEqual(pg.get_attribute('html', 'data-theme'), 'light')
        pg.reload(wait_until='networkidle')
        pg.wait_for_function("document.getElementById('ovYield').textContent !== '—'")
        self.assertEqual(pg.get_attribute('html', 'data-theme'), 'light', 'choice remembered')
        pg.wait_for_timeout(500)
        self.shot(pg, '05_overview_light')
        pg.click('#btnTheme')

    def test_04_filter_bar_ranges(self):
        pg = self.page()
        pg.goto(BASE + '/#workorders', wait_until='networkidle')
        pg.wait_for_selector('#woRows tr td strong')
        pg.select_option('#woSelect', '')
        pg.wait_for_timeout(600)
        all_rows = pg.locator('#woRows tr').count()
        self.assertGreater(all_rows, 5)
        pg.click('#rangeSeg button[data-range="custom"]')
        pg.fill('#dateFrom', '2026-08-01')
        pg.dispatch_event('#dateFrom', 'change')
        pg.fill('#dateTo', '2026-08-31')
        pg.dispatch_event('#dateTo', 'change')
        pg.wait_for_timeout(800)
        self.assertLess(pg.locator('#woRows tr').count(), all_rows, 'custom range narrows the list')
        self.assertIn('2026-08-01 ~ 2026-08-31', pg.inner_text('#pageScope'))
        self.shot(pg, '06_workorders_range')
        pg.click('#rangeSeg button[data-range="all"]')
        self.assertTrue(pg.locator('#filterBar').is_visible())
        self.goto(pg, 'logupload')
        self.assertTrue(pg.locator('#filterBar').is_hidden(), 'filter bar hidden where unused')

    def test_05_measurements_tabs(self):
        pg = self.page()
        pg.goto(BASE + '/', wait_until='networkidle')
        with pg.expect_response(lambda r: '/api/measurements' in r.url) as resp:
            self.goto(pg, 'measurements')
        size = len(resp.value.body())
        self.assertLess(size, 50 * 1024, 'B1 binned on the server')
        pg.wait_for_selector('#msBtTiles .tile')
        self.shot(pg, '07_measurements_bt')
        for tab in ('wifi', 'other'):
            pg.click(f'[data-ms-tab="{tab}"]')
            pg.wait_for_timeout(700)
            self.shot(pg, f'08_measurements_{tab}', full=(tab == 'other'))
        print(f'      measurements payload {size / 1024:.1f} KB')

    def test_06_fails_and_failanalysis(self):
        pg = self.page()
        pg.goto(BASE + '/', wait_until='networkidle')
        pg.select_option('#woSelect', '')
        self.goto(pg, 'fails')
        pg.wait_for_selector('#failPager button')
        first = pg.inner_text('#failRows tr:first-child td:first-child')
        pg.click('#failPager button:has-text("下一頁")')
        pg.wait_for_timeout(600)
        self.assertIn('第 2 /', pg.inner_text('#failPager'), 'server-side paging works')
        self.assertNotEqual(first, pg.inner_text('#failRows tr:first-child td:first-child'))
        self.shot(pg, '09_fails')
        self.goto(pg, 'failanalysis')
        pg.wait_for_selector('#faRows tr td')
        self.shot(pg, '10_failanalysis')

    def test_07_accounts_and_uploader(self):
        pg = self.page()
        pg.goto(BASE + '/', wait_until='networkidle')
        self.login(pg, ADMIN_USER, ADMIN_PASS)
        self.assertTrue(pg.locator('#navAccounts').is_visible())
        self.goto(pg, 'accounts')
        pg.click('#btnAcctNew')
        pg.fill('#fm_username', E2E_USER)
        pg.fill('#fm_display_name', 'E2E 測試')
        pg.fill('#fm_password', E2E_PASS)
        pg.fill('#fm_password2', E2E_PASS)
        pg.click('#btnFormModalSubmit')
        pg.wait_for_function(f"document.getElementById('acctUserRows').textContent.includes('{E2E_USER}')")
        self.shot(pg, '11_accounts')
        pg.click('#btnLogout')
        pg.wait_for_selector('#authBox', state='hidden')
        self.login(pg, E2E_USER, E2E_PASS)
        self.assertTrue(pg.locator('#navAccounts').is_hidden())
        self.goto(pg, 'dbtweak')
        self.assertTrue(pg.locator('#twForbidden').is_visible(), 'uploader cannot use DB Tweak')

    @unittest.skipUnless(os.path.isfile(ZIP_007), 'rawlogs/log5101-260630007.zip not present')
    def test_08_upload_with_ai_chat_then_revert(self):
        if int(psql(f"SELECT COUNT(*) FROM module_test WHERE work_order = '{WO_007}'")):
            self.skipTest(f'{WO_007} already has data')
        pg = self.page()
        pg.goto(BASE + '/', wait_until='networkidle')
        self.login(pg, ADMIN_USER, ADMIN_PASS)
        pg.set_input_files('#luFile', ZIP_007)
        pg.wait_for_selector('#luStepConfirm:not([hidden])', timeout=120000)
        if pg.locator('#luChatPanel').is_visible():
            pg.fill('#luChatInput', f'工單是 {WO_007}，2000 台')
            pg.press('#luChatInput', 'Enter')
            pg.wait_for_function("!document.getElementById('btnLuUpload').disabled", timeout=90000)
            self.shot(pg, '12_upload_ai_chat')
            pg.click('#btnLuMode')
            self.assertEqual(pg.input_value('#luWo'), WO_007, 'chat → form keeps values')
            pg.click('#btnLuMode')
        else:
            pg.fill('#luWo', WO_007)
            pg.fill('#luQty', '2000')
            pg.wait_for_function("!document.getElementById('btnLuUpload').disabled")
        pg.click('#btnLuUpload')
        pg.wait_for_function("document.getElementById('luProgressText').textContent.includes('上傳完成')", timeout=180000)
        self.assertIn('2,041', pg.inner_text('#luResultGrid'))
        self.shot(pg, '13_upload_done', full=True)
        # The new work order is on the dashboard right away (view refreshed after upload).
        pg.goto(BASE + '/', wait_until='networkidle')
        pg.wait_for_function(f"[...document.getElementById('woSelect').options].some(o => o.value === '{WO_007}')")
        pg.select_option('#woSelect', WO_007)
        pg.wait_for_function("document.getElementById('ovUnits').textContent === '1,893'")
        self.shot(pg, '14_overview_new_wo')
        self.goto(pg, 'logupload')
        pg.wait_for_selector('#luHistoryRows [data-revert]')
        # Wait for this batch's DELETE itself: older test runs may already show 已撤銷 rows.
        with pg.expect_response(lambda r: '/api/log-upload/' in r.url and r.request.method == 'DELETE', timeout=60000) as resp:
            pg.click('#luHistoryRows [data-revert]')
        self.assertEqual(resp.value.status, 200)
        self.assertEqual(resp.value.json()['reverted'], 2041)
        psql(f"DELETE FROM ai_reports WHERE work_order = '{WO_007}'")

    def test_09_dataalign_and_dbtweak(self):
        pg = self.page()
        pg.goto(BASE + '/', wait_until='networkidle')
        self.goto(pg, 'dataalign')
        pg.wait_for_selector('#alRows .target-input')
        self.assertTrue(pg.is_disabled('#alRows .target-input'), 'B9 read-only until signed in')
        self.login(pg, ADMIN_USER, ADMIN_PASS, 'dbtweak', 'tw')
        pg.wait_for_selector('#twRows tr td')
        self.shot(pg, '15_dbtweak_reparse')
        self.goto(pg, 'dataalign')
        self.assertTrue(pg.is_enabled('#alRows .target-input'))
        self.shot(pg, '16_dataalign')

    def test_10_phone_width(self):
        pg = self.page(390, 844)
        pg.goto(BASE + '/', wait_until='networkidle')
        pg.wait_for_function("document.getElementById('ovYield').textContent !== '—'")
        for page_id in ('overview', 'workorders', 'measurements', 'logupload'):
            self.goto(pg, page_id)
            overflow = pg.evaluate('document.documentElement.scrollWidth - window.innerWidth')
            self.assertLessEqual(overflow, 1, page_id)
        self.goto(pg, 'overview')
        self.shot(pg, '17_phone_overview', full=True)

    def test_99_no_console_errors(self):
        self.assertEqual([e for e in self.errors if 'favicon' not in e], [])


if __name__ == '__main__':
    if '--shots' in sys.argv:
        i = sys.argv.index('--shots')
        SHOTS = sys.argv[i + 1]
        del sys.argv[i:i + 2]
    unittest.main(verbosity=2)
