"""
Solo PIXI MP Monitor Dashboard — unit tests (no database, no network).

    python tests/test_unit.py          # or: python -m unittest discover -s tests -p "test_unit.py"

Covers the shared splitter/parser, account + chat helpers, date-range filters, the AI
report number check, the static web app (structure, i18n coverage, themes) and — when
Node.js is installed — the browser modules via `node --test "tests/js/*.test.mjs"`. Tests that need
FastAPI run only where it is installed (CI, the api container).
"""
import ast
import importlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from datetime import date
from html.parser import HTMLParser
import unittest.mock
from unittest.mock import MagicMock

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESSENTIAL_DIR = os.path.join(PROJ_ROOT, 'solo-pixi-essential')
API_DIR = os.path.join(ESSENTIAL_DIR, 'api')
WEB_DIR = os.path.join(ESSENTIAL_DIR, 'web')
RAWLOGS_DIR = os.path.join(PROJ_ROOT, 'rawlogs')
SAMPLE_ZIP_005 = os.path.join(RAWLOGS_DIR, '5101-260630005.zip')
SAMPLE_SPLIT_S = os.path.join(RAWLOGS_DIR, '5101-260630005_S')

for _p in (API_DIR, ESSENTIAL_DIR):          # same import layout as the api container
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import fastapi  # noqa: F401
    HAVE_FASTAPI = True
except ImportError:
    HAVE_FASTAPI = False


def _load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ESSENTIAL_DIR, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _service(name):
    return importlib.import_module(f'services.{name}')


def _segment(mac1='001F7B6D92A0', mac2='001F7B6D92A1', start='2026/08/24 14:16:18', result='P A S S'):
    return (f"MAC1:\t{mac1}\r\nMAC2:\t{mac2}\r\nStart:\t\t {start}\r\n"
            f"1. BT_BDR\r\n**** {result} ****\r\nEnd:\t\t {start}\r\n\r\n")


