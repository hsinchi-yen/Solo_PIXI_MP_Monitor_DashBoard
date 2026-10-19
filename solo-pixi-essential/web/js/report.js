// AI 品質報告 window (one for the whole app).
// Numbers come from the server's facts table; the AI writes only the sentences, in both
// languages. Opening shows the stored report in the UI language; it is generated when missing (streamed:
// numbers first, then each language as it finishes). Text is rendered as text, never HTML.
import { ApiError, apiGet, download, eventText, streamPost } from './core/api.js';
import { esc, fmtDateTime, fmtInt, fmtPct, fmtSigned, retryLevel, yieldLevel } from './core/format.js';
import { getLang, joinList, t, tLang } from './core/i18n.js';
import { $, $$, toast } from './core/ui.js';

const st = {
    wo: null, session: 0, lang: 'en', extra: [], row: null, facts: null, reports: {}, checks: {}, langErrors: {},
    generating: false, llm: false, ask: [], asking: false, pollTimer: null,
};

function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
}

/* ─── Rendering ─── */
function renderHeader() {
    $('#rpTitle').textContent = st.wo ? t('rp.title', { wo: st.wo }) : t('rp.titleGeneric');
    const risk = st.facts?.risk?.level;
    const badge = $('#rpRisk');
    badge.hidden = !risk;
    if (risk) {
        badge.className = `badge ${risk}`;
        badge.textContent = t(`rp.risk.${risk}`);
    }
    const at = st.row?.finished_at;
    $('#rpGenerated').textContent = at ? `${t('rp.generated', { time: fmtDateTime(at) })}${t('common.dot')}${t(`rp.trigger.${st.row.trigger || 'manual'}`)}` : '';
    $$('#rpLang button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.lang === st.lang)));
    const has = !!st.reports[st.lang];
    $('#btnRpCopy').disabled = !has;
    $('#btnRpDownload').disabled = !st.row?.id || st.generating;
    $('#btnRpRegen').disabled = st.generating || !st.llm;
    $('#btnRpCompare').disabled = st.generating || !st.row?.id;
    $('#btnRpAsk').disabled = !st.llm || !st.row?.id || st.asking;
    $('#rpAskInput').disabled = !st.llm || !st.row?.id;
    $('#rpAskInput').placeholder = st.row?.id ? t('rp.askPh') : t('rp.askNeedReport');
}

function banner(messages) {
    const box = $('#rpBanner');
    box.innerHTML = '';
    // text may be a function, so a language switch re-renders the banner in the new language
    messages.filter(Boolean).forEach(([kind, text]) => {
        const n = el('div', `notice ${kind} mb`, typeof text === 'function' ? text() : text);
        box.appendChild(n);
    });
}

function factTile(label, value, cls = '', sub = '') {
    const d = el('div', 'rp-fact');
    d.append(el('div', 'k', label), el('div', `v ${cls}`, value));
    if (sub) d.append(el('div', 'sub', sub));
    return d;
}

function renderFacts() {
    const box = $('#rpFacts');
    box.innerHTML = '';
    const f = st.facts;
    if (!f) return;
    box.append(
        factTile(t('rp.f.units'), fmtInt(f.units)),
        factTile(t('rp.f.yield'), fmtPct(f.yield_pct), `v-${yieldLevel(f.yield_pct)}`),
        factTile(t('rp.f.retry'), fmtPct(f.retry_rate), `v-${retryLevel(f.retry_rate)}`),
        factTile(t('rp.f.fail'), `${fmtInt(f.fail)} / ${fmtInt(f.stop)}`),
    );
    if (f.planned) {
        box.append(factTile(t('rp.f.planned'), fmtInt(f.planned.planned_qty)),
                   factTile(t('rp.f.aligned'), fmtPct(f.planned.aligned_yield_pct), `v-${yieldLevel(f.planned.aligned_yield_pct)}`));
    }
    if (f.previous_avg?.yield_pct != null) {
        box.append(factTile(t('rp.f.prev'), fmtPct(f.previous_avg.yield_pct), '',
                            t('rp.prevDelta', { delta: fmtSigned(f.previous_avg.yield_change) })));
    }
    const p = f.period || {};
    box.append(factTile(t('rp.f.period'), `${fmtDateTime(p.first_test)} ~ ${fmtDateTime(p.last_test)}`, 'small-value'));
}

// The facts name an empty fail category 未分類 (kept as is: it is part of the stored data hash).
const categoryName = c => (c === '未分類' ? t('common.uncategorized') : c);

function renderCauses() {
    const box = $('#rpCauses');
    box.innerHTML = '';
    const causes = st.facts?.top_causes || [];
    if (!causes.length) return;
    const sec = el('div', 'rp-section');
    sec.appendChild(el('h4', '', t('rp.causes')));
    const wrap = el('div', 'table-wrap');
    wrap.innerHTML = `<table><thead><tr><th>${esc(t('rp.col.code'))}</th><th>${esc(t('rp.col.step'))}</th><th>${esc(t('rp.col.category'))}</th><th class="num">${esc(t('rp.col.count'))}</th></tr></thead><tbody>${
        causes.map(c => `<tr><td>${esc(c.error_code)}</td><td>${esc(c.step)}</td><td>${esc(categoryName(c.category))}</td><td class="num">${fmtInt(c.count)}</td></tr>`).join('')}</tbody></table>`;
    sec.appendChild(wrap);
    box.appendChild(sec);
}

function pending(text) {
    const d = el('div', 'rp-pending');
    d.append(el('span', 'spinner'), el('span', '', text));
    return d;
}

function renderBody() {
    const body = $('#rpBody');
    body.innerHTML = '';
    const report = st.reports[st.lang];
    if (!report) {
        if (st.langErrors[st.lang]) body.appendChild(el('div', 'notice bad', t('rp.langFailed')));
        else if (st.generating) body.appendChild(pending(st.facts ? t('rp.writing') : t('rp.preparing')));
        else if (!st.row) {
            const box = el('div', 'state-msg');
            box.appendChild(el('div', '', t('rp.none')));
            const btn = el('button', 'btn primary', t('rp.generateNow'));
            btn.disabled = !st.llm;
            btn.addEventListener('click', () => generate(true));
            box.appendChild(btn);
            body.appendChild(box);
        }
        return;
    }
    const sec = (title, node) => { const s = el('div', 'rp-section'); s.append(el('h4', '', title), node); return s; };
    const list = items => { const ol = el('ol', 'rp-list'); items.forEach(x => ol.appendChild(el('li', '', x))); return ol; };
    body.append(
        sec(t('rp.summary'), el('div', 'rp-summary', report.summary)),
        sec(t('rp.findings'), list(report.findings || [])),
        sec(t('rp.recs'), list(report.recommendations || [])),
    );
}

function renderBanners(extra = []) {
    const check = st.checks[st.lang];
    banner([
        ...extra,
        !st.llm && ['warn', t('rp.llmOff')],
        check && check.ok === false && ['warn', t('rp.numberWarn', { nums: joinList(check.unknown) })],
    ]);
}

function renderAll(extra = []) {
    st.extra = extra;
    paint();
}

function paint() {
    renderHeader();
    renderBanners(st.extra);
    renderFacts();
    renderBody();
    renderCauses();
}

/* ─── Loading / generating ─── */
function setRow(row) {
    st.row = row;
    st.facts = row?.facts || st.facts;
    st.reports = { zh: row?.report_zh || null, en: row?.report_en || null };
    st.checks = row?.checks || {};
    st.langErrors = {};
}

async function loadLatest(session) {
    const data = await apiGet(`/api/reports/${encodeURIComponent(st.wo)}`);
    if (session !== st.session) return null;
    setRow(data.report);
    st.llm = data.llm?.connected ?? st.llm;
    return data;
}

async function open(wo) {
    const session = ++st.session;
    clearTimeout(st.pollTimer);
    Object.assign(st, { wo, lang: getLang(), row: null, facts: null, reports: {}, checks: {}, langErrors: {}, generating: false, ask: [], asking: false });
    $('#rpAskLog').innerHTML = '';
    $('#rpCompareBox').hidden = true;
    $('#reportModal').hidden = false;
    renderAll();
    $('#rpBody').replaceChildren(pending(t('common.loading')));
    try {
        const data = await loadLatest(session);
        if (!data) return;
        if (!data.has_data && !data.report) { renderAll([['bad', () => t('rp.noData')]]); return; }
        if (data.pending && !data.report) { renderAll([['', () => t('rp.queued')]]); poll(session); return; }
        if (!data.report && st.llm) { generate(false); return; }
        renderAll([data.stale && ['warn', () => t('rp.stale')], data.pending && ['', () => t('rp.queued')]]);
        if (data.pending) poll(session);
    } catch (err) {
        if (session === st.session) renderAll([['bad', err.message]]);
    }
}

function poll(session) {
    st.pollTimer = setTimeout(async () => {
        if (session !== st.session || $('#reportModal').hidden) return;
        try {
            const data = await loadLatest(session);
            if (!data) return;
            if (data.pending) { renderAll([['', () => t('rp.queued')]]); poll(session); }
            else renderAll([data.stale && ['warn', () => t('rp.stale')]]);
        } catch { poll(session); }
    }, 5000);
}

async function generate(force) {
    if (st.generating || !st.wo) return;
    const session = st.session;
    st.generating = true;
    st.reports = {};
    st.checks = {};
    st.langErrors = {};
    renderAll();
    let failure = null;
    try {
        await streamPost(`/api/reports/${encodeURIComponent(st.wo)}/generate?force=${force ? 'true' : 'false'}`, null, ev => {
            if (session !== st.session) return;
            if (ev.type === 'facts') { st.facts = ev.facts; renderAll(); }
            else if (ev.type === 'report') { st.reports[ev.lang] = ev.report; st.checks[ev.lang] = ev.check; renderAll(); }
            else if (ev.type === 'lang_error') { st.langErrors[ev.lang] = true; renderAll(); }
            else if (ev.type === 'error') failure = ev;
        });
    } catch (err) {
        failure = { message: err.message };
    }
    if (session !== st.session) return;
    st.generating = false;
    if (failure) {
        renderAll([['bad', () => (failure.code === 'busy' ? t('rp.queued') : t('rp.failed', { error: eventText(failure) }))]]);
        if (failure.code === 'busy') poll(session);
        return;
    }
    try { await loadLatest(session); } catch { /* keep what was streamed */ }
    renderAll();
}

/* ─── Actions ─── */
function plainText() {
    const r = st.reports[st.lang];
    if (!r) return '';
    const tr = key => tLang(st.lang, key);      // copied text follows the report's language
    return [tLang(st.lang, 'rp.title', { wo: st.wo }), '',
        `${tr('rp.summary')}: ${r.summary}`, '',
        `${tr('rp.findings')}:`, ...r.findings.map((x, i) => `${i + 1}. ${x}`), '',
        `${tr('rp.recs')}:`, ...r.recommendations.map((x, i) => `${i + 1}. ${x}`)].join('\n');
}

async function copy() {
    const text = plainText();
    try {
        if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
        else {
            // Plain http on the intranet: the async clipboard API is unavailable.
            const ta = Object.assign(document.createElement('textarea'), { value: text });
            ta.setAttribute('readonly', '');
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            ta.remove();
        }
        toast(t('rp.copied'));
    } catch (err) { toast(err.message, 'bad'); }
}

async function compare() {
    const box = $('#rpCompareBox');
    box.hidden = false;
    box.innerHTML = '';
    try {
        const d = await apiGet(`/api/reports/${encodeURIComponent(st.wo)}/compare`);
        box.appendChild(el('h4', '', t('rp.cmp.title', { from: fmtDateTime(d.base.finished_at), to: fmtDateTime(d.target.finished_at) })));
        if (!d.changes.length && !d.causes_added.length && !d.causes_removed.length && !d.causes_changed.length) {
            box.appendChild(el('div', 'muted', t('rp.cmp.none')));
            return;
        }
        if (d.changes.length) {
            const wrap = el('div', 'table-wrap rp-compare');
            wrap.innerHTML = `<table><thead><tr><th>${esc(t('rp.cmp.field'))}</th><th class="num">${esc(t('rp.cmp.before'))}</th><th class="num">${esc(t('rp.cmp.after'))}</th><th class="num">${esc(t('rp.cmp.delta'))}</th></tr></thead><tbody>${
                d.changes.map(c => `<tr><td>${esc(t(`rp.cmp.${c.field}`))}</td><td class="num">${esc(c.before ?? '—')}</td><td class="num">${esc(c.after ?? '—')}</td><td class="num ${c.delta > 0 ? 'up' : c.delta < 0 ? 'down' : ''}">${c.delta != null ? esc(fmtSigned(c.delta)) : '—'}</td></tr>`).join('')}</tbody></table>`;
            box.appendChild(wrap);
        }
        if (d.causes_added.length) box.appendChild(el('div', 'mt-s', t('rp.cmp.added', { codes: joinList(d.causes_added) })));
        if (d.causes_removed.length) box.appendChild(el('div', 'mt-s', t('rp.cmp.removed', { codes: joinList(d.causes_removed) })));
    } catch (err) {
        box.appendChild(el('div', 'muted', err instanceof ApiError && err.code === 'not_enough_reports' ? t('rp.cmp.need2') : err.message));
    }
}

async function ask() {
    const input = $('#rpAskInput');
    const text = input.value.trim();
    if (!text || st.asking || !st.row?.id) return;
    input.value = '';
    const session = st.session;
    st.ask.push({ role: 'user', content: text });
    const log = $('#rpAskLog');
    log.appendChild(el('div', 'msg user', text));
    const answer = el('div', 'msg assistant typing', '');
    log.appendChild(answer);
    log.scrollTop = log.scrollHeight;
    st.asking = true;
    renderHeader();
    let reply = '';
    try {
        await streamPost(`/api/reports/${encodeURIComponent(st.wo)}/ask`, { messages: st.ask.slice(-10), lang: getLang() }, ev => {
            if (session !== st.session) return;
            if (ev.type === 'delta') { answer.textContent += ev.text; log.scrollTop = log.scrollHeight; }
            else if (ev.type === 'done') reply = ev.reply;
            else if (ev.type === 'error') throw new Error(eventText(ev));
        });
    } catch (err) {
        answer.textContent = err.message || t('rp.askFailed');
    }
    answer.classList.remove('typing');
    if (reply) st.ask.push({ role: 'assistant', content: reply });
    st.asking = false;
    renderHeader();
    input.focus();
}

function close() {
    $('#reportModal').hidden = true;
    st.session += 1;
    clearTimeout(st.pollTimer);
}

function init() {
    $('#btnRpClose').addEventListener('click', close);
    $('#reportModal').addEventListener('mousedown', e => { if (e.target.id === 'reportModal') close(); });
    $$('#rpLang button').forEach(b => b.addEventListener('click', () => { st.lang = b.dataset.lang; renderAll(); }));
    $('#btnRpCopy').addEventListener('click', copy);
    $('#btnRpDownload').addEventListener('click', () =>
        download(`/api/reports/${encodeURIComponent(st.wo)}/markdown?lang=${st.lang}${st.row?.id ? `&id=${st.row.id}` : ''}`, `AI_Report_${st.wo}_${st.lang}.md`)
            .catch(err => toast(err.message, 'bad')));
    $('#btnRpRegen').addEventListener('click', () => generate(true));
    $('#btnRpCompare').addEventListener('click', compare);
    $('#btnRpAsk').addEventListener('click', ask);
    $('#rpAskInput').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); ask(); } });
}

function setLlm(ok) {
    st.llm = ok;
    $('#navAiReport').title = ok ? '' : t('rp.llmOff');
    if (!$('#reportModal').hidden) renderHeader();
}

/** UI language switched: show the report in the new language too. */
function onLangChange() {
    setLlm(st.llm);
    if ($('#reportModal').hidden) return;
    st.lang = getLang();
    paint();
    if (!$('#rpCompareBox').hidden) compare();
}

export default { init, open, close, setLlm, onLangChange };
