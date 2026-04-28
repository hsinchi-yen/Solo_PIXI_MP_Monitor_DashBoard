"""
Solo PIXI MP Monitor Dashboard — Unit Test Suite
Tests HTML structure, API pure functions, and JS alignment logic.
Run: python test_dashboard_suite.py
"""
import os
import re
import sys
import subprocess
import tempfile
import textwrap
import unittest
import importlib.util
from html.parser import HTMLParser
from unittest.mock import MagicMock, patch

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

PROJ_ROOT      = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_PATH = os.path.join(PROJ_ROOT, 'solo-pixi-essential', 'solo_pixi_dashboard.html')
APP_PATH       = os.path.join(PROJ_ROOT, 'solo-pixi-essential', 'api', 'app.py')


# ─── HTML collector ──────────────────────────────────────────────────────────

class _Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids          = set()
        self.pages        = set()
        self.nav_pages    = set()
        self.canvas_ids   = set()
        self.classes_seen = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        eid = attrs.get('id', '')
        if eid:
            self.ids.add(eid)
            if eid.startswith('page-'):
                self.pages.add(eid[len('page-'):])
        dp = attrs.get('data-page', '')
        if dp:
            self.nav_pages.add(dp)
        if tag == 'canvas' and eid:
            self.canvas_ids.add(eid)
        for cls in attrs.get('class', '').split():
            self.classes_seen.add(cls)


def _collect_html():
    with open(DASHBOARD_PATH, encoding='utf-8') as f:
        raw = f.read()
    c = _Collector()
    c.feed(raw)
    return raw, c


# ─── JS function extractor ────────────────────────────────────────────────────

