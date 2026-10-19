// 上傳 Log (Log Uploader): zip → /api/log-upload/analyze → confirm work order + planned quantity
// (AI chat via /chat, or the form via /validate — same rules R1–R8, same hidden inputs)
// → /commit (NDJSON progress stream). Uploaders see and undo only their own batches.
// Server texts (checks, chat opening, errors) come in both languages; the AI replies in the UI language.
import { apiDelete, apiGet, apiPost, errorText, eventText, getToken, readNdjson, request, streamPost, toError } from '../core/api.js';
import * as auth from '../core/auth.js';
import { esc, fmtDateTime, fmtInt } from '../core/format.js';
import { getLang, localized, t } from '../core/i18n.js';
import { $, debounce } from '../core/ui.js';

const st = {
    uploadId: null, owner: null, facts: null, committedId: null, busy: false,
    validation: null, validateSeq: 0, mode: 'form', aiAvailable: false,
    chat: [], chatBusy: false, extractFails: 0, openings: {}, fileLabel: null, result: null,
};
const statusText = {};          // id → [text or () => text, kind], re-rendered on a language switch
let appRef;
let pendingHistoryUser = null;   // set by 帳號管理 →「看紀錄」before the options exist

function setStatus(id, text, kind = '') {
    statusText[id] = [text, kind];
    const el = $(id);
    el.textContent = typeof text === 'function' ? text() : text;
    el.className = `status-line ${kind}`;
}

/* ─── Access ─── */
function renderAccess() {
    const user = auth.currentUser();
    $('#luLogin').hidden = !!user;
    $('#luPanel').hidden = !user;
    const admin = auth.isAdmin();
    $('#luHistoryUser').hidden = !admin;
    $('#luHistoryFilterLabel').hidden = !admin;
    $('#luHistoryTitle').textContent = admin ? t('lu.history') : t('lu.historyMine');
    if (st.uploadId && !st.busy && (!user || user.username !== st.owner)) resetFlow();
}

/* ─── Step 1: choose + analyze ─── */
function resetFlow() {
    Object.assign(st, { uploadId: null, owner: null, facts: null, committedId: null, validation: null, chat: [], extractFails: 0,
                        openings: {}, fileLabel: null, result: null });
    st.validateSeq += 1;
    renderChat();
    $('#luChatInput').value = '';
    $('#luFile').value = '';
    $('#luFileInfo').textContent = t('lu.noFile');
    setStatus('#luAnalyzeStatus', '');
    $('#luFacts').hidden = true;
    $('#luStepConfirm').hidden = true;
    $('#luStepProgress').hidden = true;
    $('#luWo').value = '';
    $('#luQty').value = '';
    renderExtract();
    $('#luAck').checked = false;
    $('#luIssues').innerHTML = '';
    $('#luAckRow').hidden = true;
    setInputsDisabled(false);
    updateUploadButton();
}

function setInputsDisabled(disabled) {
    ['#luWo', '#luQty', '#luAck', '#btnLuReset', '#btnLuMode'].forEach(id => { $(id).disabled = disabled; });
    setChatDisabled(disabled || st.chatBusy);
    $('#luDrop').classList.toggle('disabled', disabled);
}

function uploadWithProgress(url, formData, onProgress) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', url);
        if (getToken()) xhr.setRequestHeader('X-Tweak-Token', getToken());
        xhr.upload.onprogress = e => { if (e.lengthComputable) onProgress(Math.round(e.loaded * 100 / e.total)); };
        xhr.onload = () => {
            let body = {};
            try { body = JSON.parse(xhr.responseText || '{}'); } catch { /* non-JSON error page */ }
            resolve({ status: xhr.status, body });
        };
        xhr.onerror = () => reject(new Error(t('err.network')));
        xhr.send(formData);
    });
}

