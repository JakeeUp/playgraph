import assert from 'node:assert/strict';
import test from 'node:test';
import { followReason, trendLine } from '../app/static/social.js';

test('a trend line names the genre and the hours reviewed this week', () => {
  assert.equal(trendLine('RPG', 1204), `Trending in RPG · ${new Intl.NumberFormat().format(1204)} hours reviewed this week`);
  assert.equal(trendLine('', 1), 'Trending · 1 hour reviewed this week');
  assert.equal(trendLine('Action', 0), 'Trending in Action');
});

test('who to follow prefers games in common, then likes', () => {
  assert.equal(followReason({ shared_games: 2, likes_received: 40 }), '2 games in common');
  assert.equal(followReason({ shared_games: 1, likes_received: 3 }), '1 game in common');
  assert.equal(followReason({ shared_games: 0, likes_received: 3 }), '3 likes');
  assert.equal(followReason({ shared_games: 0, likes_received: 0 }), 'Active this week');
});