def _extract_js_fn(source, fn_name):
    """Extract a complete function body (handles nested braces) by function name."""
    pattern = rf'\bfunction\s+{re.escape(fn_name)}\s*\('
    m = re.search(pattern, source)
    if not m:
        return None
    start = m.start()
    # find the opening brace
    brace_start = source.index('{', start)
    depth = 0
    for i, ch in enumerate(source[brace_start:], brace_start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    return None


def _build_js_harness():
    """Return a minimal JS harness with the pure utility functions from the dashboard."""
    with open(DASHBOARD_PATH, encoding='utf-8') as f:
        raw = f.read()

    fn_names = ['debounce', 'safeNumber', 'alignGetTarget', 'computeAlignedKPIs']

    stubs = textwrap.dedent("""\
        // --- Browser stubs ---
        let globalAlignStore = {};
        // Suppress store write warnings
        const _origWarn = console.warn;
        console.warn = () => {};
    """)

    bodies = []
    for name in fn_names:
        body = _extract_js_fn(raw, name)
        if body:
            bodies.append(body)

    return stubs + '\n' + '\n\n'.join(bodies) + '\n'


# ═══════════════════════════════════════════════════════════════════════════════
class TestHTMLStructure(unittest.TestCase):
    """Validates structural integrity of solo_pixi_dashboard.html."""

    @classmethod
    def setUpClass(cls):
        cls.raw, cls.col = _collect_html()

    # ── File ──────────────────────────────────────────────────────────────────
    def test_file_exists(self):
        self.assertTrue(os.path.isfile(DASHBOARD_PATH), "Dashboard HTML not found")

    def test_minimum_line_count(self):
        lines = self.raw.count('\n') + 1
        self.assertGreaterEqual(lines, 3200,
            f"File seems too short ({lines} lines); expected ≥3200 after edits")

    # ── Pages ─────────────────────────────────────────────────────────────────
    def test_all_pages_present(self):
        expected = {'overview', 'workorders', 'fails', 'bt', 'wifi',
                    'advanced', 'failanalysis', 'dbtweak', 'dataalign'}
        missing = expected - self.col.pages
        self.assertFalse(missing, f"Missing page divs: {missing}")

    def test_dataalign_page_present(self):
        self.assertIn('dataalign', self.col.pages)

    # ── Nav links ─────────────────────────────────────────────────────────────
    def test_all_nav_links_present(self):
        expected = {'overview', 'workorders', 'fails', 'bt', 'wifi',
                    'advanced', 'failanalysis', 'dbtweak', 'dataalign'}
        missing = expected - self.col.nav_pages
        self.assertFalse(missing, f"Missing nav links: {missing}")

    def test_dataalign_nav_link(self):
        self.assertIn('dataalign', self.col.nav_pages)

    # ── Raw KPI IDs ───────────────────────────────────────────────────────────
    def test_raw_kpi_ids(self):
        for eid in ('kpiYield', 'kpiTotal', 'kpiPass', 'kpiFail', 'kpiStop', 'kpiRetry'):
            self.assertIn(eid, self.col.ids, f"Missing raw KPI element: #{eid}")

    # ── Aligned KPI IDs ───────────────────────────────────────────────────────
    def test_aligned_kpi_ids(self):
        for eid in ('kpiAlignedYield', 'kpiAlignedTotal', 'kpiAlignedPass',
                    'kpiAlignedFail', 'kpiAlignedStop', 'kpiGoldenStops'):
            self.assertIn(eid, self.col.ids, f"Missing aligned KPI element: #{eid}")

    def test_aligned_kpi_grid(self):
        self.assertIn('alignedKpiGrid', self.col.ids)

    # ── Dual KPI label text ───────────────────────────────────────────────────
    def test_aligned_label_present(self):
        self.assertIn('▲ Aligned KPI', self.raw)

    def test_raw_label_present(self):
        self.assertIn('▼ Raw KPI', self.raw)

    # ── Data Alignment page elements ──────────────────────────────────────────
    def test_align_table_body(self):
        self.assertIn('tbAlignWO', self.col.ids)

    def test_align_buttons(self):
        for eid in ('btnAlignSaveAll', 'btnAlignRefresh', 'btnAlignClearAll'):
            self.assertIn(eid, self.col.ids, f"Missing button: #{eid}")

    def test_align_summary_container(self):
        self.assertIn('alignSummary', self.col.ids)

    # ── CSS classes ───────────────────────────────────────────────────────────
    def test_kpi_dual_wrapper_class(self):
        self.assertIn('kpi-dual-wrapper', self.col.classes_seen)

    def test_kpi_aligned_card_class(self):
        self.assertIn('kpi-aligned-card', self.col.classes_seen)

    def test_align_input_class(self):
        self.assertIn('align-input', self.raw)

    # ── No duplicate IDs ─────────────────────────────────────────────────────
    def test_no_duplicate_ids(self):
        seen = {}
        for line_no, line in enumerate(self.raw.splitlines(), 1):
            for m in re.finditer(r'id="([^"]+)"', line):
                eid = m.group(1)
                if eid in seen:
                    self.fail(
                        f"Duplicate id='{eid}' on line {line_no} (first at line {seen[eid]})")
                seen[eid] = line_no

    # ── JS functions present ──────────────────────────────────────────────────
    def test_js_debounce_present(self):
        self.assertIn('function debounce(', self.raw)

    def test_js_computeAlignedKPIs_present(self):
        self.assertIn('function computeAlignedKPIs(', self.raw)

    def test_js_alignGetTarget_present(self):
        self.assertIn('function alignGetTarget(', self.raw)

    def test_js_loadDataAlign_present(self):
        self.assertIn('function loadDataAlign(', self.raw)

    def test_js_initAlignPage_present(self):
        self.assertIn('function initAlignPage(', self.raw)

    def test_js_safeNumber_present(self):
        self.assertIn('function safeNumber(', self.raw)

    # ── pageLoaders wiring ────────────────────────────────────────────────────
    def test_dataalign_in_page_loaders(self):
        self.assertIn('dataalign: loadDataAlign', self.raw)

    # ── initAlignPage called ─────────────────────────────────────────────────
    def test_initAlignPage_called(self):
        self.assertIn('initAlignPage()', self.raw)

    # ── Performance hints ─────────────────────────────────────────────────────
    def test_contain_layout_in_css(self):
        self.assertIn('contain: layout', self.raw)

    # ── Chart.js loaded ──────────────────────────────────────────────────────
    def test_chartjs_script_tag(self):
        self.assertIn('chart.js', self.raw.lower())

    # ── JS function bodies extractable ────────────────────────────────────────
    def test_js_functions_extractable(self):
        for name in ('debounce', 'safeNumber', 'alignGetTarget', 'computeAlignedKPIs'):
            body = _extract_js_fn(self.raw, name)
            self.assertIsNotNone(body, f"Could not extract JS function: {name}")


# ═══════════════════════════════════════════════════════════════════════════════
class TestAPIFunctions(unittest.TestCase):
    """Tests pure helper functions from app.py without a DB connection."""

    @classmethod
    def setUpClass(cls):
        # Ensure necessary stubs are in place before loading
        fake_psycopg2 = MagicMock()
        fake_psycopg2.connect.side_effect = RuntimeError("no DB in tests")
        sys.modules.setdefault('psycopg2', fake_psycopg2)
        sys.modules.setdefault('psycopg2.extras', MagicMock())
        for pkg in ('fastapi', 'fastapi.responses'):
            sys.modules.setdefault(pkg, MagicMock())

        spec = importlib.util.spec_from_file_location("_app_under_test", APP_PATH)
        mod  = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            pass  # startup hook may fail — functions still defined

        # Use staticmethod so Python's descriptor protocol doesn't bind 'self'
        # as the first positional argument when accessed via instance.
        cls._norm_fn  = staticmethod(mod._normalize_page_size)
        cls._build_fn = staticmethod(mod._build_where)

    # ── _normalize_page_size ──────────────────────────────────────────────────
    def test_norm_none_returns_50(self):
        self.assertEqual(self._norm_fn(None), 50)

    def test_norm_int_positive(self):
        self.assertEqual(self._norm_fn(100), 100)

    def test_norm_string_int(self):
        self.assertEqual(self._norm_fn("25"), 25)

    def test_norm_string_all(self):
        self.assertIsNone(self._norm_fn("all"))

    def test_norm_string_ALL_case_insensitive(self):
        self.assertIsNone(self._norm_fn("ALL"))

    def test_norm_zero_raises(self):
        with self.assertRaises(ValueError):
            self._norm_fn(0)

    def test_norm_negative_raises(self):
        with self.assertRaises(ValueError):
            self._norm_fn(-5)

    def test_norm_non_numeric_string_raises(self):
        with self.assertRaises(ValueError):
            self._norm_fn("abc")

    # ── _build_where ──────────────────────────────────────────────────────────
    def test_build_no_filters(self):
        where, params = self._build_fn()
        self.assertEqual(where, "")
        self.assertEqual(params, [])

    def test_build_unit_date(self):
        where, params = self._build_fn(unit_date='2024-01-15')
        self.assertIn("WHERE", where)
        self.assertIn("COALESCE", where)
        self.assertEqual(params, ['2024-01-15'])

    def test_build_work_order(self):
        where, params = self._build_fn(work_order='WO-001')
        self.assertIn("work_order = %s", where)
        self.assertEqual(params, ['WO-001'])

    def test_build_year(self):
        where, params = self._build_fn(year='2024')
        self.assertIn("YEAR", where)
        self.assertEqual(params, [2024])

    def test_build_month(self):
        where, params = self._build_fn(month='3')
        self.assertIn("MONTH", where)
        self.assertEqual(params, [3])

    def test_build_week(self):
        where, params = self._build_fn(week='10')
        self.assertIn("WEEK", where)
        self.assertEqual(params, [10])

    def test_build_day(self):
        where, params = self._build_fn(day='2024-03-15')
        self.assertIn("COALESCE", where)
        self.assertEqual(params, ['2024-03-15'])

    def test_build_multiple_filters(self):
        where, params = self._build_fn(year='2024', month='3', work_order='WO-X')
        self.assertIn("AND", where)
        self.assertIn('WO-X', params)
        self.assertIn(2024, params)
        self.assertIn(3, params)

    def test_build_where_prefix(self):
        where, _ = self._build_fn(year='2024')
        self.assertTrue(where.startswith("WHERE "))


# ═══════════════════════════════════════════════════════════════════════════════
class TestJavaScriptLogic(unittest.TestCase):
    """Tests JS alignment functions extracted from the dashboard and run via Node.js."""

    NODE_AVAILABLE = False
    JS_HARNESS     = ""

    @classmethod
    def setUpClass(cls):
        try:
            r = subprocess.run(['node', '--version'], capture_output=True, timeout=5)
            cls.NODE_AVAILABLE = r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            cls.NODE_AVAILABLE = False
        if cls.NODE_AVAILABLE:
            cls.JS_HARNESS = _build_js_harness()

    def _run_js(self, test_script):
        if not self.NODE_AVAILABLE:
            self.skipTest("Node.js not available")
        full = self.JS_HARNESS + "\n" + test_script
        with tempfile.NamedTemporaryFile(mode='w', suffix='.js',
                                         delete=False, encoding='utf-8') as f:
            f.write(full)
            tmp = f.name
        try:
            r = subprocess.run(['node', tmp], capture_output=True,
                               encoding='utf-8', timeout=15)
            if r.returncode != 0:
                self.fail(
                    f"Node.js test failed:\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}")
            return (r.stdout or '').strip()
        finally:
            os.unlink(tmp)

    # ── safeNumber ────────────────────────────────────────────────────────────
    def test_safeNumber_numeric(self):
        self.assertEqual(self._run_js("console.log(safeNumber(42));"), '42')

    def test_safeNumber_null(self):
        self.assertEqual(self._run_js("console.log(safeNumber(null));"), '0')

    def test_safeNumber_undefined(self):
        self.assertEqual(self._run_js("console.log(safeNumber(undefined));"), '0')

    def test_safeNumber_string_numeric(self):
        self.assertEqual(self._run_js("console.log(safeNumber('3.5'));"), '3.5')

    def test_safeNumber_nan(self):
        self.assertEqual(self._run_js("console.log(safeNumber(NaN));"), '0')

    # ── alignGetTarget ───────────────────────────────────────
    def test_alignGetTarget_unset_returns_null(self):
        self.assertEqual(self._run_js("console.log(alignGetTarget('WO-001'));"), 'null')

    def test_alignGetTarget_returns_number(self):
        out = self._run_js(textwrap.dedent("""\
            globalAlignStore['WO-002'] = 100;
            console.log(alignGetTarget('WO-002'));
        """))
        self.assertEqual(out, '100')

    # ── computeAlignedKPIs — golden stops ────────────────────────────────────
    def test_golden_stops_counted(self):
        script = textwrap.dedent("""\
            const summary = { pass: 80, fail: 10, stop: 5, total: 95 };
            const retries = [
                { pass_count: 1, stop_count: 1 },
                { pass_count: 1, stop_count: 0 },
                { pass_count: 0, stop_count: 1 },
            ];
            const r = computeAlignedKPIs(summary, retries, []);
            console.log(r.goldenStops);
        """)
        self.assertEqual(self._run_js(script), '1')

    def test_aligned_pass_equals_raw_pass(self):
        script = textwrap.dedent("""\
            const summary = { pass: 82, fail: 10, stop: 5, total: 97 };
            const retries = [
                { pass_count: 2, stop_count: 1 },
                { pass_count: 1, stop_count: 1 },
            ];
            const r = computeAlignedKPIs(summary, retries, []);
            console.log(r.alignedPass);
        """)
        self.assertEqual(self._run_js(script), '82')

    def test_aligned_stop_equals_raw_stop(self):
        script = textwrap.dedent("""\
            const summary = { pass: 80, fail: 10, stop: 4, total: 94 };
            const retries = [{ pass_count: 1, stop_count: 1 }];
            const r = computeAlignedKPIs(summary, retries, []);
            console.log(r.alignedStop);
        """)
        self.assertEqual(self._run_js(script), '4')

    def test_aligned_stop_never_negative(self):
        """goldenStops capped by rawStop via Math.max(0, ...)."""
        script = textwrap.dedent("""\
            const summary = { pass: 80, fail: 10, stop: 1, total: 91 };
            const retries = [
                { pass_count: 1, stop_count: 1 },
                { pass_count: 1, stop_count: 1 },
                { pass_count: 1, stop_count: 1 },
            ];
            const r = computeAlignedKPIs(summary, retries, []);
            console.log(r.alignedStop >= 0);
        """)
        self.assertEqual(self._run_js(script), 'true')

    # ── computeAlignedKPIs — gap / target ─────────────────────────────────────
    def test_gap_adds_to_fail_and_total(self):
        script = textwrap.dedent("""\
            globalAlignStore['WO-A'] = 200;
            const summary = { pass: 80, fail: 10, stop: 0, total: 150 };
            const woRows = [{ work_order: 'WO-A', total: 150 }];
            const r = computeAlignedKPIs(summary, [], woRows);
            console.log(r.totalGap, r.alignedFail, r.alignedTotal);
        """)
        self.assertEqual(self._run_js(script), '50 60 200')

    def test_negative_gap_clamped_to_zero(self):
        script = textwrap.dedent("""\
            globalAlignStore['WO-B'] = 50;
            const summary = { pass: 60, fail: 5, stop: 0, total: 65 };
            const woRows = [{ work_order: 'WO-B', total: 65 }];
            const r = computeAlignedKPIs(summary, [], woRows);
            console.log(r.totalGap);
        """)
        self.assertEqual(self._run_js(script), '0')

    def test_wo_without_target_contributes_no_gap(self):
        script = textwrap.dedent("""\
            const summary = { pass: 80, fail: 10, stop: 0, total: 90 };
            const woRows = [{ work_order: 'WO-UNSET', total: 90 }];
            const r = computeAlignedKPIs(summary, [], woRows);
            console.log(r.totalGap);
        """)
        self.assertEqual(self._run_js(script), '0')

    # ── computeAlignedKPIs — yield ────────────────────────────────────────────
    def test_aligned_yield_formula(self):
        script = textwrap.dedent("""\
            const summary = { pass: 90, fail: 10, stop: 0, total: 100 };
            const r = computeAlignedKPIs(summary, [], []);
            console.log(r.alignedYield);
        """)
        self.assertEqual(self._run_js(script), '90.00')

    def test_aligned_yield_zero_total_returns_dash(self):
        # Use unicode code point comparison to avoid terminal encoding issues
        script = textwrap.dedent("""\
            const summary = { pass: 0, fail: 0, stop: 0, total: 0 };
            const r = computeAlignedKPIs(summary, [], []);
            console.log(r.alignedYield === '\\u2014');
        """)
        self.assertEqual(self._run_js(script), 'true')

    def test_aligned_yield_two_decimal_places(self):
        script = textwrap.dedent("""\
            const summary = { pass: 1, fail: 2, stop: 0, total: 3 };
            const r = computeAlignedKPIs(summary, [], []);
            console.log(r.alignedYield);
        """)
        expected = f"{1/3*100:.2f}"
        self.assertEqual(self._run_js(script), expected)

    # ── computeAlignedKPIs — edge / null safety ───────────────────────────────
    def test_null_retries_safe(self):
        out = self._run_js(textwrap.dedent("""\
            const summary = { pass: 10, fail: 2, stop: 0, total: 12 };
            const r = computeAlignedKPIs(summary, null, null);
            console.log(r.goldenStops);
        """))
        self.assertEqual(out, '0')

    def test_no_retries_no_golden_stops(self):
        out = self._run_js(textwrap.dedent("""\
            const summary = { pass: 50, fail: 5, stop: 2, total: 57 };
            const r = computeAlignedKPIs(summary, [], []);
            console.log(r.goldenStops, r.alignedPass, r.alignedStop);
        """))
        self.assertEqual(out, '0 50 2')

    # ── debounce ──────────────────────────────────────────────────────────────
    def test_debounce_returns_function(self):
        out = self._run_js(textwrap.dedent("""\
            const fn = debounce(() => {}, 100);
            console.log(typeof fn);
        """))
        self.assertEqual(out, 'function')

    def test_debounce_does_not_fire_synchronously(self):
        out = self._run_js(textwrap.dedent("""\
            let count = 0;
            const fn = debounce(() => { count++; }, 100);
            fn();
            console.log(count);
        """))
        self.assertEqual(out, '0')


# ─── Runner ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    for cls in (TestHTMLStructure, TestAPIFunctions, TestJavaScriptLogic):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
