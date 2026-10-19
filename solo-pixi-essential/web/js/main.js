// App shell: hash routing, the filter bar, data freshness, theme, clock, idle rotation.
import { apiGet, apiPost, onApiStatus } from './core/api.js';
import * as auth from './core/auth.js';
import * as charts from './core/charts.js';
import { fmtDate, fmtShortDate, fmtTime, setThresholds } from './core/format.js';
import { applyI18n, t } from './core/i18n.js';
import { presetRange, scopeKey, scopeParams } from './core/scope.js';
import { $, $$, initEscapeClose, initFormModal, toast, closeFormModal } from './core/ui.js';
import report from './report.js';
import overview from './pages/overview.js';
import workorders from './pages/workorders.js';
import fails from './pages/fails.js';
import measurements from './pages/measurements.js';
import failanalysis from './pages/failanalysis.js';
import dbtweak from './pages/dbtweak.js';
import dataalign from './pages/dataalign.js';
import logupload from './pages/logupload.js';
import accounts from './pages/accounts.js';

const PAGES = [overview, workorders, fails, measurements, failanalysis, dbtweak, dataalign, logupload, accounts];
const byId = Object.fromEntries(PAGES.map(p => [p.id, p]));
const IDLE_MS = 5 * 60 * 1000;
const ROTATE_MS = 30 * 1000;
const ROTATION = ['overview', 'workorders'];

export const app = {
    page: null,
    scope: { range: 'all', workOrder: '', from: '', to: '' },
    options: { work_orders: [] },
    meta: null,
    params() { return scopeParams(this.scope); },
    scopeKey() { return scopeKey(this.scope); },
    latestWorkOrder() { return this.options.work_orders[0]?.work_order || ''; },
    openReport(wo) { report.open(wo); },
    reload() { return loadPage(); },
    refreshOptions: () => loadOptions(),
};