async function analyze(file) {
    resetFlow();
    st.fileLabel = `${file.name}${t('common.dot')}${(file.size / 1048576).toFixed(1)} MB`;
    $('#luFileInfo').textContent = st.fileLabel;
    if (!/\.zip$/i.test(file.name)) { setStatus('#luAnalyzeStatus', () => t('lu.notZip'), 'error'); return; }
    st.busy = true;
    setInputsDisabled(true);
    setStatus('#luAnalyzeStatus', () => t('lu.uploading', { pct: 0 }));
    try {
        const fd = new FormData();
        fd.append('file', file);
        const { status, body } = await uploadWithProgress('/api/log-upload/analyze', fd, pct => {
            setStatus('#luAnalyzeStatus', () => (pct < 100 ? t('lu.uploading', { pct }) : t('lu.analyzing')));
        });
        if (status === 401) { auth.logout(); return; }
        if (status !== 200) { setStatus('#luAnalyzeStatus', () => errorText(body, status), 'error'); return; }
        st.uploadId = body.upload_id;
        st.owner = auth.currentUser()?.username;
        st.facts = body.facts;
        st.openings = { zh: body.chat_opening || '', en: body.chat_opening_en || '' };
        renderFacts(body.facts);
        setStatus('#luAnalyzeStatus', () => t('lu.analyzed'), 'ok');
        $('#luStepConfirm').hidden = false;
        renderWoPlaceholder();
        st.aiAvailable = await checkAi();
        setMode(st.aiAvailable ? 'chat' : 'form');
    } catch (err) {
        setStatus('#luAnalyzeStatus', err.message, 'error');
    } finally {
        st.busy = false;
        setInputsDisabled(false);
        updateUploadButton();
        if (st.uploadId) focusInput();
    }
}

function renderWoPlaceholder() {
    const inferred = st.facts?.inferred_wo;
    $('#luWo').placeholder = inferred ? t('lu.woPhInferred', { wo: inferred }) : t('lu.woPh');
}

function renderFacts(f) {
    const existing = Object.values(f.existing_by_wo || {}).reduce((a, b) => a + b, 0);
    const facts = [
        [t('lu.fact.stations'), f.stations.map(s => s.station).join(' / ')],
        [t('lu.fact.records'), fmtInt(f.total_records)],
        [t('lu.fact.units'), fmtInt(f.unique_mac1)],
        [t('lu.fact.pfs'), `${fmtInt(f.pass)} / ${fmtInt(f.fail)} / ${fmtInt(f.stop)}`],
        [t('lu.fact.invalid'), fmtInt(f.invalid)],
        [t('lu.fact.existing'), fmtInt(existing)],
        [t('lu.fact.period'), f.first_test ? `${f.first_test} ~ ${f.last_test}` : '—'],
    ];
    $('#luFactGrid').innerHTML = facts.map(([k, v]) =>
        `<div class="tile"><div class="tile-label">${esc(k)}</div><div class="tile-value small-value">${esc(v)}</div></div>`).join('');
    $('#luStationRows').innerHTML = f.stations.map(s => `<tr><td>${esc(s.station)}</td><td class="num">${fmtInt(s.records)}</td>
        <td class="num">${fmtInt(s.pass)}</td><td class="num">${fmtInt(s.fail)}</td><td class="num">${fmtInt(s.stop)}</td>
        <td class="num">${fmtInt(s.invalid)}</td></tr>`).join('');
    const invalid = f.invalid_items || [];
    $('#luInvalidBox').hidden = !invalid.length;
    $('#luInvalidSummary').textContent = t('lu.invalidSummary', { n: invalid.length });
    $('#luInvalidRows').innerHTML = invalid.map(i => {
        const start = i.start ? i.start.replace(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/, '$1-$2-$3 $4:$5:$6') : '—';
        return `<tr><td>${esc(i.station)}</td><td>${esc(start)}</td><td>${esc(i.mac1 || '—')}</td><td>${esc(i.result || '—')}</td><td>${esc(localized(i.reason, i.reason_en))}</td></tr>`;
    }).join('');
    $('#luFacts').hidden = false;
}

/* ─── Step 2 (form): server rules ─── */
async function validate() {
    if (!st.uploadId || st.committedId) return;
    const mine = ++st.validateSeq;
    try {
        const data = await apiPost('/api/log-upload/validate', {
            upload_id: st.uploadId, work_order: $('#luWo').value.trim(), planned_qty: $('#luQty').value.trim(),
        });
        if (mine !== st.validateSeq) return;
        st.validation = data;
        renderIssues(data.issues);
    } catch (err) {
        if (mine !== st.validateSeq) return;
        st.validation = null;
        renderIssues([{ level: 'error', message: err.message }]);
    }
    updateUploadButton();
}