class TestLogSplitter(unittest.TestCase):
    """Web Log Uploader core: log_splitter.py rules + parser compatibility (no DB)."""

    @classmethod
    def setUpClass(cls):
        cls.ls = _load_module('_log_splitter_under_test', 'log_splitter.py')
        cls.parser = _load_module('_parser_under_test', 'module_log_parser.py')

    @staticmethod
    def _zip(members):
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            for name, data in members.items():
                z.writestr(name, data)
        buf.seek(0)
        return zipfile.ZipFile(buf)

    # ── Work order ────────────────────────────────────────────────────────────
    def test_infer_wo_from_zip_name(self):
        self.assertEqual(self.ls.infer_work_order('5101-260630005.zip'), '5101-260630005')

    def test_infer_wo_strips_log_prefix(self):
        self.assertEqual(self.ls.infer_work_order('log5101-260630007.zip', 'log5101-260630007'), '5101-260630007')

    def test_infer_wo_none_when_absent(self):
        self.assertIsNone(self.ls.infer_work_order('upload.zip', 'logs'))

    def test_wo_format(self):
        self.assertTrue(self.ls.is_valid_work_order('5101-260630005'))
        self.assertTrue(self.ls.is_valid_work_order('5101-25122302'))
        for bad in ('', '5101-2606', 'log5101-260630007', '5101_260630005', '5101-26063000A'):
            self.assertFalse(self.ls.is_valid_work_order(bad), bad)

    def test_parse_quantity(self):
        self.assertEqual(self.ls.parse_quantity('4,000'), 4000)
        self.assertEqual(self.ls.parse_quantity(' 4000 '), 4000)
        self.assertEqual(self.ls.parse_quantity(4000), 4000)
        for bad in ('abc', '4000台', '', None, True, '4.5'):
            self.assertIsNone(self.ls.parse_quantity(bad), repr(bad))

    # ── Zip discovery ─────────────────────────────────────────────────────────
    def test_find_log_all_entries_rules(self):
        zf = self._zip({
            'WO/40/logWO/Log_All.txt': 'x',
            'WO/10/logWO/Log_ALL.TXT': 'x',          # case-insensitive
            'WO/20/logWO/Log_all.csv': 'x',          # ignored: not Log_All.txt
            'WO/misc/Log_All.txt': 'x',              # ignored: no numeric station folder
            'WO/5/logWO/Log_All.txt': 'x',           # any numeric folder is a station
        })
        stations = [s for s, _ in self.ls.find_log_all_entries(zf)]
        self.assertEqual(stations, ['5', '10', '40'])

    # ── Splitting ─────────────────────────────────────────────────────────────
    def test_split_to_records_body_and_names(self):
        content = _segment() + _segment(mac1='001F7B6D92A2', mac2='001F7B6D92A3', result='F A I L')
        records, invalid = self.ls.split_to_records(content, '5101-260630005')
        self.assertEqual([r['filename'] for r in records], [
            '5101-260630005_20260824_141618_001F7B6D92A0_001F7B6D92A1_PASS.txt',
            '5101-260630005_20260824_141618_001F7B6D92A2_001F7B6D92A3_FAIL.txt',
        ])
        self.assertEqual(invalid, [])
        for r in records:
            self.assertNotIn('\r', r['content'])
            self.assertTrue(r['content'].endswith('****\nEnd:\t\t 2026/08/24 14:16:18\n\n'))

    def test_split_to_records_invalid_mac_listed(self):
        records, invalid = self.ls.split_to_records(_segment(mac1='^%', mac2='^%', result='S T O P'), '5101-260630005')
        self.assertEqual(records, [])
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0]['start'], '20260824_141618')
        self.assertIn('MAC', invalid[0]['reason'])

    def test_split_to_records_missing_result_listed(self):
        seg = "MAC1:\t001F7B6D92A0\nMAC2:\t001F7B6D92A1\nStart:\t\t 2026/08/24 14:16:18\n"
        records, invalid = self.ls.split_to_records(seg, '5101-260630005')
        self.assertEqual(records, [])
        self.assertIn('測試結果', invalid[0]['reason'])

    def test_content_hash_matches_parser_file_hash(self):
        records, _ = self.ls.split_to_records(_segment(), '5101-260630005')
        rec = self.parser.parse_log_content(records[0]['content'], records[0]['filename'])
        self.assertEqual(self.ls.content_hash(records[0]['content']), rec['file_hash'])
        self.assertEqual(rec['work_order'], '5101-260630005')
        self.assertEqual(rec['result'], 'PASS')

    def test_analyze_zip_facts(self):
        zf = self._zip({
            '5101-260630005/10/x/Log_All.txt': _segment() + _segment(mac1='^%', mac2='^%', result='S T O P'),
            '5101-260630005/20/x/Log_All.txt': _segment(start='2026/08/25 09:00:00') + _segment(mac1='001F7B6D92A2', result='F A I L'),
        })
        facts, hashes = self.ls.analyze_zip(zf, 'whatever.zip')
        self.assertEqual(facts['inferred_wo'], '5101-260630005')
        self.assertEqual(facts['total_records'], 3)
        self.assertEqual(len(hashes), 3)
        self.assertEqual((facts['pass'], facts['fail'], facts['stop'], facts['invalid']), (2, 1, 0, 1))
        self.assertEqual(facts['unique_mac1'], 2)
        self.assertEqual([s['station'] for s in facts['stations']], ['10', '20'])
        self.assertEqual(facts['invalid_items'][0]['station'], '10')
        self.assertEqual((facts['first_test'], facts['last_test']), ('2026-08-24 14:16:18', '2026-08-25 09:00:00'))

    # ── Validation rules ──────────────────────────────────────────────────────
    FACTS = {'inferred_wo': '5101-260630005', 'unique_mac1': 3976, 'invalid': 4}

    def _codes(self, *args, **kw):
        return {i['code']: i['level'] for i in self.ls.validate_upload(self.FACTS, *args, **kw)}

    def test_rules_errors_for_empty_or_bad_input(self):
        self.assertEqual(self._codes('', '')['R1'], 'error')
        self.assertEqual(self._codes('', '')['R2'], 'error')
        self.assertEqual(self._codes('5101-2606', 'abc')['R1'], 'error')
        self.assertEqual(self._codes('5101-260630005', '0')['R2'], 'error')

    def test_rules_clean_input_only_info(self):
        issues = self.ls.validate_upload(self.FACTS, '5101-260630005', '4,000')
        self.assertFalse(self.ls.has_errors(issues))
        self.assertFalse(self.ls.has_warnings(issues))
        codes = {i['code'] for i in issues}
        self.assertEqual(codes, {'R5', 'R7', 'R8'})
        r5 = next(i for i in issues if i['code'] == 'R5')['message']
        self.assertIn('4,000', r5)
        self.assertIn('3,976', r5)
        self.assertIn('+24', r5)

    def test_rules_wo_mismatch_warns(self):
        self.assertEqual(self._codes('5101-260630007', 4000)['R3'], 'warn')

    def test_rules_existing_other_wo_warns_same_wo_info(self):
        codes = self._codes('5101-260630005', 4000, existing_by_wo={'5101-260630005': 10, '5101-111111111': 5})
        self.assertEqual(codes['R4'], 'warn')
        self.assertEqual(codes['R6'], 'info')

    def test_rules_alignment_target(self):
        self.assertNotIn('R7', self._codes('5101-260630005', 4000, current_target=4000))
        msg = [i for i in self.ls.validate_upload(self.FACTS, '5101-260630005', 4000, current_target=3900) if i['code'] == 'R7']
        self.assertIn('3,900', msg[0]['message'])

    # ── Parser compatibility ──────────────────────────────────────────────────
    def test_insert_record_extra_columns(self):
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.rowcount = 1
        self.assertTrue(self.parser.insert_record(conn, {'work_order': 'W'}, extra={'upload_id': 'abc'}))
        sql, values = cur.execute.call_args[0]
        self.assertIn('source_file, upload_id)', sql)
        self.assertEqual(values[-1], 'abc')
        self.assertEqual(len(values), len(self.parser.DB_COLUMNS) + 1)

    def test_insert_record_without_extra_unchanged(self):
        conn = MagicMock()
        conn.cursor.return_value.rowcount = 0
        self.assertFalse(self.parser.insert_record(conn, {}))
        sql, values = conn.cursor.return_value.execute.call_args[0]
        self.assertNotIn('upload_id', sql)
        self.assertEqual(len(values), len(self.parser.DB_COLUMNS))

    def test_plain_sum_is_left_to_right(self):
        values = [0.1] * 10
        expected = 0.0
        for v in values:
            expected += v
        self.assertEqual(self.parser._plain_sum(values), expected)   # 0.9999999999999999, not fsum's 1.0

    @unittest.skipUnless(os.path.isfile(SAMPLE_ZIP_005) and os.path.isdir(SAMPLE_SPLIT_S),
                         "rawlogs sample (5101-260630005) not present")
    def test_sample_zip_matches_desktop_split_output(self):
        import zipfile
        with zipfile.ZipFile(SAMPLE_ZIP_005) as zf:
            entries = self.ls.find_log_all_entries(zf)
            self.assertEqual([s for s, _ in entries], ['10', '20', '30', '40'])
            web = {r['filename']: r['content']
                   for _, recs, _ in self.ls.iter_station_records(zf, entries, '5101-260630005') for r in recs}
        desktop = {n for n in os.listdir(SAMPLE_SPLIT_S) if self.parser.FILENAME_RE.match(n)}
        self.assertEqual(set(web), desktop)
        self.assertEqual(len(web), 4335)
        for name in sorted(desktop)[::40]:   # ~110 files: hashes and every parsed field identical
            from_disk = self.parser.parse_log_file(os.path.join(SAMPLE_SPLIT_S, name))
            self.assertEqual(self.parser.parse_log_content(web[name], name), from_disk, name)



