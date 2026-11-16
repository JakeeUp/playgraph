import test from 'node:test';
import assert from 'node:assert/strict';
import { STATUSES, statusLabel, indexStatuses, withStatus, filterByStatus, statusCounts } from '../app/static/status.js';

const game = (id, kind = 'game') => ({ id, name: `Game ${id}`, content_kind: kind });
const row = (id, status, kind) => ({ game: game(id, kind), status, updated_at: '2026-11-15T00:00:00Z' });
const shelf = [{ game: game(1), playtime_minutes: 60 }, { game: game(2), playtime_minutes: 0 }, { game: game(3), playtime_minutes: 5 }];
const statuses = indexStatuses([row(1, 'completed'), row(3, 'playing'), row(9, 'want_to_play'), row(8, 'want_to_play', 'software')]);

test('labels cover the five statuses and nothing else', () => {
  assert.deepEqual(STATUSES.map(({ value }) => value), ['want_to_play', 'playing', 'completed', 'paused', 'dropped']);
  assert.equal(statusLabel('want_to_play'), 'Want to play');
  assert.equal(statusLabel('beaten'), '');
  assert.equal(statusLabel(undefined), '');
});

test('no status filter keeps the shelf as it was', () => {
  assert.equal(filterByStatus(shelf, statuses, ''), shelf);
});

test('a status keeps matching games in order', () => {
  assert.deepEqual(filterByStatus(shelf, statuses, 'completed').map((e) => e.game.id), [1]);
  assert.deepEqual(filterByStatus(shelf, statuses, 'playing').map((e) => e.game.id), [3]);
  assert.deepEqual(filterByStatus(shelf, statuses, 'dropped'), []);
});

test('"No status" keeps only games without one, with no extras', () => {
  assert.deepEqual(filterByStatus(shelf, statuses, 'none').map((e) => e.game.id), [2]);
});

test('the backlog brings in games you do not own, of the shelf kind only', () => {
  const backlog = filterByStatus(shelf, statuses, 'want_to_play');
  assert.deepEqual(backlog, [{ game: game(9), unowned: true }]);
  assert.deepEqual(filterByStatus(shelf, statuses, 'want_to_play', 'software').map((e) => e.game.id), [8]);
  // No kind (the played filter is on, or the PlayStation shelf): no extras.
  assert.deepEqual(filterByStatus(shelf, statuses, 'want_to_play', null), []);
});

test('an owned game is never added twice', () => {
  const owned = indexStatuses([row(1, 'want_to_play')]);
  assert.deepEqual(filterByStatus(shelf, owned, 'want_to_play'), [shelf[0]]);
});

test('withStatus returns a new map and can clear', () => {
  const added = withStatus(statuses, 2, row(2, 'paused'));
  assert.notEqual(added, statuses);
  assert.equal(statuses.has(2), false);
  assert.equal(added.get(2).status, 'paused');
  assert.equal(withStatus(added, 2, null).has(2), false);
});

test('counts per status', () => {
  assert.deepEqual(statusCounts(statuses), { want_to_play: 2, playing: 1, completed: 1, paused: 0, dropped: 0 });
  assert.deepEqual(statusCounts(new Map()), { want_to_play: 0, playing: 0, completed: 0, paused: 0, dropped: 0 });
  assert.deepEqual(indexStatuses(null), new Map());
});
