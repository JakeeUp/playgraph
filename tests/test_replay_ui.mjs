import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { cardLines, monthLabel, replayHours, sourceName } from '../app/static/replay.js';

test('replay distinguishes unknown hours from a measured zero and keeps store labels', () => {
  assert.equal(replayHours(null), 'Hours not shared');
  assert.equal(replayHours(0), '0 h tracked');
  assert.equal(sourceName('psn'), 'PlayStation');
  assert.equal(sourceName('steam'), 'Steam');
  assert.equal(monthLabel(1), 'Jan'); assert.equal(monthLabel(12), 'Dec');
});

test('saved cards identify the source, incomplete coverage and baseline limits', () => {
  const data = { year: 2026, partial_year: true, reviews: { count: 2 }, completed_count: 3 };
  const source = { source: 'psn', total_minutes: null, top_games: [], trophies_earned: { bronze: 3, platinum: null } };
  const text = cardLines(data, source, 'Player').join('\n');
  assert.match(text, /PlayStation/); assert.match(text, /Hours not shared/);
  assert.match(text, /Partial year/); assert.match(text, /baseline/);
  assert.match(text, /platinum: unknown/); assert.match(text, /currently marked completed/);
});

test('saved Steam cards contain yearly game deltas rather than lifetime figures', () => {
  const data = { year: 2026, partial_year: false, reviews: { count: 0 }, completed_count: 0 };
  const lines = cardLines(data, { source: 'steam', total_minutes: 120, achievements_earned: 4,
    top_games: [{ game: { name: 'Cold Harbor' }, total_minutes: 60 }] }, 'Player');
  assert.ok(lines.includes('Steam')); assert.ok(lines.includes('4 achievements earned'));
  assert.ok(lines.includes('1. Cold Harbor | 1 h'));
  assert.ok(lines.includes('Software excluded. Steam and PlayStation are never combined.'));
});

test('demo replay is owner-only, validates years and refreshes reviews and statuses', async () => {
  const year = new Date().getUTCFullYear();
  const data = { users: [{ id: 1, display_name: 'Me', has_steam: true }],
    games: [{ id: 1, name: 'Game', content_kind: 'game' }], library: [{ game_id: 1, playtime_minutes: 100 }],
    comments: [], reviews: [], follows: [], replays: { [year]: { year, sources: [], reviews: { count: 0 } } } };
  const context = vm.createContext({ window: { PLAYGRAPH_DEMO: data, fetch: () => { throw new Error('Unexpected network'); } },
    sessionStorage: { getItem: () => null, setItem: () => {} }, location: { href: 'http://localhost/', origin: 'http://localhost' },
    document: { addEventListener: () => {} }, URL, Response, setTimeout: (fn) => { fn(); } });
  vm.runInContext(await readFile(new URL('../demo/demo-api.js', import.meta.url), 'utf8'), context);
  const fetch = context.window.fetch;
  assert.equal((await fetch('/me/replay?year=oops')).status, 422);
  assert.equal((await fetch(`/me/replay?year=${year + 1}`)).status, 422);
  await fetch('/me/games/1/status', { method: 'PUT', body: JSON.stringify({ status: 'completed' }) });
  const report = await (await fetch('/me/replay')).json();
  assert.equal(report.completed_count, 1); assert.equal(report.reviews.count, 0);
  await fetch('/me/games/1/status', { method: 'DELETE' });
  assert.equal((await (await fetch('/me/replay')).json()).completed_count, 0);
  await fetch('/auth/logout', { method: 'POST' });
  assert.equal((await fetch('/me/replay')).status, 401);
});