function renderIssues(issues) {
    const icon = { error: '❌', warn: '⚠️', info: 'ℹ️' };
    const order = { error: 0, warn: 1, info: 2 };
    // In chat mode the assistant asks for missing values itself; "please enter …" errors would just repeat it.
    const missing = { R1: !$('#luWo').value.trim(), R2: !$('#luQty').value.trim() };
    $('#luIssues').innerHTML = issues
        .filter(i => !(st.mode === 'chat' && missing[i.code]))
        .sort((a, b) => order[a.level] - order[b.level])
        .map(i => `<li class="${esc(i.level)}">${icon[i.level] || ''} ${esc(localized(i.message, i.message_en))}</li>`).join('');
    const needsAck = issues.some(i => i.level === 'warn');
    $('#luAckRow').hidden = !needsAck;
    if (!needsAck) $('#luAck').checked = false;
}

function updateUploadButton() {
    const v = st.validation;
    $('#btnLuUpload').disabled = st.busy || st.chatBusy || !!st.committedId || !v || !v.can_upload
        || (v.needs_ack && !$('#luAck').checked);
}

/* ─── Step 2 (AI chat) ─── */
async function checkAi() {
    try { return !!(await apiGet('/api/llm-status')).connected; } catch { return false; }
}

function setMode(mode, note = '') {
    const prev = st.mode;
    st.mode = mode;
    const chat = mode === 'chat';
    $('#luChatPanel').hidden = !chat;
    $('#luFormPanel').hidden = chat;
    renderModeLabels();
    if (note) st.chat.push({ role: 'note', content: note });
    if (chat) {
        if (!st.chat.some(m => m.role === 'assistant')) st.chat.unshift({ role: 'assistant', content: localized(st.openings.zh, st.openings.en) || t('lu.woPh') });
        const wo = $('#luWo').value.trim(), qty = $('#luQty').value.trim();
        if (prev === 'form' && (wo || qty)) st.chat.push({ role: 'note', content: t('lu.formValues', { wo: wo || '—', qty: qty || '—' }) });
    }
    renderChat();
    renderExtract();
    if (!chat) validate();
    else if (st.validation) renderIssues(st.validation.issues);
    else $('#luIssues').innerHTML = '';
    updateUploadButton();
    if (!st.busy) focusInput();
}

function renderModeLabels() {
    const chat = st.mode === 'chat';
    $('#luModeLabel').textContent = chat ? t('lu.modeChat') : t('lu.modeForm');
    $('#luModeHint').textContent = chat ? t('lu.chatHint') : (st.aiAvailable ? '' : t('lu.aiOffline'));
    const btn = $('#btnLuMode');
    btn.textContent = chat ? t('lu.toForm') : t('lu.toChat');
    btn.hidden = !(st.aiAvailable || chat);
}

function focusInput() {
    if (st.mode === 'chat') $('#luChatInput').focus();
    else $($('#luWo').value.trim() ? '#luQty' : '#luWo').focus();
}

function bubble(role, text) {
    const div = document.createElement('div');
    div.className = `msg ${role}`;
    div.textContent = text;
    return div;
}

function renderChat() {
    const log = $('#luChatLog');
    log.innerHTML = '';
    st.chat.forEach(m => log.appendChild(bubble(m.role, m.content)));
    log.scrollTop = log.scrollHeight;
}

function renderExtract() {
    const wo = $('#luWo').value.trim(), qty = $('#luQty').value.trim();
    $('#luExtract').textContent = (wo || qty)
        ? t('lu.extract', { wo: wo || t('lu.notGiven'), qty: qty ? `${fmtInt(qty)} ${t('common.unit')}` : t('lu.notGiven') })
        : t('lu.extractNone');
}

function setChatDisabled(disabled) {
    $('#luChatInput').disabled = disabled;
    $('#btnLuChatSend').disabled = disabled;
}