/* ─── Routing ─── */
function pageFromHash() {
    const id = location.hash.replace(/^#/, '').split('?')[0];
    return byId[id] ? id : 'overview';
}

async function show(id) {
    const page = byId[id];
    app.page = page;
    $$('.page').forEach(s => { s.hidden = s.id !== `page-${id}`; });
    $$('.nav-item[data-page]').forEach(a => {
        if (a.dataset.page === id) a.setAttribute('aria-current', 'page');
        else a.removeAttribute('aria-current');
    });
    $('#pageTitle').textContent = t(`page.${id}`);
    document.title = `${t(`page.${id}`)} · ${t('app.title')}`;
    $('#filterBar').hidden = !page.scoped;
    updateScopeLabel();
    await loadPage();
}

async function loadPage() {
    const page = app.page;
    if (!page) return;
    try {
        await page.load(app);
    } catch (err) {
        console.error(`page ${page.id} failed`, err);
    }
}

/* ─── Filter bar ─── */
function updateScopeLabel() {
    const el = $('#pageScope');
    if (!app.page?.scoped) { el.textContent = ''; return; }
    const parts = [];
    if (app.scope.workOrder) parts.push(`${t('filter.woAria')} ${app.scope.workOrder}`);
    const { from, to } = app.scope.range === 'custom' ? app.scope : presetRange(app.scope.range);
    if (from || to) parts.push(from === to ? from : `${from || '…'} ~ ${to || '…'}`);
    el.textContent = parts.join('・') || t('filter.scopeAll');
}

function renderRange() {
    $$('#rangeSeg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.range === app.scope.range)));
    $('#customRange').hidden = app.scope.range !== 'custom';
}

function renderWoOptions() {
    const sel = $('#woSelect');
    sel.innerHTML = '';
    const all = new Option(t('filter.allWo'), '');
    sel.appendChild(all);
    app.options.work_orders.forEach((w, i) => {
        const label = `${w.work_order}${i === 0 ? t('filter.latest') : ''}　${fmtShortDate(w.first_date)}–${fmtShortDate(w.last_date)}`;
        sel.appendChild(new Option(label, w.work_order));
    });
    sel.value = app.scope.workOrder;
}

function scopeChanged() {
    renderRange();
    updateScopeLabel();
    loadPage();
}

function initFilters() {
    $$('#rangeSeg button').forEach(b => b.addEventListener('click', () => {
        app.scope.range = b.dataset.range;
        if (b.dataset.range === 'custom' && !app.scope.from) {
            const latest = app.options.work_orders[0];
            app.scope.from = latest ? fmtDate(latest.first_date) : fmtDate(new Date());
            app.scope.to = latest ? fmtDate(latest.last_date) : fmtDate(new Date());
            $('#dateFrom').value = app.scope.from;
            $('#dateTo').value = app.scope.to;
        }
        scopeChanged();
    }));
    for (const [id, key] of [['#dateFrom', 'from'], ['#dateTo', 'to']]) {
        $(id).addEventListener('change', e => { app.scope[key] = e.target.value; scopeChanged(); });
    }
    $('#woSelect').addEventListener('change', e => { app.scope.workOrder = e.target.value; scopeChanged(); });
}

async function loadOptions() {
    try {
        app.options = await apiGet('/api/filter-options');
    } catch {
        app.options = app.options || { work_orders: [] };
    }
    renderWoOptions();
}

/* ─── Freshness, status lights ─── */
async function loadMeta() {
    try {
        const meta = await apiGet('/api/meta');
        const changed = app.meta && app.meta.stats_refreshed_at !== meta.stats_refreshed_at;
        app.meta = meta;
        setThresholds(meta.thresholds);
        renderFreshness();
        setLlm(meta.llm?.connected);
        return changed;
    } catch {
        return false;
    }
}

function renderFreshness() {
    const at = app.meta?.stats_refreshed_at;
    $('#freshText').textContent = at ? t('fresh.label', { time: `${fmtShortDate(at)} ${fmtTime(at)}` }) : t('fresh.unknown');
}

function setLlm(ok) {
    $('#llmDot').className = `dot ${ok ? 'ok' : 'err'}`;
    $('#llmText').textContent = ok ? t('status.llmOk') : t('status.llmErr');
    report.setLlm(!!ok);
}

function initStatus() {
    onApiStatus(ok => {
        $('#apiDot').className = `dot ${ok ? 'ok' : 'err'}`;
        $('#apiText').textContent = ok ? t('status.apiOk') : t('status.apiErr');
    });
    $('#btnRefresh').addEventListener('click', async () => {
        const btn = $('#btnRefresh');
        btn.disabled = true;
        btn.textContent = t('fresh.refreshing');
        try {
            await apiPost('/api/stats/refresh');
            await Promise.all([loadMeta(), loadOptions()]);
            await loadPage();
            toast(t('fresh.done'));
        } catch (err) {
            toast(err.message, 'bad');
        } finally {
            btn.disabled = false;
            btn.textContent = t('fresh.refresh');
        }
    });
    // New data (scheduled view refresh, uploads): reload the visible page quietly.
    setInterval(async () => {
        if (await loadMeta()) {
            await loadOptions();
            if (app.page?.scoped && !busy()) loadPage();
        }
    }, 60 * 1000);
}

/* ─── Theme, clock ─── */
function currentTheme() { return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'; }

function renderThemeButton() {
    $('#btnTheme').textContent = currentTheme() === 'dark' ? t('theme.toLight') : t('theme.toDark');
}

function initTheme() {
    renderThemeButton();
    $('#btnTheme').addEventListener('click', () => {
        const next = currentTheme() === 'dark' ? 'light' : 'dark';
        document.documentElement.dataset.theme = next;
        try { localStorage.setItem('pixi-theme', next); } catch { /* storage blocked */ }
        renderThemeButton();
        charts.rethemeAll();
    });
}

function initClock() {
    const tick = () => {
        const now = new Date();
        $('#clockTime').textContent = now.toTimeString().slice(0, 5);
        $('#clockDate').textContent = now.toLocaleDateString('zh-TW', { year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short' });
    };
    tick();
    setInterval(tick, 10 * 1000);
}

/* ─── Signed-in user box ─── */
function renderAuth(user, reason) {
    $('#authBox').hidden = !user;
    $('#navAccounts').hidden = !auth.isAdmin();
    if (user) {
        $('#authName').textContent = t('auth.user', { name: auth.displayName(user) });
        $('#authName').title = auth.displayName(user);
        $('#authRole').textContent = auth.isAdmin() ? t('auth.admin') : t('auth.uploader');
        $('#btnChangePw').hidden = auth.isAdmin();
    }
    if (reason === 'expired') toast(t('auth.expired'), 'bad');
    PAGES.forEach(p => p.onAuthChange?.(user, reason, app));
}

/* ─── Idle rotation: 儀表板 ↔ 工單 every 30 s after 5 min idle; filters untouched ─── */
const idle = { last: Date.now(), timer: null };

function busy() {
    return !$('#reportModal').hidden || !$('#formModal').hidden || PAGES.some(p => p.isBusy?.());
}

function initIdleRotation() {
    const touch = () => { idle.last = Date.now(); };
    ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart'].forEach(ev =>
        window.addEventListener(ev, touch, { passive: true }));
    setInterval(() => {
        if (Date.now() - idle.last < IDLE_MS || busy() || document.hidden) return;
        const i = ROTATION.indexOf(app.page?.id);
        const next = ROTATION[(i + 1) % ROTATION.length];
        if (location.hash !== `#${next}`) location.hash = next;
        else loadPage();
    }, ROTATE_MS);
}

/* ─── Start ─── */
async function start() {
    applyI18n();
    initFormModal();
    initEscapeClose([['formModal', closeFormModal], ['reportModal', () => report.close()]]);
    charts.applyDefaults();
    initTheme();
    initClock();
    initStatus();
    initFilters();
    renderRange();
    report.init(app);
    PAGES.forEach(p => p.init?.(app));
    auth.onAuthChange(renderAuth);
    $('#btnLogout').addEventListener('click', auth.logout);
    $('#btnChangePw').addEventListener('click', auth.openChangePassword);
    $('#navAiReport').addEventListener('click', () => {
        const wo = app.latestWorkOrder();
        if (wo) report.open(wo);
        else toast(t('rp.noLatest'));
    });
    window.addEventListener('hashchange', () => show(pageFromHash()));

    await Promise.all([loadMeta(), loadOptions(), auth.restore()]);
    app.scope.workOrder = app.latestWorkOrder();     // 預設：最新一張工單 (Q3)
    renderWoOptions();
    initIdleRotation();
    await show(pageFromHash());
}

// Chart.js is a deferred classic script; module scripts run after it.
start();
