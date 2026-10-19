// 儀表板: seven numbers (each shown once) and four charts, from GET /api/overview.
import { apiGet } from '../core/api.js';
import * as charts from '../core/charts.js';
import { fmtInt, fmtPct, fmtShortDate, fmtTime, retryLevel, yieldLevel } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $ } from '../core/ui.js';

let seq = 0;

function setNumber(id, text, level) {
    const el = $(id);
    el.textContent = text;
    el.classList.remove('v-good', 'v-warn', 'v-bad');
    if (level) el.classList.add(`v-${level}`);
}

function renderNumbers(s, aligned) {
    const yieldText = s.total ? `${Number(s.yield_pct).toFixed(2)}` : '—';
    const hero = $('#ovYield');
    hero.innerHTML = '';
    hero.append(yieldText);
    if (s.total) { const small = document.createElement('small'); small.textContent = '%'; hero.append(small); }
    hero.classList.remove('v-good', 'v-warn', 'v-bad');
    if (s.total) hero.classList.add(`v-${yieldLevel(s.yield_pct)}`);

    const al = $('#ovAligned');
    al.innerHTML = '';
    if (aligned.has_target && aligned.yield_pct != null) {
        const b = document.createElement('b');
        b.textContent = fmtPct(aligned.yield_pct);
        b.className = `v-${yieldLevel(aligned.yield_pct)}`;
        al.append(t('ov.aligned'), ' ', b);
        if (aligned.gap > 0) {
            const gap = document.createElement('span');
            gap.className = 'gap';
            gap.textContent = t('ov.alignedGap', { gap: fmtInt(aligned.gap) });
            al.append(gap);
        }
    } else {
        al.textContent = t('ov.alignedNoTarget');
    }
    setNumber('#ovUnits', fmtInt(s.total));
    $('#ovUnitsSub').textContent = s.total ? t('ov.unitsSub', { attempts: fmtInt(s.test_attempts) }) : '';
    setNumber('#ovPass', fmtInt(s.pass));
    setNumber('#ovFail', fmtInt(s.fail));
    setNumber('#ovStop', fmtInt(s.stop));
    setNumber('#ovRetry', s.total ? fmtPct(s.retry_rate) : '—', s.total ? retryLevel(s.retry_rate) : '');
    $('#ovRetrySub').textContent = s.total ? t('ov.retrySub', { units: fmtInt(s.retry_units) }) : '';
}

function renderTrend(trend) {
    const pts = trend.points || [];
    $('#ovTrendNote').textContent = pts.length ? (trend.unit === 'hour' ? t('ov.trendHour') : t('ov.trendDay')) : '';
    if (!pts.length) { charts.chartMessage('chOvTrend', t('common.empty')); return; }
    const labels = pts.map(p => (trend.unit === 'hour' ? fmtTime(p.t) : fmtShortDate(p.t)));
    charts.render('chOvTrend', c => ({
        data: {
            labels,
            datasets: [
                { type: 'line', label: t('ov.yieldAxis'), data: pts.map(p => p.yield_pct), yAxisID: 'y',
                  borderColor: c.good, backgroundColor: c.good, tension: 0.25, pointRadius: 3, spanGaps: true, order: 0 },
                { type: 'bar', label: t('ov.unitsAxis'), data: pts.map(p => p.total), yAxisID: 'y1',
                  backgroundColor: charts.alpha(c.c1, 0.55), borderRadius: 4, order: 1 },
            ],
        },
        options: {
            interaction: { mode: 'index', intersect: false },
            scales: {
                y: { position: 'left', suggestedMin: 90, max: 100, title: { display: true, text: t('ov.yieldAxis') } },
                y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: t('ov.unitsAxis') } },
            },
        },
    }));
}

function renderSplit(split) {
    const count = r => (split.find(x => x.result === r) || {}).count || 0;
    const values = [count('PASS'), count('FAIL'), count('STOP')];
    if (!values.some(Boolean)) { charts.chartMessage('chOvSplit', t('common.empty')); return; }
    const total = values.reduce((a, b) => a + b, 0);
    charts.render('chOvSplit', c => ({
        type: 'doughnut',
        data: {
            labels: ['PASS', 'FAIL', 'STOP'].map((l, i) => `${l}  ${fmtInt(values[i])}（${(values[i] * 100 / total).toFixed(2)}%）`),
            datasets: [{ data: values, backgroundColor: [c.pass, c.fail, c.stop], borderWidth: 0 }],
        },
        options: { cutout: '62%', plugins: { legend: { position: window.innerWidth < 900 ? 'bottom' : 'right', labels: { font: { size: 15 } } } } },
    }));
}

function renderMonthly(monthly) {
    const note = monthly.year ? t('ov.yearAll', { year: monthly.year }) : '';
    $('#ovMonthlyNote').textContent = note;
    $('#ovMonthlyFailNote').textContent = note;
    const months = monthly.months || [];
    if (!months.some(m => m.total)) {
        charts.chartMessage('chOvMonthly', t('common.empty'));
        charts.chartMessage('chOvMonthlyFail', t('common.empty'));
        return;
    }
    const labels = months.map(m => t('ov.month', { m: m.month }));
    charts.render('chOvMonthly', c => ({
        type: 'bar',
        data: { labels, datasets: [
            { label: 'PASS', data: months.map(m => m.passed), backgroundColor: c.pass, stack: 'u', borderRadius: 3 },
            { label: 'FAIL + STOP', data: months.map(m => m.failed + m.stopped), backgroundColor: c.fail, stack: 'u', borderRadius: 3 },
        ] },
        options: { scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } } },
    }));
    charts.render('chOvMonthlyFail', c => ({
        type: 'bar',
        data: { labels, datasets: [
            { label: 'FAIL', data: months.map(m => m.failed), backgroundColor: c.fail, borderRadius: 3 },
            { label: 'STOP', data: months.map(m => m.stopped), backgroundColor: c.stop, borderRadius: 3 },
        ] },
        options: { scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
    }));
}

async function load(app) {
    const mine = ++seq;
    try {
        const data = await apiGet(`/api/overview${queryString(app.params())}`);
        if (mine !== seq) return;
        renderNumbers(data.summary, data.aligned);
        if (!data.summary.total) {
            ['chOvTrend', 'chOvSplit'].forEach(id => charts.chartMessage(id, t('common.empty')));
        } else {
            renderTrend(data.trend);
            renderSplit(data.split);
        }
        renderMonthly(data.monthly);
    } catch (err) {
        if (mine !== seq) return;
        ['#ovYield', '#ovUnits', '#ovPass', '#ovFail', '#ovStop', '#ovRetry'].forEach(id => { $(id).textContent = '—'; });
        $('#ovAligned').textContent = err.message;
        ['chOvTrend', 'chOvSplit', 'chOvMonthly', 'chOvMonthlyFail'].forEach(id =>
            charts.chartMessage(id, `${t('common.loadFailed')}：${err.message}`, 'error'));
    }
}

export default { id: 'overview', scoped: true, load };