async function chatSend() {
    const input = $('#luChatInput');
    const text = input.value.trim();
    if (!text || st.chatBusy || st.busy || !st.uploadId || st.committedId) return;
    input.value = '';
    st.chat.push({ role: 'user', content: text });
    renderChat();
    st.chatBusy = true;
    setChatDisabled(true);
    $('#btnLuMode').disabled = true;
    updateUploadButton();
    const log = $('#luChatLog');
    const typing = bubble('assistant typing', '');
    log.appendChild(typing);
    log.scrollTop = log.scrollHeight;
    let reply = null, fallback = null;
    try {
        await streamPost('/api/log-upload/chat', {
            upload_id: st.uploadId,
            messages: st.chat.filter(m => m.role !== 'note'),
            known: { work_order: $('#luWo').value.trim(), planned_qty: $('#luQty').value.trim() },
            lang: getLang(),
        }, ev => {
            if (ev.type === 'extract') {
                $('#luWo').value = ev.work_order || '';
                $('#luQty').value = ev.planned_qty != null ? String(ev.planned_qty) : '';
                st.extractFails = ev.extract_ok ? 0 : st.extractFails + 1;
                renderExtract();
            } else if (ev.type === 'validation') {
                st.validateSeq += 1;   // drop any in-flight form validation
                st.validation = ev;
                renderIssues(ev.issues);
            } else if (ev.type === 'delta') {
                typing.textContent += ev.text;
                log.scrollTop = log.scrollHeight;
            } else if (ev.type === 'done') {
                reply = ev.reply;
            } else if (ev.type === 'fallback') {
                fallback = localized(ev.reason, ev.reason_en);
            }
        });
        if (!reply && !fallback) fallback = t('lu.fallbackDefault');
    } catch (err) {
        fallback = err.message;
    } finally {
        typing.remove();
        st.chatBusy = false;
        setChatDisabled(false);
        $('#btnLuMode').disabled = false;
        if (!st.uploadId) {
            // flow was reset meanwhile (e.g. signed out)
        } else if (reply && st.extractFails < 2) {
            st.chat.push({ role: 'assistant', content: reply });
            renderChat();
            updateUploadButton();
            $('#luChatInput').focus();
        } else {
            if (reply) st.chat.push({ role: 'assistant', content: reply });
            st.aiAvailable = false;
            setMode('form', t('lu.fallback', { reason: fallback || t('lu.fallbackDefault') }));
        }
    }
}

/* ─── Step 3: commit + progress stream ─── */
async function commit() {
    const wo = $('#luWo').value.trim();
    const qty = $('#luQty').value.trim();
    if (!confirm(t('lu.confirm', { wo, qty }))) return;
    st.busy = true;
    setInputsDisabled(true);
    updateUploadButton();
    $('#luStepProgress').hidden = false;
    $('#luResultGrid').hidden = true;
    $('#luFailureBox').hidden = true;
    $('#btnLuRevert').hidden = true;
    $('#btnLuNext').hidden = true;
    const fill = $('#luProgressFill');
    fill.className = '';
    fill.style.width = '0%';
    setStatus('#luProgressText', () => t('lu.prepare'));
    $('#luStepProgress').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    let finished = false;
    try {
        const res = await request('/api/log-upload/commit', {
            method: 'POST', raw: true,
            body: { upload_id: st.uploadId, work_order: wo, planned_qty: qty, ack_warnings: $('#luAck').checked, mode: st.mode },
        });
        if (!res.ok) {
            const err = await toError(res);
            if (err.data.issues) { st.validation = err.data; renderIssues(err.data.issues); }
            fill.className = 'error';
            setStatus('#luProgressText', () => errorText(err.data, err.status), 'error');
            return;
        }
        await readNdjson(res, ev => { if (handleEvent(ev)) finished = true; });
        if (!finished) setStatus('#luProgressText', () => t('lu.disconnected'), 'error');
    } catch (err) {
        setStatus('#luProgressText', err.message, 'error');
    } finally {
        st.busy = false;
        if (!st.committedId) setInputsDisabled(false);
        updateUploadButton();
        loadHistory();
    }
}

/** Returns true once the stream reached a terminal event. */
function handleEvent(ev) {
    const fill = $('#luProgressFill');
    if (ev.type === 'start') {
        setStatus('#luProgressText', () => t('lu.start', { wo: ev.work_order, stations: ev.stations.length, total: fmtInt(ev.total) }));
    } else if (ev.type === 'split') {
        setStatus('#luProgressText', () => t('lu.split', { station: ev.station, records: fmtInt(ev.records) }));
    } else if (ev.type === 'progress') {
        fill.style.width = `${Math.round(ev.done * 100 / Math.max(ev.total, 1))}%`;
        setStatus('#luProgressText', () => t('lu.progress', { done: fmtInt(ev.done), total: fmtInt(ev.total), uploaded: fmtInt(ev.uploaded), skipped: fmtInt(ev.skipped), failed: fmtInt(ev.failed) }));
    } else if (ev.type === 'done' || ev.type === 'error') {
        st.committedId = st.uploadId;
        if (ev.type === 'done') {
            fill.style.width = '100%';
            fill.className = 'done';
            setStatus('#luProgressText', () => t('lu.done', { sec: ev.elapsed_sec }), 'ok');
            appRef.refreshOptions();
        } else {
            fill.className = 'error';
            setStatus('#luProgressText', () => t('lu.error', { message: eventText(ev) }), 'error');
        }
        renderResults(ev);
        $('#btnLuRevert').hidden = !(ev.uploaded > 0);
        $('#btnLuNext').hidden = false;
        return true;
    }
    return false;
}

