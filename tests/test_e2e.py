"""
Browser end-to-end tests (Playwright) against a running stack; also writes the screenshots
used in docs/test-report/.

    pip install playwright && python -m playwright install chromium   # once
    PIXI_BASE_URL=http://localhost:8001 python tests/test_e2e.py [--shots DIR]

Uses Chrome if installed (PIXI_BROWSER_CHANNEL=chrome, default), else bundled Chromium.
Upload steps use rawlogs/log5101-260630007.zip and revert their batch at the end.
The UI opens in English (the default); test_12/13 switch to 中文 and back.
"""
import os
import re
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
SHOTS = os.environ.get('PIXI_SHOTS', os.path.join(ROOT, 'docs', 'test-report', 'v3.1.0', 'screenshots'))
CJK = re.compile(r'[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]')
PAGES = ['overview', 'workorders', 'fails', 'measurements', 'failanalysis', 'dbtweak', 'dataalign', 'logupload', 'accounts']
# UI text of the sidebar, top bar and visible page — without data (table bodies, names, chat, selects)
# and without the language button and the upload menu item, which are Chinese on purpose (Q55).
UI_TEXT_JS = """() => [document.querySelector('.sidebar'), document.querySelector('.topbar'),
    document.querySelector('.page:not([hidden])')].map(el => {
        const c = el.cloneNode(true);
        c.querySelectorAll('tbody, select, .msg, #authName, #btnLang, #pageScope, #luFileInfo, a[data-page="logupload"]')
            .forEach(x => x.remove());
        return c.textContent;
    }).join(' ')"""
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
        if full:
            # Full-page captures draw sticky elements (sidebar, header) at the current scroll position
            pg.evaluate('window.scrollTo(0, 0)')
        pg.wait_for_timeout(600)  # chart transitions (300 ms) finish
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
        self.assertIn('Updated', pg.inner_text('#freshText'))
        self.assertEqual(pg.get_attribute('html', 'lang'), 'en', 'English by default')
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
            self.assertEqual(pg.get_attribute('#rpLang button[data-lang="en"]', 'aria-pressed'), 'true',
                             'Q49 the report opens in the UI language')
            self.assertIsNone(CJK.search(pg.inner_text('#rpBody')), 'English report text')
            self.shot(pg, '02_ai_report_en')
            pg.click('#rpLang button[data-lang="zh"]')
            pg.wait_for_timeout(300)
            self.shot(pg, '03_ai_report_zh')
            with pg.expect_download() as dl:
                pg.click('#btnRpDownload')
            self.assertTrue(dl.value.suggested_filename.endswith('_zh.md'))
            pg.click('#rpLang button[data-lang="en"]')
            pg.click('#btnRpCompare')
            pg.wait_for_selector('#rpCompareBox:not([hidden]) h4, #rpCompareBox:not([hidden]) .muted')
            pg.fill('#rpAskInput', 'Which step does ERR_5 mostly happen in?')
            pg.click('#btnRpAsk')
            pg.wait_for_function("document.querySelectorAll('#rpAskLog .msg.assistant:not(.typing)').length >= 1", timeout=90000)
            answer = pg.inner_text('#rpAskLog .msg.assistant').strip()
            self.assertTrue(answer)
            self.assertIsNone(CJK.search(answer), f'English question, English answer: {answer}')
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
        pg.click('#failPager button:has-text("Next")')
        pg.wait_for_timeout(600)
        self.assertIn('Page 2 /', pg.inner_text('#failPager'), 'server-side paging works')
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
        pg.fill('#fm_display_name', 'E2E Tester')
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
            self.assertIn('Read 4 stations', pg.inner_text('#luChatLog'), 'opening in the UI language')
            pg.fill('#luChatInput', f'Work order {WO_007}, 2000 units')
            pg.press('#luChatInput', 'Enter')
            pg.wait_for_function("!document.getElementById('btnLuUpload').disabled", timeout=90000)
            reply = pg.inner_text('#luChatLog .msg.assistant:last-child')
            self.assertIsNone(CJK.search(reply), f'Q49 the assistant answers in English: {reply}')
            self.assertIsNone(CJK.search(pg.inner_text('#luIssues')), 'checks R1–R8 in English')
            self.shot(pg, '12_upload_ai_chat')
            pg.click('#btnLuMode')
            self.assertEqual(pg.input_value('#luWo'), WO_007, 'chat → form keeps values')
            pg.click('#btnLuMode')
        else:
            pg.fill('#luWo', WO_007)
            pg.fill('#luQty', '2000')
            pg.wait_for_function("!document.getElementById('btnLuUpload').disabled")
        pg.click('#btnLuUpload')
        pg.wait_for_function("document.getElementById('luProgressText').textContent.includes('Upload complete')", timeout=180000)
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

    # ── B4 / B5: a failed request shows a message; idle rotation keeps going ───
    def test_11_api_failure_message_and_rotation_b4_b5(self):
        # Own context without the console hook: the aborted requests log errors on purpose.
        ctx = self.browser.new_context(viewport={'width': 1920, 'height': 1080})
        pg = ctx.new_page()
        pg.clock.install()
        pg.route('**/api/overview*', lambda route: route.abort())
        pg.goto(BASE + '/', wait_until='networkidle')
        pg.wait_for_selector('#page-overview .state-msg.error')
        self.assertIn('Failed to load', pg.inner_text('#page-overview .ov-charts'), 'B4 failure shown in place')
        self.assertIn('err', pg.get_attribute('#apiDot', 'class'), 'B4/B6 API light turns red')
        self.shot(pg, '18_api_error')
        # B5: after 5 idle minutes the rotation moves on although the overview failed …
        pg.clock.fast_forward('05:31')
        pg.wait_for_function("location.hash === '#workorders'")
        # … and comes back to a working overview once the API answers again.
        pg.unroute('**/api/overview*')
        pg.clock.fast_forward('00:31')
        pg.wait_for_function("location.hash === '#overview'")
        pg.wait_for_function("document.getElementById('ovYield').textContent !== '—'")
        self.assertIn('ok', pg.get_attribute('#apiDot', 'class'))
        ctx.close()

    # ── Q45–Q50: English by default, one button to 中文, remembered, ?lang= wins ──
    def test_12_language_switch_keeps_numbers_and_is_remembered(self):
        pg = self.page()
        pg.goto(BASE + '/', wait_until='networkidle')
        pg.wait_for_function("document.getElementById('ovYield').textContent !== '—'")
        self.assertEqual(pg.inner_text('#btnLang'), '中文')
        self.assertEqual(pg.inner_text('#pageTitle'), 'Dashboard')
        numbers = {i: pg.inner_text(i) for i in ('#ovYield', '#ovUnits', '#ovPass', '#ovFail', '#ovStop', '#ovRetry')}
        wo = pg.input_value('#woSelect')
        pg.locator('.prefs').screenshot(path=os.path.join(SHOTS, '19_lang_button_en.png'))
        pg.click('#btnLang')
        pg.wait_for_function("document.documentElement.lang === 'zh-Hant-TW'")
        pg.wait_for_function("document.getElementById('pageTitle').textContent === '儀表板'")
        pg.wait_for_timeout(800)
        self.assertEqual(pg.inner_text('#btnLang'), 'EN')
        self.assertIn('資料更新於', pg.inner_text('#freshText'))
        self.assertIn('良率', pg.inner_text('#page-overview'))
        self.assertEqual({i: pg.inner_text(i) for i in numbers}, numbers, 'same numbers in both languages')
        self.assertEqual(pg.input_value('#woSelect'), wo, 'filters kept')
        pg.locator('.prefs').screenshot(path=os.path.join(SHOTS, '19_lang_button_zh.png'))
        self.shot(pg, '20_overview_zh')
        pg.reload(wait_until='networkidle')
        pg.wait_for_function("document.getElementById('ovYield').textContent !== '—'")
        self.assertEqual(pg.get_attribute('html', 'lang'), 'zh-Hant-TW', 'Q47 choice remembered')
        pg.goto(BASE + '/?lang=en#overview', wait_until='networkidle')
        self.assertEqual(pg.inner_text('#pageTitle'), 'Dashboard', 'Q47 ?lang=en wins over the saved choice')
        pg.click('#btnLang')
        pg.click('#btnLang')                     # back to English for the other tests (saved per browser context)
        self.assertEqual(pg.get_attribute('html', 'lang'), 'en')

    def test_13_every_page_english_then_chinese(self):
        pg = self.page()
        pg.goto(BASE + '/', wait_until='networkidle')
        self.login(pg, ADMIN_USER, ADMIN_PASS)
        found = {}
        for page_id in PAGES:
            self.goto(pg, page_id)
            text = pg.evaluate(UI_TEXT_JS)
            hits = sorted(set(CJK.findall(text)))
            if hits:
                found[page_id] = ''.join(hits)
        self.assertEqual(found, {}, 'Q48 no Chinese UI text in English mode')
        pg.click('#btnLang')
        pg.wait_for_function("document.documentElement.lang === 'zh-Hant-TW'")
        for page_id in PAGES:
            self.goto(pg, page_id)
            self.assertIsNotNone(CJK.search(pg.inner_text('#pageTitle')), page_id)
            self.shot(pg, f'21_zh_{PAGES.index(page_id) + 1:02d}_{page_id}')
        pg.click('#btnLang')

    def test_14_language_switch_with_report_open(self):
        pg = self.page()
        pg.goto(BASE + '/', wait_until='networkidle')
        pg.click('#navAiReport')
        pg.wait_for_selector('#reportModal:not([hidden])')
        pg.wait_for_function("document.querySelector('#rpBody .rp-summary') || document.querySelector('#rpBody .state-msg')",
                             timeout=120000)
        self.assertIn('AI Quality Report', pg.inner_text('#rpTitle'))
        pg.evaluate("document.getElementById('btnLang').click()")   # the modal covers the sidebar
        pg.wait_for_function("document.getElementById('rpTitle').textContent.includes('AI 品質報告')")
        self.assertEqual(pg.get_attribute('#rpLang button[data-lang="zh"]', 'aria-pressed'), 'true')
        pg.keyboard.press('Escape')
        pg.click('#btnLang')

    # ── Q53–Q55: uploaders (line staff) get 中文; sign-out restores the browser's language ──
    @unittest.skipUnless(os.path.isfile(ZIP_007), 'rawlogs/log5101-260630007.zip not present')
    def test_15_uploader_gets_chinese_then_signout_restores_english(self):
        pg = self.page()
        pg.goto(BASE + '/', wait_until='networkidle')
        self.assertEqual(pg.get_attribute('html', 'lang'), 'en')
        self.assertEqual(pg.inner_text('a[data-page="logupload"] [data-i18n]'), '上傳 Log', 'Q55 menu item in Chinese')
        token = pg.request.post(BASE + '/api/auth/login', data={'username': ADMIN_USER, 'password': ADMIN_PASS}).json()['token']
        pg.request.post(BASE + '/api/admin/users', headers={'X-Tweak-Token': token},   # 400 when test_07 made it already
                        data={'username': E2E_USER, 'display_name': 'E2E Tester', 'password': E2E_PASS})
        try:
            self.login(pg, E2E_USER, E2E_PASS)
            pg.wait_for_function("document.documentElement.lang === 'zh-Hant-TW'")
            self.assertEqual(pg.inner_text('#pageTitle'), '上傳 Log', 'Q54 uploader → 中文')
            self.assertEqual(pg.inner_text('#btnLang'), 'EN')
            pg.set_input_files('#luFile', ZIP_007)
            pg.wait_for_selector('#luStepConfirm:not([hidden])', timeout=120000)
            self.assertIn('分析完成', pg.inner_text('#luAnalyzeStatus'))
            if pg.locator('#luChatPanel').is_visible():
                self.assertIn('已讀取 4 個站別', pg.inner_text('#luChatLog'), 'opening in 中文')
                pg.fill('#luChatInput', f'工單是 {WO_007}，2000 台')
                pg.press('#luChatInput', 'Enter')
                pg.wait_for_function("!document.getElementById('btnLuUpload').disabled", timeout=90000)
                reply = pg.inner_text('#luChatLog .msg.assistant:last-child')
                self.assertIsNotNone(CJK.search(reply), f'the assistant answers in 中文: {reply}')
                self.assertIn('計畫', pg.inner_text('#luIssues'))
            pg.locator('#luStepConfirm').scroll_into_view_if_needed()
            self.shot(pg, '22_uploader_zh_upload')
            pg.click('#btnLuReset')
            pg.click('#btnLogout')
            pg.wait_for_function("document.documentElement.lang === 'en'")
            self.assertEqual(pg.inner_text('#pageTitle'), 'Log Upload', 'sign-out restores English')
            self.shot(pg, '23_after_signout_en')
        finally:
            psql(f"DELETE FROM upload_log WHERE username = '{E2E_USER}' AND status = 'analyzed'")

    def test_99_no_console_errors(self):
        self.assertEqual([e for e in self.errors if 'favicon' not in e], [])


if __name__ == '__main__':
    if '--shots' in sys.argv:
        i = sys.argv.index('--shots')
        SHOTS = sys.argv[i + 1]
        del sys.argv[i:i + 2]
    unittest.main(verbosity=2)
