import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { getLang, initialLang, joinList, localized, setLang, STRINGS, t, tLang } from '../../solo-pixi-essential/web/js/core/i18n.js';

const CJK = /[　-〿㐀-鿿＀-￯]/;
// Language names are written in their own language on purpose; the upload menu item stays Chinese
// for the line staff (Q55).
const ENGLISH_MAY_HAVE_CHINESE = new Set(['lang.switch', 'lang.aria', 'rp.zh', 'nav.logupload']);
const vars = s => [...s.matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort();

test('English is the default language', () => {
    assert.equal(getLang(), 'en');
    assert.equal(t('fresh.label', { time: '10/19 22:59' }), 'Updated 10/19 22:59');
    assert.equal(t('nav.overview'), 'Dashboard');
});

test('t() switches with setLang() and falls back to the key', () => {
    setLang('zh', { save: false, notify: false });
    try {
        assert.equal(t('fresh.label', { time: '10/19 22:59' }), '資料更新於 10/19 22:59');
        assert.equal(t('nav.overview'), '儀表板');
        assert.equal(joinList(['A', 'B']), 'A、B');
        assert.equal(localized('中文', 'English'), '中文');
    } finally {
        setLang('en', { save: false, notify: false });
    }
    assert.equal(joinList(['A', 'B']), 'A, B');
    assert.equal(localized('中文', 'English'), 'English');
    assert.equal(localized('中文', undefined), '中文');      // no English version: show what there is
    assert.equal(tLang('zh', 'ov.yield'), '良率');
    const warn = console.warn;
    console.warn = () => {};
    assert.equal(t('no.such.key'), 'no.such.key');
    console.warn = warn;
});

test('?lang= wins, then the saved choice, then English', () => {
    assert.equal(initialLang('', null), 'en');
    assert.equal(initialLang('', 'zh'), 'zh');
    assert.equal(initialLang('?lang=en', 'zh'), 'en');
    assert.equal(initialLang('?lang=zh', null), 'zh');
    assert.equal(initialLang('?lang=fr', 'xx'), 'en');
});

test('each dictionary has no duplicate keys (later ones would silently win)', () => {
    for (const lang of ['en', 'zh']) {
        const src = readFileSync(new URL(`../../solo-pixi-essential/web/js/core/strings.${lang}.js`, import.meta.url), 'utf8');
        const keys = [...src.matchAll(/^\s+'([\w.]+)':/gm)].map(m => m[1]);
        const dupes = keys.filter((k, i) => keys.indexOf(k) !== i);
        assert.deepEqual(dupes, [], lang);
        assert.equal(Object.keys(STRINGS[lang]).length, keys.length, lang);
    }
});

test('English and Chinese have the same keys and {variables}', () => {
    assert.deepEqual(Object.keys(STRINGS.en).sort(), Object.keys(STRINGS.zh).sort());
    const mismatched = Object.keys(STRINGS.en).filter(k => vars(STRINGS.en[k]).join() !== vars(STRINGS.zh[k]).join());
    assert.deepEqual(mismatched, []);
});

test('English strings contain no Chinese characters or full-width punctuation', () => {
    const bad = Object.entries(STRINGS.en).filter(([k, v]) => !ENGLISH_MAY_HAVE_CHINESE.has(k) && CJK.test(v)).map(([k]) => k);
    assert.deepEqual(bad, []);
});

test('Chinese UI keeps its wording', () => {
    assert.equal(STRINGS.zh['nav.overview'], '儀表板');
    assert.equal(STRINGS.zh['ov.yield'], '良率');
    assert.match(STRINGS.zh['nav.measurements'], /量測/);
    assert.equal(STRINGS.en['ov.m10'], 'Oct');
    assert.equal(STRINGS.zh['ov.m10'], '10 月');
});
