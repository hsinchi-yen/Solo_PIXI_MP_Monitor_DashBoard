// 量測分析: BT / WiFi / 其他 tabs over one GET /api/measurements (already binned on the server).
import { apiGet } from '../core/api.js';
import * as charts from '../core/charts.js';
import { esc, fmtDateTime, fmtInt, fmtNum, fmtShortDate } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $, $$, tableState } from '../core/ui.js';

let tab = 'bt';
let data = null;
let dataKey = '';
let seq = 0;
let appRef;

const tile = (label, value, cls = '', sub = '') =>
    `<div class="tile"><div class="tile-label">${esc(label)}</div><div class="tile-value ${cls}">${esc(value)}</div>${sub ? `<div class="tile-sub">${esc(sub)}</div>` : ''}</div>`;

function histChart(id, hist, color, noteId) {
    if (noteId) $(noteId).textContent = hist?.clipped ? t('ms.clipped', { n: fmtInt(hist.clipped) }) : '';
    if (!hist || !hist.n) { charts.chartMessage(id, t('common.empty')); return; }
    charts.render(id, c => ({
        type: 'bar',
        data: { labels: hist.labels, datasets: [{ label: t('ms.count'), data: hist.counts, backgroundColor: c[color], borderRadius: 3 }] },
        options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
    }));
}

