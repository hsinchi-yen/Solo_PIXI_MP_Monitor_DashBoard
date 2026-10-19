"""
Solo PIXI MP Monitor Dashboard — Unit Test Suite
Tests HTML structure, API pure functions, and JS alignment logic.
Run: python test_dashboard_suite.py
"""
import io
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

    # ── SVG Donut ring elements ───────────────────────────────────────────────
    def test_donut_arc_pass_id(self):
        self.assertIn('donutArcPass', self.col.ids)

    def test_donut_arc_fail_id(self):
        self.assertIn('donutArcFail', self.col.ids)

    def test_donut_arc_stop_id(self):
        self.assertIn('donutArcStop', self.col.ids)

    def test_donut_ring_pct_id(self):
        self.assertIn('donutRingPct', self.col.ids)

    def test_donut_ring_counts_id(self):
        self.assertIn('donutRingCounts', self.col.ids)

    def test_donut_ring_wrap_class(self):
        self.assertIn('donut-ring-wrap', self.col.classes_seen)

    def test_donut_arc_segment_class(self):
        self.assertIn('donut-arc-segment', self.col.classes_seen)

    # ── MAC1 Range Analysis elements ──────────────────────────────────────────
    def test_mac1_range_summary_id(self):
        self.assertIn('mac1RangeSummary', self.col.ids)

    def test_mac1_range_info_id(self):
        self.assertIn('mac1RangeInfo', self.col.ids)

    def test_tb_mac1_incidents_id(self):
        self.assertIn('tbMac1Incidents', self.col.ids)

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

    def test_js_updateDonutRing_present(self):
        self.assertIn('function updateDonutRing(', self.raw)

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

    # ── /api/fails response contract ─────────────────────────────────────────
    def test_api_fails_selects_work_order(self):
        """Fail List rows must include the Work Order rendered by the dashboard."""
        with open(APP_PATH, encoding='utf-8') as app_file:
            source = app_file.read()

        endpoint = source.split('def api_fails(', 1)[1].split('\n@app.', 1)[0]
        select_clause = endpoint.split('SELECT', 1)[1].split('FROM module_test', 1)[0]
        self.assertRegex(select_clause, r'\bwork_order\b')


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

    # ── updateDonutRing ───────────────────────────────────────────────────────
    def test_updateDonutRing_extractable(self):
        with open(DASHBOARD_PATH, encoding='utf-8') as f:
            raw = f.read()
        self.assertIsNotNone(_extract_js_fn(raw, 'updateDonutRing'))

    def test_updateDonutRing_pass_arc_length(self):
        """PASS arc length = (pass/total) * 282.74."""
        if not self.NODE_AVAILABLE:
            self.skipTest("Node.js not available")
        with open(DASHBOARD_PATH, encoding='utf-8') as f:
            fn_body = _extract_js_fn(f.read(), 'updateDonutRing') or ''
        script = textwrap.dedent("""\
            const _attrs = {};
            const _styles = {};
            const document = {
                getElementById(id) {
                    return {
                        setAttribute(k, v) { _attrs[id + '.' + k] = v; },
                        get style() {
                            if (!_styles[id]) _styles[id] = {};
                            return _styles[id];
                        },
                        set innerHTML(v) {},
                    };
                }
            };
        """) + fn_body + textwrap.dedent("""
            updateDonutRing(80, 10, 10);
            const passArc = parseFloat(_attrs['donutArcPass.stroke-dasharray'].split(' ')[0]);
            const expected = (80/100)*282.74;
            console.log(Math.abs(passArc - expected) < 0.1);
        """)
        self.assertEqual(self._run_js(script), 'true')

    def test_updateDonutRing_zero_total_shows_dash(self):
        if not self.NODE_AVAILABLE:
            self.skipTest("Node.js not available")
        with open(DASHBOARD_PATH, encoding='utf-8') as f:
            fn_body = _extract_js_fn(f.read(), 'updateDonutRing') or ''
        texts = {}
        script = textwrap.dedent("""\
            const _attrs = {};
            const _texts = {};
            const document = {
                getElementById(id) {
                    return {
                        setAttribute(k, v) { _attrs[id + '.' + k] = v; },
                        get style() { return _attrs; },
                        set textContent(v) { _texts[id] = v; },
                        get textContent() { return _texts[id]; },
                        set innerHTML(v) { _texts[id] = v; },
                    };
                }
            };
        """) + fn_body + textwrap.dedent("""
            updateDonutRing(0, 0, 0);
            console.log(_texts['donutRingPct'] === '—');
        """)
        self.assertEqual(self._run_js(script), 'true')

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


