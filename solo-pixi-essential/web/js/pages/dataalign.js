// 計畫數對齊: planned quantity per work order (alignment_targets) vs units tested; aligned yield.
// All numbers come from GET /api/alignment; saving needs a signed-in account.
import { apiGet, apiPost } from '../core/api.js';
import * as auth from '../core/auth.js';
import { esc, fmtDateTime, fmtInt, fmtPct, yieldLevel } from '../core/format.js';
import { t } from '../core/i18n.js';
import { queryString } from '../core/scope.js';
import { $, $$, tableState, toast } from '../core/ui.js';

let seq = 0;
let appRef;

const tile = (label, value, cls = '') =>
    `<div class="tile"><div class="tile-label">${esc(label)}</div><div class="tile-value ${cls}">${esc(value)}</div></div>`;

function dirtyCount() { return $$('#alRows .target-input.dirty').length; }

function renderStatus() {
    const st = $('#alStatus');
    const n = dirtyCount();
    if (!auth.isSignedIn()) st.textContent = t('al.loginHint');
    else st.textContent = n ? t('al.unsaved', { n }) : '';
    $('#btnAlignSave').disabled = !auth.isSignedIn() || !n;
    $$('#alRows .target-input').forEach(i => { i.disabled = !auth.isSignedIn(); });
}

async function load(app = appRef) {
    const mine = ++seq;
    const tbody = $('#alRows');
    try {
        const data = await apiGet(`/api/alignment${queryString(app.params())}`);
        if (mine !== seq) return;
        const tot = data.totals;
        $('#alTiles').innerHTML = [
            tile(t('al.workOrders'), fmtInt(tot.work_orders)),
            tile(t('al.gap'), tot.gap ? `+${fmtInt(tot.gap)}` : '0', tot.gap ? 'bad-text' : 'good-text'),
            tile(t('al.stopToPass'), fmtInt(tot.stop_to_pass)),
            tile(t('al.alignedYield'), fmtPct(tot.aligned_yield), tot.aligned_yield != null ? `${yieldLevel(tot.aligned_yield)}-text` : ''),
        ].join('');
        if (!data.items.length) { tableState(tbody, 12, 'empty'); renderStatus(); return; }
        tbody.innerHTML = data.items.map(r => `<tr>
            <td class="nowrap"><strong>${esc(r.work_order)}</strong></td>
            <td class="nowrap">${esc(fmtDateTime(r.last_test))}</td>
            <td class="num">${fmtInt(r.total)}</td>
            <td class="num good-text">${fmtInt(r.passed)}</td>
            <td class="num bad-text">${fmtInt(r.failed)}</td>
            <td class="num warn-text">${fmtInt(r.stopped)}</td>
            <td class="num"><input class="input target-input" type="number" min="1" step="1" inputmode="numeric"
                data-wo="${esc(r.work_order)}" data-orig="${r.target_total ?? ''}" value="${r.target_total ?? ''}" placeholder="—"
                aria-label="${esc(`${t('al.col.target')} ${r.work_order}`)}"></td>
            <td class="num ${r.gap ? 'bad-text' : ''}">${r.gap ? `+${fmtInt(r.gap)}` : '—'}</td>
            <td class="num">${r.stop_to_pass ? fmtInt(r.stop_to_pass) : '—'}</td>
            <td class="num good-text">${fmtInt(r.passed)}</td>
            <td class="num bad-text">${fmtInt(r.aligned_fail)}</td>
            <td class="num"><span class="badge ${yieldLevel(r.aligned_yield)}">${fmtPct(r.aligned_yield)}</span></td>
        </tr>`).join('');
        renderStatus();
    } catch (err) {
        if (mine !== seq) return;
        $('#alTiles').innerHTML = '';
        tableState(tbody, 12, 'error', t('common.loadFailedMsg', { error: err.message }), () => load());
    }
}

async function save() {
    const payload = {};
    for (const input of $$('#alRows .target-input.dirty')) {
        const v = input.value.trim();
        if (v !== '' && !(Number.isInteger(Number(v)) && Number(v) > 0)) {
            input.focus();
            toast(t('al.badTarget'), 'bad');
            return;
        }
        payload[input.dataset.wo] = v === '' ? null : Number(v);
    }
    const btn = $('#btnAlignSave');
    btn.disabled = true;
    try {
        await apiPost('/api/alignment-targets', payload);
        toast(t('al.saved', { n: Object.keys(payload).length }));
        await load();
    } catch (err) {
        $('#alStatus').textContent = t('al.saveFailed', { error: err.message });
        btn.disabled = false;
    }
}

function init(app) {
    appRef = app;
    $('#alRows').addEventListener('input', e => {
        const input = e.target.closest('.target-input');
        if (!input) return;
        input.classList.toggle('dirty', input.value.trim() !== input.dataset.orig);
        renderStatus();
    });
    $('#btnAlignSave').addEventListener('click', save);
}

export default {
    id: 'dataalign', scoped: true, init, load: app => load(app),
    onAuthChange: () => { if (appRef?.page?.id === 'dataalign') renderStatus(); },
    isBusy: () => dirtyCount() > 0,
    onLangChange: () => { if (appRef?.page?.id === 'dataalign') renderStatus(); },
};