function renderResults(ev) {
    st.result = ev;
    const cards = [[t('lu.res.new'), ev.uploaded, 'good-text'], [t('lu.res.skipped'), ev.skipped, ''],
                   [t('lu.res.invalid'), ev.invalid, 'warn-text'], [t('lu.res.failed'), ev.failed, ev.failed ? 'bad-text' : '']];
    $('#luResultGrid').innerHTML = cards.map(([k, v, cls]) =>
        `<div class="tile"><div class="tile-label">${esc(k)}</div><div class="tile-value ${cls}">${fmtInt(v)}</div></div>`).join('');
    $('#luResultGrid').hidden = false;
    const failures = ev.failures || [];
    $('#luFailureBox').hidden = !failures.length;
    $('#luFailureSummary').textContent = t('lu.failSummary', { n: failures.length });
    $('#luFailureRows').innerHTML = failures.map(f => `<tr><td>${esc(f.station)}</td><td>${esc(f.filename)}</td><td>${esc(f.error)}</td></tr>`).join('');
}

async function revert(uploadId, label = '') {
    if (!uploadId || !confirm(t('lu.revertConfirm', { label: label ? t('common.paren', { text: label }) : '' }))) return false;
    try {
        const data = await apiDelete(`/api/log-upload/${uploadId}`);
        if (uploadId === st.committedId) setStatus('#luProgressText', () => t('lu.reverted', { n: fmtInt(data.reverted) }), 'ok');
        appRef.refreshOptions();
        loadHistory();
        return true;
    } catch (err) {
        alert(err.message);
        return false;
    }
}

/* ─── History ─── */
const STATUS = { done: 'done', failed: 'failed', reverted: 'reverted', running: 'running' };

async function loadUploaderOptions() {
    try {
        const data = await apiGet('/api/admin/users');
        const sel = $('#luHistoryUser');
        const current = pendingHistoryUser ?? sel.value;
        pendingHistoryUser = null;
        sel.innerHTML = '';
        sel.appendChild(new Option(t('common.all'), ''));
        sel.appendChild(new Option(`${data.admin.username}${t('lu.adminRole')}`, data.admin.username));
        data.items.forEach(u => sel.appendChild(new Option(auth.displayName(u), u.username)));
        sel.value = [...sel.options].some(o => o.value === current) ? current : '';
        return data;
    } catch { return null; }
}

