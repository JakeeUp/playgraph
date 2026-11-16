import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { badgeLabel, badgeFor } from '../app/static/post.js';

test('only the three earned badge tiers receive an accessible label', () => {
  for (const tier of [100, 1000, 5000]) {
    assert.equal(badgeLabel(tier), `Verified player: ${new Intl.NumberFormat().format(tier)}+ verified hours on game reviews`);
  }
  for (const value of [null, undefined, '', false, 0, -1, 99, 101, 100.5, Infinity, 'oops']) {
    assert.equal(badgeLabel(value), '');
    assert.equal(badgeFor(value), null);
  }
});

test('demo badges use current game reviews and linked accounts across every response', async () => {
  const data = {
    users: [{ id: 1, display_name: 'Me', has_steam: true }, { id: 2, display_name: 'Player', has_steam: true },
      { id: 3, display_name: 'Unlinked', has_steam: false }],
    games: [{ id: 1, name: 'Game', content_kind: 'game' }, { id: 2, name: 'App', content_kind: 'software' }],
    library: [], comments: [], follows: [],
    reviews: [{ id: 1, user_id: 2, game_id: 1, rating: 4, verified_playtime_minutes: 6000, minutes_ago: 1 },
      { id: 2, user_id: 3, game_id: 1, rating: 4, verified_playtime_minutes: 300000, minutes_ago: 1 },
      { id: 3, user_id: 2, game_id: 2, rating: 4, verified_playtime_minutes: 300000, minutes_ago: 1 }],
  };
  const context = vm.createContext({ window: { PLAYGRAPH_DEMO: data, fetch: () => { throw new Error('Unexpected network'); } },
    sessionStorage: { getItem: () => null, setItem: () => {} }, location: { href: 'http://localhost/', origin: 'http://localhost' },
    document: { addEventListener: () => {} }, URL, Response, setTimeout: (fn) => { fn(); } });
  vm.runInContext(await readFile(new URL('../demo/demo-api.js', import.meta.url), 'utf8'), context);
  const get = async (path) => (await context.window.fetch(path)).json();
  const profile = await get('/users/2');
  assert.equal(profile.badge, 100);
  assert.equal(profile.counts.verified_minutes, 6000);
  assert.equal((await get('/users/3')).badge, null);
  assert.equal((await get('/reviews/1')).review.author_badge, 100);
  assert.equal((await get('/explore/suggestions')).items.find((person) => person.id === 2).badge, 100);
  await context.window.fetch('/reviews/1/repost', { method: 'POST', body: JSON.stringify({ quote: 'Agreed' }) });
  const feed = await get('/me/feed');
  assert.ok(feed.items.some((item) => item.repost?.author_badge === 100));
  await context.window.fetch('/reviews/1/like', { method: 'POST' });
  assert.equal((await get('/reviews/1')).review.author_badge, 100);
});