function renderBt(bt) {
    const s = bt.summary || {};
    const b1 = Number(s.avg_ber_2441), b2 = Number(s.avg_ber_2480);
    const delta = Number.isFinite(b1) && Number.isFinite(b2) ? Math.abs(b1 - b2) : null;
    $('#msBtTiles').innerHTML = [
        tile(t('ms.bdrPass'), fmtInt(s.bdr_passed), 'good-text'),
        tile(t('ms.bdrFail'), fmtInt(s.bdr_failed), s.bdr_failed ? 'bad-text' : ''),
        tile(t('ms.lePass'), fmtInt(s.le_passed), 'good-text'),
        tile(t('ms.leFail'), fmtInt(s.le_failed), s.le_failed ? 'bad-text' : ''),
        tile(t('ms.ber2441'), fmtNum(s.avg_ber_2441, 4)),
        tile(t('ms.ber2480'), fmtNum(s.avg_ber_2480, 4)),
        tile(t('ms.berDelta'), fmtNum(delta, 4), delta != null && delta > 0.01 ? 'warn-text' : ''),
        tile(t('ms.perLe'), fmtNum(s.avg_per_le, 4)),
    ].join('');
    histChart('chMsBdr', bt.hist.bdr_power, 'c1', '#msBdrNote');
    const e1 = bt.hist.edr1_devm, e2 = bt.hist.edr2_devm;
    $('#msEdrNote').textContent = (e1?.clipped || e2?.clipped) ? t('ms.clipped', { n: fmtInt((e1?.clipped || 0) + (e2?.clipped || 0)) }) : '';
    if (!e1?.n) { charts.chartMessage('chMsEdr', t('common.empty')); return; }
    const labels = [...new Set([...e1.labels, ...(e2?.labels || [])])].sort((a, b) => parseFloat(a) - parseFloat(b));
    const map = h => Object.fromEntries((h?.labels || []).map((l, i) => [l, h.counts[i]]));
    const m1 = map(e1), m2 = map(e2);
    charts.render('chMsEdr', c => ({
        type: 'bar',
        data: { labels, datasets: [
            { label: t('ms.edr1'), data: labels.map(l => m1[l] || 0), backgroundColor: charts.alpha(c.c1, 0.8), borderRadius: 3 },
            { label: t('ms.edr2'), data: labels.map(l => m2[l] || 0), backgroundColor: charts.alpha(c.c2, 0.8), borderRadius: 3 },
        ] },
        options: { scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
    }));
}

function evmBars(id, labels, values) {
    if (!values.some(v => v != null)) { charts.chartMessage(id, t('common.empty')); return; }
    charts.render(id, c => ({
        type: 'bar',
        data: { labels, datasets: [{ label: t('ms.avgEvm'), data: values, backgroundColor: [c.c1, c.c3, c.c4, c.c2], borderRadius: 4 }] },
        options: { indexAxis: 'y', plugins: { legend: { display: false } } },
    }));
}

function renderWifi(w) {
    const a = w.avg_evm_24g || {}, b = w.avg_evm_5g || {};
    evmBars('chMsEvm24', ['CCK-11', 'OFDM-54', 'HT20', 'HT40'], [a.cck11, a.ofdm54, a.ht20, a.ht40]);
    evmBars('chMsEvm5', ['OFDM-54', 'HT20', 'HT40', 'VHT80'], [b.ofdm54, b.ht20, b.ht40, b.vht80]);
    const tail = w.tail || [];
    if (!tail.some(x => x.p90 != null)) charts.chartMessage('chMsEvmTail', t('common.empty'));
    else charts.render('chMsEvmTail', c => ({
        type: 'bar',
        data: { labels: tail.map(x => x.label), datasets: [
            { label: 'P90', data: tail.map(x => x.p90), backgroundColor: charts.alpha(c.c1, 0.8), borderRadius: 3 },
            { label: 'P95', data: tail.map(x => x.p95), backgroundColor: charts.alpha(c.c2, 0.8), borderRadius: 3 },
        ] },
        options: { indexAxis: 'y' },
    }));
    histChart('chMsXtalCap', w.hist.xtal_cap, 'c3', '#msCapNote');
    histChart('chMsXtalPpm', w.hist.xtal_ppm, 'c4', '#msPpmNote');
}

function renderOther(o, topRetries) {
    const overall = o.duration?.overall || {};
    $('#msOtherTiles').innerHTML = [
        tile(t('ms.retryUnits'), fmtInt(o.retry?.units), o.retry?.units ? 'warn-text' : 'good-text'),
        tile(t('ms.highRisk'), fmtInt(o.retry?.high), o.retry?.high ? 'bad-text' : 'good-text'),
        tile(t('ms.durP95'), overall.p95 != null ? `${fmtNum(overall.p95, 1)} ${t('common.seconds')}` : '—'),
        tile(t('ms.ppmDrift'), fmtNum(o.ppm_drift, 2)),
    ].join('');
    const r = o.retry || {};
    if (!r.units) charts.chartMessage('chMsRetry', t('common.empty'));
    else charts.render('chMsRetry', c => ({
        type: 'bar',
        data: { labels: [t('ms.riskHigh'), t('ms.riskMedium'), t('ms.riskLow')],
                datasets: [{ label: t('ms.retryUnits'), data: [r.high, r.medium, r.low], backgroundColor: [c.bad, c.warn, c.good], borderRadius: 4 }] },
        options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
    }));
    const by = o.duration?.by_result || [];
    if (!by.length) charts.chartMessage('chMsDuration', t('common.empty'));
    else charts.render('chMsDuration', c => ({
        type: 'bar',
        data: { labels: by.map(x => x.result), datasets: [
            { label: 'P50', data: by.map(x => x.p50), backgroundColor: charts.alpha(c.c3, 0.8), borderRadius: 3 },
            { label: 'P90', data: by.map(x => x.p90), backgroundColor: charts.alpha(c.c4, 0.8), borderRadius: 3 },
            { label: 'P99', data: by.map(x => x.p99), backgroundColor: charts.alpha(c.fail, 0.8), borderRadius: 3 },
        ] },
        options: { scales: { y: { beginAtZero: true, title: { display: true, text: t('common.seconds') } } } },
    }));
    const ppm = o.ppm_trend || [];
    if (!ppm.length) charts.chartMessage('chMsPpm', t('common.empty'));
    else charts.render('chMsPpm', c => ({
        type: 'line',
        data: { labels: ppm.map(x => fmtShortDate(x.day)), datasets: [
            { label: t('ms.ppmAvg'), data: ppm.map(x => x.avg), borderColor: c.c3, backgroundColor: c.c3, tension: 0.2, pointRadius: 2 },
            { label: t('ms.ppmRange'), data: ppm.map(x => x.max), borderColor: 'transparent', backgroundColor: charts.alpha(c.c3, 0.15), fill: '+1', pointRadius: 0 },
            { label: '', data: ppm.map(x => x.min), borderColor: 'transparent', pointRadius: 0 },
        ] },
        options: { plugins: { legend: { labels: { filter: item => item.text } } } },
    }));
    const tbody = $('#msRetryRows');
    if (!topRetries.length) tableState(tbody, 8, 'empty');
    else tbody.innerHTML = topRetries.map(x => `<tr>
        <td class="nowrap">${esc(x.work_order || '—')}</td><td><code>${esc(x.mac1)}</code></td>
        <td class="num">${fmtInt(x.attempts)}</td><td class="num">${fmtInt(x.pass_count)}</td>
        <td class="num">${fmtInt(x.fail_count)}</td><td class="num">${fmtInt(x.stop_count)}</td>
        <td><span class="badge ${esc(x.retry_risk)}">${esc(t(`ms.risk${x.retry_risk[0].toUpperCase()}${x.retry_risk.slice(1)}`))}</span></td>
        <td class="nowrap">${esc(fmtDateTime(x.last_attempt))}</td></tr>`).join('');
}

function renderTab() {
    $$('[data-ms-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.msTab === tab)));
    $$('[data-ms-view]').forEach(v => { v.hidden = v.dataset.msView !== tab; });
    if (!data) return;
    if (tab === 'bt') renderBt(data.bt);
    else if (tab === 'wifi') renderWifi(data.wifi);
    else renderOther(data.other, data.top_retries || []);
}

function showError(err) {
    const msg = `${t('common.loadFailed')}：${err.message}`;
    ['chMsBdr', 'chMsEdr', 'chMsEvm24', 'chMsEvm5', 'chMsEvmTail', 'chMsXtalCap', 'chMsXtalPpm', 'chMsRetry', 'chMsDuration', 'chMsPpm']
        .forEach(id => charts.chartMessage(id, msg, 'error'));
    $('#msBtTiles').innerHTML = '';
    $('#msOtherTiles').innerHTML = '';
}

async function load(app = appRef) {
    const key = app.scopeKey();
    const mine = ++seq;
    try {
        const fresh = await apiGet(`/api/measurements${queryString(app.params())}`);
        if (mine !== seq) return;
        data = fresh;
        dataKey = key;
        renderTab();
    } catch (err) {
        if (mine !== seq) return;
        data = null;
        showError(err);
    }
}

function init(app) {
    appRef = app;
    $$('[data-ms-tab]').forEach(b => b.addEventListener('click', () => {
        tab = b.dataset.msTab;
        if (data && dataKey === appRef.scopeKey()) renderTab();
        else { renderTab(); load(); }
    }));
}

export default { id: 'measurements', scoped: true, init, load: app => load(app) };