async function loadHistory() {
    if (!auth.isSignedIn()) return;
    const tbody = $('#luHistoryRows');
    const filterUser = auth.isAdmin() ? $('#luHistoryUser').value : '';
    try {
        const data = await apiGet(`/api/log-upload/history?limit=20${filterUser ? `&username=${encodeURIComponent(filterUser)}` : ''}`);
        if (!data.items.length) { tbody.innerHTML = `<tr><td colspan="10" class="muted">${esc(t('lu.none'))}</td></tr>`; return; }
        const me = auth.currentUser();
        tbody.innerHTML = data.items.map(r => {
            const canRevert = (r.status === 'done' || r.status === 'failed') && r.uploaded > 0 && (auth.isAdmin() || r.username === me.username);
            const who = r.display_name && r.display_name !== r.username ? t('common.nameWithId', { name: r.display_name, id: r.username }) : (r.username || '—');
            const tip = r.status === 'reverted' && r.reverted_by ? t('lu.revertedBy', { user: r.reverted_by, time: fmtDateTime(r.reverted_at) }) : (r.error || '');
            return `<tr>
                <td class="nowrap">${esc(fmtDateTime(r.created_at))}</td>
                <td class="nowrap">${esc(r.work_order || '—')}</td>
                <td class="num">${r.planned_qty != null ? fmtInt(r.planned_qty) : '—'}</td>
                <td class="truncate" title="${esc(r.zip_name || '')}">${esc(r.zip_name || '—')}</td>
                <td class="num">${r.uploaded != null ? fmtInt(r.uploaded) : '—'}</td>
                <td class="num">${r.skipped != null ? fmtInt(r.skipped) : '—'}</td>
                <td class="num">${r.failed != null ? fmtInt(r.failed) : '—'}</td>
                <td class="nowrap"><span class="badge ${STATUS[r.status] || 'muted'}" title="${esc(tip)}">${esc(t(`lu.status.${r.status}`))}</span>${r.mode === 'chat' ? ` <span title="${esc(t('lu.viaChat'))}">🤖</span>` : ''}</td>
                <td class="nowrap">${esc(who)}</td>
                <td>${canRevert ? `<button class="btn small" type="button" data-revert="${esc(r.id)}" data-label="${esc(r.work_order || '')}">${esc(t('lu.revert'))}</button>` : ''}</td>
            </tr>`;
        }).join('');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="10" class="bad-text">${esc(err.message)}</td></tr>`;
    }
}

/* ─── Wiring ─── */
function init(app) {
    appRef = app;
    auth.bindLoginForm({ user: $('#luUser'), pass: $('#luPass'), button: $('#btnLuLogin'), message: $('#luLoginMsg') });
    const drop = $('#luDrop');
    drop.addEventListener('click', () => { if (!st.busy) $('#luFile').click(); });
    drop.addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === ' ') && !st.busy) { e.preventDefault(); $('#luFile').click(); } });
    drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('dragover'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
    drop.addEventListener('drop', e => {
        e.preventDefault();
        drop.classList.remove('dragover');
        const file = e.dataTransfer.files?.[0];
        if (file && !st.busy) analyze(file);
    });
    $('#luFile').addEventListener('change', e => { const file = e.target.files?.[0]; if (file) analyze(file); });
    const debounced = debounce(validate, 300);
    ['#luWo', '#luQty'].forEach(id => $(id).addEventListener('input', () => { st.validation = null; updateUploadButton(); debounced(); }));
    $('#luAck').addEventListener('change', updateUploadButton);
    $('#btnLuReset').addEventListener('click', resetFlow);
    $('#btnLuNext').addEventListener('click', () => { resetFlow(); $('#luDrop').focus(); });
    $('#btnLuMode').addEventListener('click', () => { if (!st.chatBusy) setMode(st.mode === 'chat' ? 'form' : 'chat'); });
    $('#btnLuChatSend').addEventListener('click', chatSend);
    $('#luChatInput').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); chatSend(); } });
    $('#btnLuUpload').addEventListener('click', commit);
    $('#btnLuRevert').addEventListener('click', async () => { if (await revert(st.committedId)) $('#btnLuRevert').hidden = true; });
    $('#btnLuHistoryRefresh').addEventListener('click', loadHistory);
    $('#luHistoryUser').addEventListener('change', loadHistory);
    $('#luHistoryRows').addEventListener('click', async e => {
        const btn = e.target.closest('[data-revert]');
        if (btn && await revert(btn.dataset.revert, btn.dataset.label) && btn.dataset.revert === st.committedId) $('#btnLuRevert').hidden = true;
    });
    resetFlow();
}

async function load() {
    renderAccess();
    if (!auth.isSignedIn()) return;
    if (auth.isAdmin()) await loadUploaderOptions();
    await loadHistory();
}

/** Language switched: re-render what this page drew (the conversation so far stays as written). */
function onLangChange() {
    renderAccess();
    $('#luFileInfo').textContent = st.fileLabel || t('lu.noFile');
    for (const [id, [text, kind]] of Object.entries(statusText)) setStatus(id, text, kind);
    if (st.facts) { renderFacts(st.facts); renderWoPlaceholder(); }
    renderModeLabels();
    renderExtract();
    if (st.validation) renderIssues(st.validation.issues);
    if (st.result) renderResults(st.result);
    if (appRef?.page?.id === 'logupload' && st.uploadId && !st.committedId) loadHistory();   // load() is skipped while busy
}

export default {
    id: 'logupload', scoped: false, init, load, onLangChange,
    onAuthChange: () => { renderAccess(); if (appRef?.page?.id === 'logupload') load(); },
    isBusy: () => st.busy || st.chatBusy || (!!st.uploadId && !st.committedId),
    /** Used by 帳號管理 →「看紀錄」. */
    showHistoryFor(username) { pendingHistoryUser = username; },
    loadUploaderOptions,
};