class TestAccountsAndChat(unittest.TestCase):
    """services/auth_utils.py (accounts) + services/upload_chat.py (uploader AI chat), no DB / LLM."""

    @classmethod
    def setUpClass(cls):
        cls.auth = _service('auth_utils')
        cls.chat = _service('upload_chat')
        cls.facts = {'zip_name': 'log5101-260630007.zip', 'inferred_wo': '5101-260630007',
                     'stations': [{'station': s} for s in ('10', '20', '30', '40')],
                     'total_records': 2041, 'unique_mac1': 1893, 'invalid': 3,
                     'first_test': '2026-09-30 08:00:00', 'last_test': '2026-10-01 13:07:58'}

    # ── Passwords / sessions ──────────────────────────────────────────────────
    def test_password_hash_roundtrip(self):
        h = self.auth.hash_password('dummy1234', iterations=1000)
        self.assertTrue(h.startswith('pbkdf2_sha256$1000$'))
        self.assertTrue(self.auth.verify_password('dummy1234', h))
        self.assertFalse(self.auth.verify_password('dummy12345', h))

    def test_password_hash_is_salted(self):
        self.assertNotEqual(self.auth.hash_password('same-pass', iterations=1000),
                            self.auth.hash_password('same-pass', iterations=1000))

    def test_verify_rejects_garbage(self):
        for stored in (None, '', 'plain', 'md5$1$aa$bb', 'pbkdf2_sha256$x$zz$yy'):
            self.assertFalse(self.auth.verify_password('x', stored), stored)

    def test_session_token_stored_as_digest(self):
        token = self.auth.new_session_token()
        self.assertGreaterEqual(len(token), 40)
        digest = self.auth.token_hash(token)
        self.assertEqual(len(digest), 64)
        self.assertNotIn(token, digest)

    def test_username_rules(self):
        n = self.auth.normalize_username
        self.assertEqual(n('  Wang.Li '), 'wang.li')
        self.assertIsNone(self.auth.username_error('wang.li'))
        for bad in ('ab', 'a b', '王小明', 'x' * 33, ''):
            self.assertIsNotNone(self.auth.username_error(n(bad)), bad)
        self.assertIsNotNone(self.auth.username_error('pixi', reserved=['PIXI']))

    def test_password_rules(self):
        self.assertIsNone(self.auth.password_error('12345678'))
        for bad in (None, '', '1234567', 'x' * 129):
            self.assertIsNotNone(self.auth.password_error(bad))

    def test_profile_rules(self):
        profile, err = self.auth.clean_profile('  王小明 ', '', ' wang@example.com ')
        self.assertIsNone(err)
        self.assertEqual(profile, {'display_name': '王小明', 'department': None, 'email': 'wang@example.com'})
        self.assertIsNotNone(self.auth.clean_profile(' ', None, None)[1])
        self.assertIsNotNone(self.auth.clean_profile('x', None, 'not-an-email')[1])

    def test_upload_ownership(self):
        admin = {'username': 'pixi', 'role': 'admin'}
        wang = {'username': 'wang', 'role': 'uploader'}
        self.assertTrue(self.auth.can_access_upload(admin, 'wang'))
        self.assertTrue(self.auth.can_access_upload(wang, 'wang'))
        self.assertFalse(self.auth.can_access_upload(wang, 'chen'))
        self.assertFalse(self.auth.can_access_upload(wang, None))

    # ── AI chat ───────────────────────────────────────────────────────────────
    def test_opening_uses_exact_facts(self):
        text = self.chat.opening_message(self.facts)
        for part in ('4 個站別', '2,041', '1,893', '3 筆無效'):
            self.assertIn(part, text)

    def test_work_order_regex_wins(self):
        f = self.chat.merge_fields({}, '工單是5101-260630007，2000台', llm_wo='5101-260630001', llm_qty=2000)
        self.assertEqual(f, {'work_order': '5101-260630007', 'planned_qty': 2000})

    def test_known_values_kept_when_message_adds_nothing(self):
        f = self.chat.merge_fields({'work_order': '5101-260630007', 'planned_qty': '2,000'}, '好')
        self.assertEqual(f, {'work_order': '5101-260630007', 'planned_qty': 2000})
        self.assertEqual(self.chat.merge_fields('not-a-dict', '好'), {'work_order': None, 'planned_qty': None})

    def test_fallback_quantity_without_llm(self):
        q = self.chat.fallback_quantity
        self.assertEqual(q('工單是5101-260630007，2000台'), 2000)
        self.assertEqual(q('4,000 pcs'), 4000)
        for unsure in ('4k', '四千', '3500 台，不對，是 3,600', '5101-2606300'):
            self.assertIsNone(q(unsure), unsure)
        f = self.chat.merge_fields({}, '5101-260630007 2000台', llm_ok=False)
        self.assertEqual(f, {'work_order': '5101-260630007', 'planned_qty': 2000})

    def test_parse_extract(self):
        self.assertEqual(self.chat.parse_extract('{"work_order": " 5101-260630007 ", "planned_qty": 2000}'),
                         ('5101-260630007', 2000))
        self.assertEqual(self.chat.parse_extract('{"work_order": null, "planned_qty": true}'), (None, None))
        with self.assertRaises(ValueError):
            self.chat.parse_extract('not json')

    def test_next_step(self):
        s = self.chat.next_step
        self.assertEqual(s({'work_order': None, 'planned_qty': None}), 'work_order')
        self.assertEqual(s({'work_order': '5101-26063', 'planned_qty': 2000}), 'work_order')
        self.assertEqual(s({'work_order': '5101-260630007', 'planned_qty': None}), 'planned_qty')
        self.assertEqual(s({'work_order': '5101-260630007', 'planned_qty': 0}), 'planned_qty')
        self.assertEqual(s({'work_order': '5101-260630007', 'planned_qty': 2000}), 'confirm')

    def test_normalize_messages(self):
        ok = self.chat.normalize_messages([{'role': 'assistant', 'content': 'hi'}, {'role': 'user', 'content': ' x '}])
        self.assertEqual(ok[-1], {'role': 'user', 'content': 'x'})
        for bad in ([], [{'role': 'system', 'content': 'x'}], [{'role': 'user', 'content': 'x' * 501}],
                    [{'role': 'user', 'content': 'x'}, {'role': 'assistant', 'content': 'y'}],
                    [{'role': 'user', 'content': 'x'}] * 41):
            with self.assertRaises(ValueError):
                self.chat.normalize_messages(bad)

    def test_reply_prompt_carries_rule_results_only(self):
        ls = _load_module('_log_splitter_for_chat', 'log_splitter.py')
        fields = {'work_order': '5101-260630007', 'planned_qty': 2000}
        issues = ls.validate_upload(self.facts, fields['work_order'], 2000, {}, None)
        msgs = self.chat.build_reply_messages(self.facts, fields, issues, [{'role': 'user', 'content': 'x'}])
        system = msgs[0]['content']
        self.assertEqual(msgs[0]['role'], 'system')
        self.assertIn('不可以自行判斷資料對錯', system)
        self.assertIn('差 +107 台', system)                  # R5 text from the rules, not the model
        self.assertIn('不要提到「我已確認」', system)          # no warning → no ack instruction
        warn = ls.validate_upload(self.facts, '5101-260630099', 2000, {}, None)
        system = self.chat.build_reply_messages(self.facts, {'work_order': '5101-260630099', 'planned_qty': 2000},
                                                warn, [{'role': 'user', 'content': 'x'}])[0]['content']
        self.assertIn('勾選畫面上的「我已確認」', system)

    def test_reply_prompt_hides_missing_value_errors_while_asking(self):
        ls = _load_module('_log_splitter_for_chat2', 'log_splitter.py')
        issues = ls.validate_upload(self.facts, '', None, {}, None)
        system = self.chat.build_reply_messages(self.facts, {'work_order': None, 'planned_qty': None},
                                                issues, [{'role': 'user', 'content': 'x'}])[0]['content']
        self.assertNotIn('請輸入工單計畫生產數。', system)
        self.assertIn('還沒有取得工單名稱', system)


