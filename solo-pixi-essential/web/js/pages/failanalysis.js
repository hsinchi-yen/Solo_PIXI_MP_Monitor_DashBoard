// 失敗分析: top failing steps / error codes, unidentified FAIL / STOP, and the detail table.
import { apiGet } from '../core/api.js';
import * as charts from '../core/charts.js';
import { esc, fmtInt } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $, tableState } from '../core/ui.js';

let seq = 0;
const CHART_TOP = 10;

// The detail rows are per (code, step, message); the chart merges messages so each bar has its own label.
function chartRows(steps) {
    const byLabel = new Map();
    for (const r of steps) {
        const label = `${r.fail_error_code ? `${r.fail_error_code}: ` : ''}${r.fail_step}`;
        byLabel.set(label, (byLabel.get(label) || 0) + r.count);
    }
    return [...byLabel].sort((a, b) => b[1] - a[1]).slice(0, CHART_TOP);
}

async function load(app) {
    const mine = ++seq;
    const qs = queryString(app.params());
    const tbody = $('#faRows');
    try {
        const [steps, config] = await Promise.all([
            apiGet(`/api/fail-analysis${qs}`),
            apiGet(`/api/config-analysis${qs}`),
        ]);
        if (mine !== seq) return;
        if (!steps.length) charts.chartMessage('chFaSteps', t('common.empty'));
        else {
            const rows = chartRows(steps);
            charts.render('chFaSteps', c => ({
                type: 'bar',
                data: {
                    labels: rows.map(r => r[0]),
                    datasets: [{ label: t('fa.occurrences'), data: rows.map(r => r[1]), backgroundColor: c.fail, borderRadius: 4 }],
                },
                options: { indexAxis: 'y', plugins: { legend: { display: false } },
                           scales: { x: { beginAtZero: true, ticks: { precision: 0 } }, y: { ticks: { autoSkip: false } } } },
            }));
        }
        if (!config.length) charts.chartMessage('chFaConfig', t('common.empty'));
        else charts.render('chFaConfig', c => ({
            type: 'bar',
            data: {
                labels: config.map(r => t('fa.configLabel', { result: r.result })),
                datasets: [{ label: t('fa.occurrences'), data: config.map(r => r.count),
                             backgroundColor: config.map(r => (r.result === 'FAIL' ? c.fail : c.stop)), borderRadius: 4 }],
            },
            options: { indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } },
        }));
        if (!steps.length) tableState(tbody, 4, 'empty');
        else tbody.innerHTML = steps.map(r => `<tr>
            <td class="nowrap">${esc(r.fail_error_code || '—')}</td>
            <td>${esc(r.fail_step)}</td>
            <td>${esc(r.fail_message || '—')}</td>
            <td class="num">${fmtInt(r.count)}</td>
        </tr>`).join('');
    } catch (err) {
        if (mine !== seq) return;
        const msg = `${t('common.loadFailed')}：${err.message}`;
        charts.chartMessage('chFaSteps', msg, 'error');
        charts.chartMessage('chFaConfig', msg, 'error');
        tableState(tbody, 4, 'error', msg, () => load(app));
    }
}

export default { id: 'failanalysis', scoped: true, load };
