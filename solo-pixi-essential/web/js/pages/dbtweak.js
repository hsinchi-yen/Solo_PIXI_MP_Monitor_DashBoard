// 資料庫維護 (DB Tweak, administrator only): browse / delete records, raw logs, background reparse.
// Uses the same filter bar as the rest of the dashboard; batch delete needs a date range or work order.
import { apiDelete, apiGet, apiPost, download } from '../core/api.js';
import * as auth from '../core/auth.js';
import { esc, fmtDate, fmtDateTime, fmtInt, fmtNum } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $, debounce, tableState, toast } from '../core/ui.js';
import { renderPager } from './fails.js';

const state = { page: 1, key: '', total: 0 };
let seq = 0;
let appRef;
let jobTimer = null;

function renderAccess() {
    const user = auth.currentUser();
    const admin = auth.isAdmin();
    $('#twLogin').hidden = !!user;
    $('#twForbidden').hidden = !user || admin;
    $('#twPanel').hidden = !admin;
    if (user && !admin) $('#twForbiddenText').textContent = t('tw.forbidden', { user: user.username });
}

async function loadRecords() {
    const mine = ++seq;
    const tbody = $('#twRows');
    const params = { ...appRef.params(), page: state.page, page_size: $('#twPageSize').value };
    const search = $('#twSearch').value.trim();
    if (search) params.search = search;
    try {
        const data = await apiGet(`/api/tweak/records${queryString(params)}`);
        if (mine !== seq) return;
        state.page = data.page;
        state.total = data.total;
        if (!data.items.length) tableState(tbody, 10, 'empty');
        else tbody.innerHTML = data.items.map(r => `<tr>
            <td class="num">${r.id}</td>
            <td class="nowrap">${esc(fmtDateTime(r.start_time))}</td>
            <td><code>${esc(r.mac1)}</code></td>
            <td class="nowrap">${esc(r.work_order || '—')}</td>
            <td class="nowrap">${esc(fmtDate(r.unit_date))}</td>
            <td><span class="badge ${esc(String(r.result).toLowerCase())}">${esc(r.result)}</span></td>
            <td class="num">${fmtNum(r.test_duration_sec, 1)}</td>
            <td>${esc(r.fail_step_name || '—')}</td>
            <td class="truncate" title="${esc(r.source_file || '')}">${esc(r.source_file || '—')}</td>
            <td class="nowrap">
                ${r.has_raw_log ? `<button class="btn small" type="button" data-log="${r.id}">${esc(t('fails.download'))}</button>` : `<span class="muted">${esc(t('tw.noLog'))}</span>`}
                <button class="btn small" type="button" data-del="${r.id}" data-mac="${esc(r.mac1)}">${esc(t('tw.deleteOne'))}</button>
            </td></tr>`).join('');
        renderPager($('#twPager'), data, p => { state.page = p; loadRecords(); });
    } catch (err) {
        if (mine !== seq) return;
        tableState(tbody, 10, 'error', t('common.loadFailedMsg', { error: err.message }), loadRecords);
    }
}

/* ─── Reparse job ─── */
function renderJob(job) {
    const running = job?.status === 'running';
    $('#btnReparse').disabled = running;
    $('#btnReparseCancel').hidden = !running;
    const bar = $('#reparseBar');
    if (!job?.id) { $('#reparseText').textContent = t('tw.reparseNone'); bar.style.width = '0%'; return; }
    const pct = job.total ? Math.round(job.done * 100 / job.total) : 0;
    bar.style.width = `${pct}%`;
    bar.className = job.status === 'done' ? 'done' : job.status === 'failed' ? 'error' : '';
    $('#reparseText').textContent = t('tw.reparseStatus', {
        status: t(`job.${job.status}`), done: fmtInt(job.done), total: fmtInt(job.total),
        updated: fmtInt(job.updated), failed: fmtInt(job.failed),
    });
}

async function pollJob() {
    clearTimeout(jobTimer);
    if (!auth.isAdmin()) return;
    try {
        const job = await apiGet('/api/reparse');
        renderJob(job);
        if (job?.status === 'running' && appRef.page?.id === 'dbtweak') jobTimer = setTimeout(pollJob, 2000);
    } catch { /* shown on next poll */ }
}

async function load(app = appRef) {
    renderAccess();
    if (!auth.isAdmin()) return;
    const key = app.scopeKey();
    if (key !== state.key) { state.key = key; state.page = 1; }
    await Promise.all([loadRecords(), pollJob()]);
}

function init(app) {
    appRef = app;
    auth.bindLoginForm({ user: $('#twUser'), pass: $('#twPass'), button: $('#btnTwLogin'), message: $('#twLoginMsg') });
    $('#btnTwSwitch').addEventListener('click', auth.logout);
    $('#twSearch').addEventListener('input', debounce(() => { state.page = 1; loadRecords(); }, 300));
    $('#twPageSize').addEventListener('change', () => { state.page = 1; loadRecords(); });
    $('#twRows').addEventListener('click', async e => {
        const logBtn = e.target.closest('[data-log]');
        if (logBtn) {
            try { await download(`/api/tweak/raw-log/${logBtn.dataset.log}`, `record_${logBtn.dataset.log}.txt`); }
            catch (err) { toast(err.message, 'bad'); }
            return;
        }
        const delBtn = e.target.closest('[data-del]');
        if (!delBtn || !confirm(t('tw.deleteOneConfirm', { id: delBtn.dataset.del, mac: delBtn.dataset.mac }))) return;
        try {
            await apiDelete(`/api/tweak/records/${delBtn.dataset.del}`);
            toast(t('tw.deleted', { n: 1 }));
            await Promise.all([loadRecords(), appRef.refreshOptions()]);
        } catch (err) { toast(err.message, 'bad'); }
    });
    $('#btnTwDeleteScope').addEventListener('click', async () => {
        const params = appRef.params();
        if (!params.date_from && !params.date_to && !params.work_order) { toast(t('tw.deleteScopeNeed'), 'bad'); return; }
        if (!confirm(t('tw.deleteScopeConfirm', { scope: $('#pageScope').textContent, n: fmtInt(state.total) }))) return;
        try {
            const res = await apiDelete(`/api/tweak/delete-scope${queryString(params)}`);
            toast(t('tw.deleted', { n: fmtInt(res.deleted) }));
            state.page = 1;
            await Promise.all([loadRecords(), appRef.refreshOptions()]);
        } catch (err) { toast(err.message, 'bad'); }
    });
    $('#btnReparse').addEventListener('click', async () => {
        if (!confirm(t('tw.reparseConfirm'))) return;
        try { renderJob(await apiPost('/api/reparse')); pollJob(); }
        catch (err) { toast(err.message, 'bad'); }
    });
    $('#btnReparseCancel').addEventListener('click', async () => {
        const job = await apiGet('/api/reparse').catch(() => null);
        if (job?.id) await apiPost(`/api/reparse/${job.id}/cancel`).catch(err => toast(err.message, 'bad'));
        pollJob();
    });
}

export default {
    id: 'dbtweak', scoped: true, init, load: app => load(app),
    onAuthChange: () => { if (appRef?.page?.id === 'dbtweak') load(); else renderAccess(); },
};