class TestEnglishMessages(unittest.TestCase):
    """Server messages shown in the UI exist in English too (Msg / message_en); no DB, no LLM."""

    CJK = re.compile(r'[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]')
    ERROR_CALLS = {'ApiError': 1, 'bad_request': 0, 'not_found': 0, 'conflict': 0}   # index of the message arg

    @classmethod
    def setUpClass(cls):
        cls.errors = importlib.import_module('errors')
        cls.auth = _service('auth_utils')
        cls.chat = _service('upload_chat')
        cls.ls = _load_module('_log_splitter_en', 'log_splitter.py')
        cls.facts = {'zip_name': 'log5101-260630007.zip', 'inferred_wo': '5101-260630007',
                     'stations': [{'station': s} for s in ('10', '20', '30', '40')],
                     'total_records': 2041, 'unique_mac1': 1893, 'invalid': 3,
                     'first_test': '2026-09-30 08:00:00', 'last_test': '2026-10-01 13:07:58'}

    def assertEnglish(self, text):
        self.assertTrue(text, 'empty English text')
        self.assertIsNone(self.CJK.search(text), text)

    def test_every_raised_error_has_english(self):
        missing = []
        for root, _, files in os.walk(API_DIR):
            for name in files:
                if not name.endswith('.py'):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding='utf-8') as f:
                    tree = ast.parse(f.read())
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    fn = getattr(node.func, 'id', None) or getattr(node.func, 'attr', None)
                    if fn == 'ValueError' and node.args and isinstance(node.args[0], (ast.Constant, ast.JoinedStr)) \
                            and self.CJK.search(ast.unparse(node.args[0])):
                        missing.append(f'{os.path.relpath(path, API_DIR)}:{node.lineno}')
                    if fn not in self.ERROR_CALLS or len(node.args) <= self.ERROR_CALLS[fn]:
                        continue
                    arg = node.args[self.ERROR_CALLS[fn]]
                    # a Msg(…) literal, or a variable holding one (checked by the tests below)
                    if isinstance(arg, (ast.Constant, ast.JoinedStr)):
                        missing.append(f'{os.path.relpath(path, API_DIR)}:{node.lineno}')
        self.assertEqual(missing, [])

    def test_error_response_carries_both_languages(self):
        err = self.errors.bad_request(self.errors.Msg('工單號太長', 'The work order is too long.'), 'bad_work_order')
        self.assertEqual(str(err.message), '工單號太長')
        self.assertEqual(self.errors.english(err.message), 'The work order is too long.')
        self.assertEqual(self.errors.english(self.errors.not_found().message), 'Not found')
        if HAVE_FASTAPI:
            body = json.loads(self.errors.error_response(400, err.message, err.code).body)
            self.assertEqual(body, {'error': '工單號太長', 'error_en': 'The work order is too long.', 'code': 'bad_work_order'})

    def test_account_rule_messages(self):
        for msg in (self.auth.username_error('a'), self.auth.username_error('pixi', reserved=['pixi']),
                    self.auth.password_error('1'), self.auth.password_error('x' * 500),
                    self.auth.clean_profile(' ', None, None)[1], self.auth.clean_profile('x' * 200, None, None)[1],
                    self.auth.clean_profile('x', None, 'bad')[1]):
            self.assertTrue(self.CJK.search(msg))
            self.assertEnglish(self.errors.english(msg))

    def test_chat_input_errors(self):
        for bad in ([], [{'role': 'system', 'content': 'x'}], [{'role': 'user', 'content': 'x' * 501}],
                    [{'role': 'user', 'content': ''}], [{'role': 'user', 'content': 'x'}] * 41):
            with self.assertRaises(ValueError) as ctx:
                self.chat.normalize_messages(bad)
            self.assertEnglish(self.errors.english(ctx.exception.args[0]))

    def test_upload_rules_in_english(self):
        cases = [('', None, {}, None), ('5101-26', 'abc', {}, None),
                 ('5101-260630099', 2000, {'5101-260630001': 5, None: 2, '5101-260630099': 7}, 1990)]
        codes = set()
        for wo, qty, existing, target in cases:
            for issue in self.ls.validate_upload(self.facts, wo, qty, existing, target):
                codes.add(issue['code'])
                self.assertTrue(self.CJK.search(issue['message']))
                self.assertEnglish(issue['message_en'])
        self.assertEqual(codes, {f'R{i}' for i in range(1, 9)})

    def test_invalid_segment_reasons_in_english(self):
        content = _segment(mac1='^%') + _segment(result='')
        _, invalid = self.ls.split_to_records(content, '5101-260630005')
        self.assertEqual(len(invalid), 2)
        for item in invalid:
            self.assertTrue(self.CJK.search(item['reason']))
            self.assertEnglish(item['reason_en'])

    def test_chat_texts_follow_ui_language(self):
        self.assertEqual(self.chat.ui_lang('en'), 'en')
        self.assertEqual(self.chat.ui_lang('xx'), 'zh')
        opening = self.chat.opening_message(self.facts, 'en')
        self.assertEnglish(opening)
        for part in ('4 stations', '2,041', '1,893', '3 invalid'):
            self.assertIn(part, opening)
        for fields in ({'work_order': None, 'planned_qty': None}, {'work_order': '5101-260630007', 'planned_qty': None},
                       {'work_order': '5101-260630007', 'planned_qty': 2000}):
            self.assertEnglish(self.chat.fallback_reply(fields, 'en'))
            issues = self.ls.validate_upload(self.facts, fields['work_order'] or '', fields['planned_qty'], {}, None)
            system = self.chat.build_reply_messages(self.facts, fields, issues, [{'role': 'user', 'content': 'x'}], 'en')[0]['content']
            self.assertIn('Always reply in English', system)
            self.assertEnglish(system)
        warn = self.ls.validate_upload(self.facts, '5101-260630099', 2000, {}, None)
        system = self.chat.build_reply_messages(self.facts, {'work_order': '5101-260630099', 'planned_qty': 2000},
                                                warn, [{'role': 'user', 'content': 'x'}], 'en')[0]['content']
        self.assertIn('tick "I have checked"', system)
        self.assertIn('a difference of +107', system)


