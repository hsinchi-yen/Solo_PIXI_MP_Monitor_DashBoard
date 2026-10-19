// 失敗清單: FAIL / STOP records, paged on the server, with search and raw-log download.
import { apiGet, download } from '../core/api.js';
import { esc, fmtDateTime, fmtInt } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $, debounce, tableState, toast } from '../core/ui.js';

const PAGE_SIZE = 50;
const state = { page: 1, key: '' };
let seq = 0;
let appRef;

export function renderPager(el, data, onPage) {
    el.innerHTML = '';
    if (!data.total) return;
    const info = document.createElement('span');
    info.textContent = t('common.pageInfo', { page: data.page, pages: data.pages, total: fmtInt(data.total) });
    const prev = document.createElement('button');
    prev.className = 'btn small';
    prev.textContent = t('common.prev');
    prev.disabled = data.page <= 1;
    prev.addEventListener('click', () => onPage(data.page - 1));
    const next = document.createElement('button');
    next.className = 'btn small';
    next.textContent = t('common.next');
    next.disabled = data.page >= data.pages;
    next.addEventListener('click', () => onPage(data.page + 1));
    el.append(info, prev, next);
}

async function load(app = appRef) {
    const key = app.scopeKey();
    if (key !== state.key) { state.key = key; state.page = 1; }
    const mine = ++seq;
    const tbody = $('#failRows');
    const params = { ...app.params(), page: state.page, page_size: PAGE_SIZE };
    const search = $('#failSearch').value.trim();
    if (search) params.search = search;
    try {
        const data = await apiGet(`/api/fails${queryString(params)}`);
        if (mine !== seq) return;
        state.page = data.page;
        if (!data.items.length) tableState(tbody, 9, 'empty');
        else {
            tbody.innerHTML = data.items.map(r => `<tr>
                <td class="nowrap">${esc(fmtDateTime(r.start_time))}</td>
                <td class="nowrap">${esc(r.work_order || '—')}</td>
                <td><code>${esc(r.mac1)}</code></td>
                <td><span class="badge ${r.result === 'FAIL' ? 'fail' : 'stop'}">${esc(r.result)}</span></td>
                <td>${esc(r.fail_step_name || '—')}</td>
                <td class="nowrap">${esc(r.fail_error_code || '—')}</td>
                <td>${esc(r.fail_category || '—')}</td>
                <td class="truncate" title="${esc(r.fail_message || '')}">${esc(r.fail_message || '—')}</td>
                <td><button class="btn small" type="button" data-log="${r.id}">${esc(t('fails.download'))}</button></td>
            </tr>`).join('');
        }
        renderPager($('#failPager'), data, p => { state.page = p; load(); });
    } catch (err) {
        if (mine !== seq) return;
        tableState(tbody, 9, 'error', t('common.loadFailedMsg', { error: err.message }), () => load());
        $('#failPager').innerHTML = '';
    }
}

function init(app) {
    appRef = app;
    $('#failSearch').addEventListener('input', debounce(() => { state.page = 1; load(); }, 300));
    $('#failRows').addEventListener('click', async e => {
        const btn = e.target.closest('[data-log]');
        if (!btn) return;
        try { await download(`/api/raw-log/${btn.dataset.log}`, `record_${btn.dataset.log}.txt`); }
        catch (err) { toast(err.message, 'bad'); }
    });
}

export default { id: 'fails', scoped: true, init, load: app => load(app) };
