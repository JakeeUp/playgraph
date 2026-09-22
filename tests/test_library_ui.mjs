import test from 'node:test';
import assert from 'node:assert/strict';
import { selectLibrary, summarize, achievementPercent, steamArt } from '../app/static/library.js';
import { barFraction } from '../app/static/chart.js';
import { ratingAtPosition } from '../app/static/rating.js';

const library = [
  { game: { id: 1, name: 'Alpha', genres: 'Action, RPG' }, playtime_minutes: 60, achievements_unlocked: 0, achievements_total: 10 },
  { game: { id: 2, name: 'Beta', genres: null }, playtime_minutes: 0, achievements_unlocked: null, achievements_total: null },
  { game: { id: 3, name: 'Gamma', genres: 'Action' }, playtime_minutes: 120, achievements_unlocked: 5, achievements_total: 10 },
];
test('ratings map pointer positions to half-stars and clamp drag boundaries', () => {
  assert.equal(ratingAtPosition(-10, 200), 0.5);
  assert.equal(ratingAtPosition(10, 200), 0.5);
  assert.equal(ratingAtPosition(30, 200), 1);
  assert.equal(ratingAtPosition(100, 200), 2.5);
  assert.equal(ratingAtPosition(170, 200), 4.5);
  assert.equal(ratingAtPosition(300, 200), 5);
  assert.equal(ratingAtPosition(20, 0), 0);
});
test('software is available separately and cannot inflate selected game stats', () => {
  const software = { ...library[0], game: { ...library[0].game, content_kind: 'software' }, playtime_minutes: 99999 };
  const source = [...library, software];
  assert.equal(summarize(selectLibrary(source, { kind: 'game' })).minutes, 180);
  assert.equal(selectLibrary(source, { kind: 'software' }).length, 1);
  assert.equal(source.length, 4);
});
test('library filtering combines name, genre and actual play status without changing source order', () => {
  assert.deepEqual(selectLibrary(library, { query: ' AL ', genre: 'RPG', filter: 'played' }).map((row) => row.game.id), [1]);
  assert.deepEqual(selectLibrary(library, { filter: 'unplayed' }).map((row) => row.game.id), [2]);
  assert.deepEqual(library.map((row) => row.game.id), [1, 2, 3]);
});
test('zero achievement progress sorts ahead of missing progress', () => {
  assert.deepEqual(selectLibrary(library, { sort: 'achievements' }).map((row) => row.game.id), [3, 1, 2]);
  assert.equal(achievementPercent(library[1]), null);
  assert.equal(achievementPercent(library[0]), 0);
});
test('summary counts game playtime once even when genres overlap', () => {
  assert.deepEqual(summarize(library), { games: 3, minutes: 180, played: 2, unlocked: 5, achievementGames: 2 });
});
test('search covers a library larger than the previous 200 game cutoff', () => {
  const entries = Array.from({ length: 326 }, (_, i) => ({ ...library[0], game: { id: i, name: `Game ${i}`, genres: 'Action' } }));
  assert.equal(selectLibrary(entries, { query: 'Game 325' })[0].game.id, 325);
});
test('artwork URLs are built from the numeric app id alone on the one CSP-allowed host', () => {
  const base = 'https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/';
  assert.equal(steamArt(1145360, 'library_600x900.jpg'), `${base}1145360/library_600x900.jpg`);
  assert.equal(steamArt('620', 'header.jpg'), `${base}620/header.jpg`);
  // Anything that is not a plain number collapses to NaN instead of steering the path or host.
  for (const hostile of ['1/../../x', '//attacker.test/x', 'javascript:alert(1)', '1?x=y', { id: 1 }]) {
    const url = new URL(steamArt(hostile, 'header.jpg'));
    assert.equal(url.origin, 'https://shared.fastly.steamstatic.com');
    assert.equal(url.pathname, '/store_item_assets/steam/apps/NaN/header.jpg');
  }
});
test('bars share one baseline and one maximum, clamped to the unit range', () => {
  assert.equal(barFraction(50, 200), 0.25);
  assert.equal(barFraction(200, 200), 1);
  assert.equal(barFraction(0, 200), 0);
  assert.equal(barFraction(300, 200), 1);
  assert.equal(barFraction(-5, 200), 0);
  for (const [value, max] of [[5, 0], [5, -1], [NaN, 10], [Infinity, 10]]) assert.equal(barFraction(value, max), 0);
});