# ═══════════════════════════════════════════════════════════════════════════════
ESSENTIAL_DIR   = os.path.join(PROJ_ROOT, 'solo-pixi-essential')
RAWLOGS_DIR     = os.path.join(PROJ_ROOT, 'rawlogs')
SAMPLE_ZIP_005  = os.path.join(RAWLOGS_DIR, '5101-260630005.zip')
SAMPLE_SPLIT_S  = os.path.join(RAWLOGS_DIR, '5101-260630005_S')


def _load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ESSENTIAL_DIR, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _segment(mac1='001F7B6D92A0', mac2='001F7B6D92A1', start='2026/08/24 14:16:18', result='P A S S'):
    return (f"MAC1:\t{mac1}\r\nMAC2:\t{mac2}\r\nStart:\t\t {start}\r\n"
            f"1. BT_BDR\r\n**** {result} ****\r\nEnd:\t\t {start}\r\n\r\n")


class TestLogUploader(unittest.TestCase):
    """Web Log Uploader core: log_splitter.py rules + parser compatibility (no DB)."""

    @classmethod
    def setUpClass(cls):
        cls.ls = _load_module('_log_splitter_under_test', 'log_splitter.py')
        cls.parser = _load_module('_parser_under_test', 'module_log_parser.py')
        cls.raw, cls.col = _collect_html()

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

    # ── Dashboard page ────────────────────────────────────────────────────────
    def test_logupload_page_and_nav(self):
        self.assertIn('logupload', self.col.pages)
        self.assertIn('logupload', self.col.nav_pages)

    def test_logupload_nav_under_management(self):
        nav = self.raw[self.raw.index('<nav>'):self.raw.index('</nav>')]
        mgmt = nav.index('>Management<')
        self.assertLess(mgmt, nav.index('data-page="dataalign"'))
        self.assertLess(nav.index('data-page="dataalign"'), nav.index('data-page="logupload"'))

    def test_logupload_page_loader_registered(self):
        self.assertRegex(self.raw, r'logupload:\s*loadLogUploader')

    def test_scheduled_ai_carousel_removed_manual_button_kept(self):
        # The 10-minute forced page carousel + AI report popup was cancelled; it also hijacked
        # the Log Uploader page mid-upload. The on-demand sidebar button stays.
        for gone in ('startCarouselScheduler', 'startScheduledCarousel', 'prefetchCarouselAiReport'):
            self.assertNotIn(gone, self.raw)
        self.assertIn('nav-ai-report', self.col.ids)
        self.assertIn('function openLatestWoTerminalReport', self.raw)

    def test_logupload_required_ids(self):
        for eid in ('luLogin', 'luPanel', 'luFile', 'luWo', 'luQty', 'luAck', 'btnLuUpload',
                    'luIssues', 'luProgressFill', 'btnLuRevert', 'luHistoryRows'):
            self.assertIn(eid, self.col.ids, eid)


class TestAccountsAndChat(unittest.TestCase):
    """Phase 2: auth_utils.py (accounts) + upload_chat.py (AI confirmation), no DB / LLM."""

    @classmethod
    def setUpClass(cls):
        if ESSENTIAL_DIR not in sys.path:
            sys.path.insert(0, ESSENTIAL_DIR)   # upload_chat imports log_splitter
        cls.auth = _load_module('_auth_utils_under_test', 'auth_utils.py')
        cls.chat = _load_module('_upload_chat_under_test', 'upload_chat.py')
        cls.raw, cls.col = _collect_html()
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

    # ── Dashboard page ────────────────────────────────────────────────────────
    def test_accounts_page_nav_after_logupload(self):
        self.assertIn('accounts', self.col.pages)
        nav = self.raw[self.raw.index('<nav>'):self.raw.index('</nav>')]
        self.assertLess(nav.index('data-page="logupload"'), nav.index('data-page="accounts"'))
        self.assertRegex(self.raw, r'accounts:\s*loadAccounts')

    def test_session_login_replaces_base64_token(self):
        self.assertIn("fetch('/api/auth/login'", self.raw)
        self.assertNotIn("fetch('/api/tweak/login'", self.raw)
        self.assertIn('sessionStorage', self.raw)

    def test_phase2_required_ids(self):
        for eid in ('authBox', 'btnAuthChangePw', 'btnAuthLogout', 'navAccounts', 'tweakForbidden',
                    'luChatPanel', 'luChatLog', 'luChatInput', 'btnLuChatSend', 'btnLuMode', 'luFormPanel',
                    'luHistoryUser', 'acctUserRows', 'acctAuditRows', 'acctLoginRows', 'btnAcctNew', 'formModal'):
            self.assertIn(eid, self.col.ids, eid)


# ─── Runner ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    for cls in (TestHTMLStructure, TestAPIFunctions, TestJavaScriptLogic, TestLogUploader, TestAccountsAndChat):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
