import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { createGameSearch, searchResultMeta } from '../app/static/game-search.js';
import { isPlayStationGame, reviewVerification } from '../app/static/library.js';

test('game search labels release year and every release platform', () => {
  assert.equal(searchResultMeta({ first_release_date: '2025-12-31T23:00:00Z', platforms: [{ abbreviation: 'PC' }, { name: 'Switch' }] }), '2025 · PC, Switch');
  assert.equal(searchResultMeta({}), 'Release details not listed');
  assert.equal(searchResultMeta({ first_release_date: 'invalid' }), 'Release details not listed');
});

test('demo imports persist, ignore forged metadata, stay unverified, and reset', async () => {
  const data = { users: [{ id: 1, display_name: 'Me' }], games: [], library: [], reviews: [], comments: [],
    importable_games: [{ id: 9001, igdb_id: 900001, name: 'Moonlit Harbor', steam_appid: null, catalog_source: 'igdb', content_kind: 'game' }] };
  let saved = null;
  const storage = { getItem: () => saved, setItem: (_, value) => { saved = value; }, removeItem: () => { saved = null; } };
  const code = await readFile(new URL('../demo/demo-api.js', import.meta.url), 'utf8');
  function boot() {
    const context = vm.createContext({ window: { PLAYGRAPH_DEMO: data, fetch: () => { throw new Error('Unexpected network'); } },
      sessionStorage: storage, location: { href: 'http://localhost/', origin: 'http://localhost' },
      document: { addEventListener: () => {} }, URL, Response, setTimeout: (fn) => fn() });
    vm.runInContext(code, context); return context.window;
  }
  let window = boot();
  assert.equal((await (await window.fetch('/games/search?q=Moonlit')).json()).external.length, 1);
  const add = () => window.fetch('/games/import', { method: 'POST', body: JSON.stringify({ igdb_id: 900001, name: 'Forged' }) });
  assert.equal((await (await add()).json()).name, 'Moonlit Harbor');
  await add();
  assert.equal((await (await window.fetch('/games')).json()).total, 1);
  await window.fetch('/games/9001/reviews', { method: 'POST', body: JSON.stringify({ rating: 4, body: 'Sample review' }) });
  window = boot();
  const reviews = await (await window.fetch('/games/9001/reviews')).json();
  assert.equal(reviews[0].verified_playtime_minutes, null);
  assert.equal((await window.fetch('/games/9001')).status, 200);
  await window.fetch('/auth/logout', { method: 'POST' });
  assert.equal((await (await window.fetch('/games/search?q=Moonlit')).json()).external.length, 0);
  assert.equal((await add()).status, 401);
  window.playgraphDemo.reset();
  assert.equal((await window.fetch('/games/9001')).status, 404);
});

test('an IGDB-only game is not a PlayStation import or a verified review', () => {
  assert.equal(isPlayStationGame({ steam_appid: null, catalog_source: 'igdb' }), false);
  assert.equal(isPlayStationGame({ steam_appid: null, catalog_source: 'psn' }), true);
  assert.equal(reviewVerification({ verified_source: null, verified_playtime_minutes: null, verified_achievement_pct: null }), null);
});

test('search errors connect before display and stale searches cannot restore results', async () => {
  class Node {
    constructor() { this.children = []; this.listeners = {}; this.hidden = false; this.isConnected = false; this.value = 'Moonlit'; }
    setAttribute(name, value) { this[name] = value; }
    removeAttribute(name) { delete this[name]; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    append(...nodes) { for (const node of nodes) { node.isConnected = this.isConnected; this.children.push(node); } }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  }
  const nodes = new Map(['game-search', 'game-search-results', 'game-search-form', 'game-search-query', 'game-search-submit', 'game-search-open', 'game-search-platform']
    .map((id) => [id, Object.assign(new Node(), { id, isConnected: true })]));
  const previous = globalThis.document;
  globalThis.document = { querySelector: (selector) => nodes.get(selector.slice(1)), createElement: () => new Node() };
  try {
    createGameSearch({ user: { id: 1 } }, async () => { throw new Error('Search unavailable'); }, {});
    const submit = () => nodes.get('game-search-form').listeners.submit({ preventDefault() {} });
    await submit();
    assert.equal(nodes.get('game-search-results').children[0].textContent, 'Search unavailable');
    assert.equal(nodes.get('game-search-results').children[0].hidden, false);
    let resolve;
    const second = createGameSearch({}, () => new Promise((done) => { resolve = done; }), {});
    const waiting = submit(); second.clear(); resolve({ local: [], external: [] }); await waiting;
    assert.equal(nodes.get('game-search-results').children.length, 0);
  } finally { globalThis.document = previous; }
});