class TestFilters(unittest.TestCase):
    """services/filters.py — date-range scope (B2) and the legacy parameters it replaces."""

    @classmethod
    def setUpClass(cls):
        cls.f = _service('filters')
        cls.errors = importlib.import_module('errors')

    def scope(self, **kw):
        return self.f.parse_scope(**kw)

    def test_empty_scope(self):
        s = self.scope()
        self.assertTrue(s.is_empty)
        self.assertEqual(self.f.raw_where(s), ('', []))

    def test_inclusive_range_becomes_half_open(self):
        s = self.scope(date_from='2026-08-01', date_to='2026-08-31')
        self.assertEqual((s.date_from, s.date_to), (date(2026, 8, 1), date(2026, 9, 1)))
        where, params = self.f.raw_where(s)
        self.assertEqual(where, 'WHERE COALESCE(unit_date, start_time::date) >= %s AND COALESCE(unit_date, start_time::date) < %s')
        self.assertEqual(params, [date(2026, 8, 1), date(2026, 9, 1)])

    def test_expression_matches_index(self):
        # idx_mt_eff_date is built on exactly this expression; any drift loses the index.
        with open(os.path.join(API_DIR, 'services', 'migrations.py'), encoding='utf-8') as f:
            mig = f.read()
        self.assertIn(f'(({self.f.EFF_DATE}))', mig)

    def test_legacy_year_month_day(self):
        self.assertEqual(self.scope(year=2026).date_from, date(2026, 1, 1))
        self.assertEqual(self.scope(year=2026).date_to, date(2027, 1, 1))
        s = self.scope(year=2026, month=2)
        self.assertEqual((s.date_from, s.date_to), (date(2026, 2, 1), date(2026, 3, 1)))
        s = self.scope(day='2026-08-21')
        self.assertEqual((s.date_from, s.date_to), (date(2026, 8, 21), date(2026, 8, 22)))

    def test_iso_week_bug_b3_fixed(self):
        # Old SQL mixed ISO week with calendar year: year=2024&week=1 also matched 2024-12-30/31.
        s = self.scope(year=2024, week=1)
        self.assertEqual((s.date_from, s.date_to), (date(2024, 1, 1), date(2024, 1, 8)))

    def test_bad_input_is_400(self):
        for kw in ({'date_from': '2026-13-01'}, {'month': 3}, {'year': 2026, 'week': 60},
                   {'date_from': '2026-08-10', 'date_to': '2026-08-01'}, {'work_order': 'x' * 65}):
            with self.assertRaises(self.errors.ApiError, msg=kw) as ctx:
                self.scope(**kw)
            self.assertEqual(ctx.exception.status, 400)

    def test_work_order_and_mv_where(self):
        s = self.scope(work_order=' 5101-260630006 ', date_from='2026-08-12')
        where, params = self.f.mv_where(s)
        self.assertEqual(where, 'WHERE eff_date >= %s AND wo_key = %s')
        self.assertEqual(params, [date(2026, 8, 12), '5101-260630006'])

    def test_describe(self):
        self.assertEqual(self.scope().describe(), '全部資料')
        self.assertEqual(self.scope(day='2026-08-21').describe(), '2026-08-21')


