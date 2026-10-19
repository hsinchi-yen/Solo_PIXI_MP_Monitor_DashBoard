// node --test tests/js — browser modules that do not touch the DOM.
import test from 'node:test';
import assert from 'node:assert/strict';
import { esc, fmtClockDate, fmtDate, fmtInt, fmtPct, fmtSigned, isoDate, retryLevel, setThresholds, THRESHOLDS, toDate, yieldLevel } from '../../solo-pixi-essential/web/js/core/format.js';

test('yield colour levels use the shared thresholds (98 / 95)', () => {
    assert.equal(yieldLevel(99.97), 'good');
    assert.equal(yieldLevel(98), 'good');
    assert.equal(yieldLevel(97.99), 'warn');
    assert.equal(yieldLevel(95), 'warn');
    assert.equal(yieldLevel(94.99), 'bad');
    assert.equal(yieldLevel(null), '');
    assert.equal(yieldLevel('—'), '');
});

test('retry levels: lower is better (3 / 5)', () => {
    assert.equal(retryLevel(2.5), 'good');
    assert.equal(retryLevel(3), 'good');
    assert.equal(retryLevel(4.2), 'warn');
    assert.equal(retryLevel(8.77), 'bad');
});

test('thresholds can be replaced from /api/meta', () => {
    const saved = { ...THRESHOLDS };
    setThresholds({ yield_good: 99.2, yield_warn: 98.5 });
    assert.equal(yieldLevel(99), 'warn');
    setThresholds(saved);
    assert.equal(yieldLevel(99), 'good');
});

test('number formatting', () => {
    assert.equal(fmtInt(4335), '4,335');
    assert.equal(fmtInt(null), '—');
    assert.equal(fmtPct(99.966), '99.97%');
    assert.equal(fmtPct(undefined), '—');
    assert.equal(fmtSigned(0.72), '+0.72');
    assert.equal(fmtSigned(-0.03), '-0.03');
});

test('dates: API strings and ISO parameters', () => {
    assert.equal(fmtDate('2026-08-21'), '2026-08-21');
    assert.equal(fmtDate('2026-08-21 16:38:31'), '2026-08-21');
    assert.equal(toDate('not a date'), null);
    assert.equal(isoDate(new Date(2026, 0, 5)), '2026-01-05');
});

test('esc() is safe inside attributes', () => {
    assert.equal(esc(`<b title="x">'&`), '&lt;b title=&quot;x&quot;&gt;&#39;&amp;');
    assert.equal(esc(null), '');
});

test('sidebar clock date stays numeric in both languages', () => {
    const d = new Date(2026, 9, 20);
    assert.equal(fmtClockDate(d, 'en'), '2026/10/20 (Tue)');
    assert.equal(fmtClockDate(d, 'zh'), '2026/10/20（週二）');
    assert.equal(fmtClockDate(null, 'en'), '—');
});
