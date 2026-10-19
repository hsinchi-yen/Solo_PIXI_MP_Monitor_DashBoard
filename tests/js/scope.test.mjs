import test from 'node:test';
import assert from 'node:assert/strict';
import { presetRange, queryString, scopeKey, scopeParams } from '../../solo-pixi-essential/web/js/core/scope.js';

const WED = new Date(2026, 9, 21);   // Wednesday 2026-10-21

test('今天 / 本週 (Mon–Sun) / 本月 / 全部', () => {
    assert.deepEqual(presetRange('today', WED), { from: '2026-10-21', to: '2026-10-21' });
    assert.deepEqual(presetRange('week', WED), { from: '2026-10-19', to: '2026-10-25' });
    assert.deepEqual(presetRange('week', new Date(2026, 9, 25)), { from: '2026-10-19', to: '2026-10-25' });   // Sunday
    assert.deepEqual(presetRange('month', WED), { from: '2026-10-01', to: '2026-10-31' });
    assert.deepEqual(presetRange('month', new Date(2028, 1, 10)), { from: '2028-02-01', to: '2028-02-29' });  // leap year
    assert.deepEqual(presetRange('all', WED), { from: '', to: '' });
});

test('default scope is the latest work order over all dates', () => {
    assert.deepEqual(scopeParams({ range: 'all', workOrder: '5101-260630006' }, WED), { work_order: '5101-260630006' });
});

test('custom range keeps both ends; reversed input is swapped', () => {
    assert.deepEqual(scopeParams({ range: 'custom', from: '2026-08-21', to: '2026-08-12', workOrder: '' }, WED),
        { date_from: '2026-08-12', date_to: '2026-08-21' });
});

test('query string drops empty values; scope key is stable', () => {
    assert.equal(queryString({ work_order: 'A', date_from: '' }), '?work_order=A');
    assert.equal(queryString({}), '');
    assert.equal(scopeKey({ range: 'today', workOrder: '' }, WED), '?date_from=2026-10-21&date_to=2026-10-21');
});