class TestReportText(unittest.TestCase):
    """services/report_text.py — AI report number check, Markdown, comparison (C2/C3)."""

    FACTS = {
        'work_order': '5101-260630006', 'units': 3992, 'pass': 3991, 'fail': 1, 'stop': 0,
        'yield_pct': 99.97, 'retry_rate': 8.77, 'retry_units': 350,
        'period': {'first_test': '2026-08-12 16:25:46', 'last_test': '2026-08-21 16:38:31', 'days': 8},
        'top_causes': [{'error_code': 'ERR_5', 'step': 'BT_TX_BDR', 'category': 'TestFail', 'count': 213},
                       {'error_code': 'ERR_65', 'step': 'WIFI_RX_VERIFY_PER', 'category': 'TestFail', 'count': 41}],
        'planned': {'planned_qty': 3990, 'tested_units': 3992, 'gap': 0, 'completion_pct': 100.05, 'aligned_yield_pct': 99.97},
        'previous_avg': {'yield_pct': 100.0, 'retry_rate': 8.05, 'retry_change': 0.72},
        'risk': {'level': 'high', 'yield': 'good', 'retry': 'bad'},
    }

    @classmethod
    def setUpClass(cls):
        cls.rt = _service('report_text')

    def report(self, summary, findings=(), recs=()):
        return {'summary': summary, 'findings': list(findings), 'recommendations': list(recs)}

    def test_numbers_from_facts_pass(self):
        r = self.report('良率 99.97%，測試 3,992 台，重測率 8.77% 高於前次 8.05%（上升 0.72）。',
                        ['ERR_5 在 BT_TX_BDR 發生 213 次。'], ['把重測率降到 5.0% 以下。'])
        self.assertEqual(self.rt.check_numbers(r, self.FACTS), {'ok': True, 'unknown': []})

    def test_invented_number_flagged(self):
        # Seen in the local run: the model summed three error codes (41 + 16 + 13) into "70".
        r = self.report('良率 99.97%', ['ERR_65、ERR_61、ERR_60 共 70 次'])
        self.assertEqual(self.rt.check_numbers(r, self.FACTS)['unknown'], ['70'])

    def test_identifiers_are_not_numbers(self):
        r = self.report('工單 5101-260630006 的 ERR_65 與 WIFI_5G 問題，QCA9377 模組，2.4G 頻段')
        self.assertTrue(self.rt.check_numbers(r, self.FACTS)['ok'])

    def test_rounding_tolerated(self):
        self.assertTrue(self.rt.check_numbers(self.report('良率約 100%，重測率 8.8%'), self.FACTS)['ok'])

    def test_level(self):
        self.assertEqual(self.rt.level(99.0, 98, 95), 'good')
        self.assertEqual(self.rt.level(96.0, 98, 95), 'warn')
        self.assertEqual(self.rt.level(90.0, 98, 95), 'bad')
        self.assertEqual(self.rt.level(4.0, 3, 5, higher_is_better=False), 'warn')
        self.assertEqual(self.rt.level(None, 3, 5), 'unknown')

    def test_facts_hash_stable(self):
        a = self.rt.facts_hash(self.FACTS)
        self.assertEqual(a, self.rt.facts_hash(json.loads(json.dumps(self.FACTS))))
        changed = dict(self.FACTS, units=3993)
        self.assertNotEqual(a, self.rt.facts_hash(changed))

    def test_markdown(self):
        row = {'facts': self.FACTS, 'finished_at': None,
               'report_zh': self.report('總評一句。', ['發現一', '發現二', '發現三'], ['建議一', '建議二', '建議三'])}
        md = self.rt.to_markdown(row, 'zh')
        for part in ('# 工單品質報告 — 5101-260630006', '| 良率 | 99.97% |', '## 發現', '3. 發現三', '| ERR_5 | BT_TX_BDR | 213 |'):
            self.assertIn(part, md)

    def test_compare(self):
        after = json.loads(json.dumps(self.FACTS))
        after['units'] = 4000
        after['yield_pct'] = 99.5
        after['top_causes'] = [{'error_code': 'ERR_5', 'count': 220}, {'error_code': 'ERR_9', 'count': 3}]
        d = self.rt.compare(self.FACTS, after)
        fields = {c['field']: c for c in d['changes']}
        self.assertEqual(fields['units']['delta'], 8)
        self.assertEqual(fields['yield_pct']['delta'], -0.47)
        self.assertEqual(d['causes_added'], ['ERR_9'])
        self.assertEqual(d['causes_removed'], ['ERR_65'])
        self.assertEqual(d['causes_changed'], [{'error_code': 'ERR_5', 'before': 213, 'after': 220}])

    def test_schema_fixes_three_items(self):
        props = self.rt.REPORT_SCHEMA['properties']
        self.assertEqual((props['findings']['minItems'], props['findings']['maxItems']), (3, 3))
        self.assertEqual(set(self.rt.REPORT_SCHEMA['required']), {'summary', 'findings', 'recommendations'})


