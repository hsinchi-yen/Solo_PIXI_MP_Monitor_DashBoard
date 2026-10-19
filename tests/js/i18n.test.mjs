import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { STRINGS, t } from '../../solo-pixi-essential/web/js/core/i18n.js';

test('t() interpolates and falls back to the key', () => {
    assert.equal(t('fresh.label', { time: '10/19 22:59' }), '資料更新於 10/19 22:59');
    const warn = console.warn;
    console.warn = () => {};
    assert.equal(t('no.such.key'), 'no.such.key');
    console.warn = warn;
});

test('dictionary has no duplicate keys (later ones would silently win)', () => {
    const src = readFileSync(new URL('../../solo-pixi-essential/web/js/core/i18n.js', import.meta.url), 'utf8');
    const keys = [...src.matchAll(/^\s+'([\w.]+)':/gm)].map(m => m[1]);
    const dupes = keys.filter((k, i) => keys.indexOf(k) !== i);
    assert.deepEqual(dupes, []);
    assert.equal(Object.keys(STRINGS).length, keys.length);
});

test('UI language is Traditional Chinese', () => {
    assert.equal(STRINGS['nav.overview'], '儀表板');
    assert.equal(STRINGS['ov.yield'], '良率');
    assert.match(STRINGS['nav.measurements'], /量測/);
});
