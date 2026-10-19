// 工單: one row per work order with yield / retry colour-coded by the shared thresholds.
import { apiGet } from '../core/api.js';
import { esc, fmtDateTime, fmtInt, fmtNum, fmtPct, retryLevel, yieldLevel } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $, debounce, tableState } from '../core/ui.js';

let rows = [];
let seq = 0;
let appRef;

function render() {
    const q = $('#woSearch').value.trim().toLowerCase();
    const shown = q ? rows.filter(r => (r.work_order || '').toLowerCase().includes(q)) : rows;
    $('#woCount').textContent = t('wo.count', { n: fmtInt(shown.length) });
    const tbody = $('#woRows');
    if (!shown.length) { tableState(tbody, 10, 'empty'); return; }
    tbody.innerHTML = shown.map(r => `<tr>
        <td class="nowrap"><strong>${esc(r.work_order)}</strong></td>
        <td class="nowrap">${esc(fmtDateTime(r.last_test))}</td>
        <td class="num">${fmtInt(r.total)}</td>
        <td class="num good-text">${fmtInt(r.passed)}</td>
        <td class="num bad-text">${fmtInt(r.failed)}</td>
        <td class="num warn-text">${fmtInt(r.stopped)}</td>
        <td class="num"><span class="badge ${yieldLevel(r.yield_pct)}">${fmtPct(r.yield_pct)}</span></td>
        <td class="num"><span class="badge ${retryLevel(r.retry_rate)}">${fmtPct(r.retry_rate)}</span></td>
        <td class="num">${fmtNum(r.avg_duration_sec, 1)}</td>
        <td><button class="btn small" type="button" data-report="${esc(r.work_order)}">✨ ${esc(t('wo.report'))}</button></td>
    </tr>`).join('');
}

async function load(app) {
    const mine = ++seq;
    const tbody = $('#woRows');
    if (!rows.length) tableState(tbody, 10, 'loading');
    try {
        const data = await apiGet(`/api/work-order-summary${queryString(app.params())}`);
        if (mine !== seq) return;
        rows = data;
        render();
    } catch (err) {
        if (mine !== seq) return;
        rows = [];
        tableState(tbody, 10, 'error', `${t('common.loadFailed')}：${err.message}`, () => load(app));
    }
}

function init(app) {
    appRef = app;
    $('#woSearch').addEventListener('input', debounce(render, 200));
    $('#woRows').addEventListener('click', e => {
        const btn = e.target.closest('[data-report]');
        if (btn) appRef.openReport(btn.dataset.report);
    });
}

export default { id: 'workorders', scoped: true, init, load };