class _Html(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.dup_ids, self.nav_pages, self.sections = set(), set(), [], []
        self.i18n_keys, self.scripts, self.styles, self.inline_styles = set(), [], [], 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get('id'):
            (self.dup_ids if a['id'] in self.ids else self.ids).add(a['id'])
            if a['id'].startswith('page-') and tag == 'section':
                self.sections.append(a['id'][5:])
        if a.get('data-page'):
            self.nav_pages.append(a['data-page'])
        for k in ('data-i18n', 'data-i18n-placeholder', 'data-i18n-title', 'data-i18n-aria'):
            if a.get(k):
                self.i18n_keys.add(a[k])
        if 'style' in a:
            self.inline_styles += 1
        if tag == 'script' and a.get('src'):
            self.scripts.append(a['src'])
        if tag == 'link' and a.get('rel') == 'stylesheet':
            self.styles.append(a['href'])


class TestWebApp(unittest.TestCase):
    """solo-pixi-essential/web — structure, themes, i18n coverage, no CDN, bug B1 guard."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(WEB_DIR, 'index.html'), encoding='utf-8') as f:
            cls.html = f.read()
        cls.doc = _Html()
        cls.doc.feed(cls.html)
        cls.js = {}
        for root, _, files in os.walk(os.path.join(WEB_DIR, 'js')):
            for name in files:
                path = os.path.join(root, name)
                with open(path, encoding='utf-8') as f:
                    cls.js[os.path.relpath(path, WEB_DIR)] = f.read()
        cls.css = ''
        for n in sorted(os.listdir(os.path.join(WEB_DIR, 'css'))):
            with open(os.path.join(WEB_DIR, 'css', n), encoding='utf-8') as f:
                cls.css += f.read()

    def test_nine_pages_each_with_nav_section_and_module(self):
        pages = ['overview', 'workorders', 'fails', 'measurements', 'failanalysis', 'dbtweak', 'dataalign', 'logupload', 'accounts']
        self.assertEqual(sorted(self.doc.nav_pages), sorted(pages))
        self.assertEqual(sorted(self.doc.sections), sorted(pages))
        main = self.js['js/main.js']
        for p in pages:
            self.assertIn(f"from './pages/{p}.js'", main, p)
            self.assertIn(f"id: '{p}'", self.js[f'js/pages/{p}.js'], p)

    def test_ai_report_is_a_button_not_a_page_link(self):
        # B1: the old <a> without data-page blanked the content area when clicked.
        m = re.search(r'<(\w+)[^>]*id="navAiReport"', self.html)
        self.assertEqual(m.group(1), 'button')
        self.assertNotIn('data-page', m.group(0))

    def test_no_duplicate_ids(self):
        self.assertEqual(self.doc.dup_ids, set())

    def test_ids_used_by_js_exist(self):
        used = set()
        for src in self.js.values():
            used |= set(re.findall(r"\$\('#([A-Za-z][\w-]*)'\)", src))
            used |= set(re.findall(r"getElementById\('([A-Za-z][\w-]*)'\)", src))
            used |= set(re.findall(r"chartMessage\('([A-Za-z][\w-]*)'", src))
            used |= set(re.findall(r"charts\.render\('([A-Za-z][\w-]*)'", src))
        missing = sorted(i for i in used if i not in self.doc.ids and i != 'toast')
        self.assertEqual(missing, [])

    def test_every_i18n_key_defined(self):
        # strings.en.js and strings.zh.js have the same keys (checked in tests/js/i18n.test.mjs)
        defined = set(re.findall(r"^\s+'([\w.]+)':", self.js['js/core/strings.en.js'], re.M))
        self.assertEqual(defined, set(re.findall(r"^\s+'([\w.]+)':", self.js['js/core/strings.zh.js'], re.M)))
        used = set(self.doc.i18n_keys)
        for src in self.js.values():
            code = re.sub(r'^\s*//.*$', '', src, flags=re.M)      # ignore usage examples in comments
            used |= set(re.findall(r"\bt\('([\w.]+)'", code))
        # keys built at runtime: t(`lu.status.${s}`), t(`job.${s}`), t(`acct.action.${a}`) …
        dynamic_prefixes = set(re.findall(r"\bt\(`([\w.]+)\$\{", ''.join(self.js.values())))
        self.assertTrue(dynamic_prefixes)
        for prefix in dynamic_prefixes:
            self.assertTrue(any(k.startswith(prefix) for k in defined), prefix)
        self.assertEqual(sorted(used - defined), [])

    def test_two_themes_only(self):
        themes = set(re.findall(r'\[data-theme="(\w+)"\]', self.css))
        self.assertEqual(themes, {'dark', 'light'})
        self.assertIn("'light' ? 'light' : 'dark'", self.html)   # dark is the default

    def test_no_inline_styles_in_markup(self):
        self.assertEqual(self.doc.inline_styles, 0)

    def test_assets_local_no_cdn(self):
        for ref in self.doc.scripts + self.doc.styles:
            self.assertFalse(ref.startswith('http'), ref)
            self.assertTrue(os.path.isfile(os.path.join(WEB_DIR, ref)), ref)

    def test_no_html_from_ai_text(self):
        # AI report / chat text is rendered with textContent; no Markdown-to-HTML anywhere.
        for name in ('js/report.js', 'js/pages/logupload.js'):
            self.assertNotIn('marked', self.js[name])
        self.assertNotIn('innerHTML = report', self.js['js/report.js'])

    def test_colours_only_in_theme(self):
        with open(os.path.join(WEB_DIR, 'css', 'theme.css'), encoding='utf-8') as f:
            theme = f.read()
        hexes = re.findall(r'#[0-9a-fA-F]{6}\b', self.css.replace(theme, ''))
        self.assertEqual(hexes, [])

    def test_idle_rotation_only_overview_and_workorders(self):
        self.assertIn("const ROTATION = ['overview', 'workorders'];", self.js['js/main.js'])

    def test_english_is_the_default_language(self):
        self.assertIn('<html lang="en"', self.html)
        self.assertIn('id="btnLang"', self.html)
        self.assertIn("LANGS.includes(stored) ? stored : 'en'", self.js['js/core/i18n.js'])

    def test_no_chinese_text_outside_the_dictionary(self):
        # Every visible string comes from strings.*.js; comments may stay Chinese.
        cjk = re.compile(r'[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]')
        found = []
        for name, src in self.js.items():
            if name.startswith('js/core/strings.'):
                continue
            for n, line in enumerate(src.splitlines(), 1):
                code = re.sub(r'//.*$|^\s*\*.*$|/\*.*?\*/', '', line)
                if cjk.search(code) and "'未分類'" not in code:      # data value from the AI facts
                    found.append(f'{name}:{n}')
        body = re.sub(r'<!--.*?-->', '', self.html, flags=re.S)
        if cjk.search(re.sub(r'<script>.*?</script>', '', body, flags=re.S)):
            found.append('index.html')
        self.assertEqual(found, [])


@unittest.skipUnless(shutil.which('node'), 'Node.js not installed')
class TestJavaScript(unittest.TestCase):
    """Runs the browser-module tests in tests/js with Node's built-in test runner."""

    def test_node_suite(self):
        js_dir = os.path.join(PROJ_ROOT, 'tests', 'js')
        files = sorted(os.path.join(js_dir, f) for f in os.listdir(js_dir) if f.endswith('.test.mjs'))
        proc = subprocess.run(['node', '--test', *files], capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:] + proc.stderr[-2000:])


@unittest.skipUnless(HAVE_FASTAPI, 'FastAPI not installed (runs in CI / the api container)')
class TestApiApp(unittest.TestCase):
    """The FastAPI app: routes, OpenAPI security scheme, error shape — without a database."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.app = importlib.import_module('app').app
        cls.client = TestClient(cls.app)      # not a context manager: lifespan (DB) does not run

    def test_routes_present(self):
        paths = {r.path for r in self.app.routes}
        for p in ('/health', '/api/overview', '/api/measurements', '/api/alignment', '/api/trend', '/api/hourly-output', '/api/filter-options',
                  '/api/reports/{work_order}', '/api/reports/{work_order}/generate', '/api/reports/{work_order}/ask',
                  '/api/reparse', '/api/stats/refresh', '/api/auth/login', '/api/log-upload/commit', '/api/admin/users'):
            self.assertIn(p, paths)
        for gone in ('/api/mac-range', '/api/date-units', '/api/wifi-metrics', '/api/workorders/{wo}/ai-summary'):
            self.assertNotIn(gone, paths)

    def test_openapi_has_token_scheme(self):
        schema = self.client.get('/openapi.json').json()
        self.assertIn('X-Tweak-Token', json.dumps(schema['components']['securitySchemes']))

    def test_bad_date_is_400_with_error_shape(self):
        r = self.client.get('/api/summary?date_from=2026-99-01')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['code'], 'bad_date')

    def test_protected_routes_need_token(self):
        for method, path in (('get', '/api/tweak/records'), ('post', '/api/reparse'), ('get', '/api/admin/users')):
            with unittest.mock.patch('services.auth.session_identity', return_value=None):
                r = getattr(self.client, method)(path)
            self.assertEqual(r.status_code, 401, path)
            self.assertEqual(r.json()['code'], 'unauthorized')

    def test_alignment_post_requires_login_b9(self):
        with unittest.mock.patch('services.auth.session_identity', return_value=None):
            r = self.client.post('/api/alignment-targets', json={'5101-260630006': 4000})
        self.assertEqual(r.status_code, 401)


if __name__ == '__main__':
    unittest.main(verbosity=2)
