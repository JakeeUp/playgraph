import test from 'node:test';
import assert from 'node:assert/strict';
import { splitBySource, playtimeLabel, playStationSummary, reviewVerification, isPlayStationGame,
  summarize, hours } from '../app/static/library.js';
import { privacyNote, failureText } from '../app/static/psn-account.js';

const trophies = (earned, total) => ({ earned, total, progress: null });
const steamEntry = { source: 'steam', game: { id: 1, name: 'Steam game', steam_appid: 10 }, playtime_minutes: 600,
  achievements_unlocked: 5, achievements_total: 10, trophies: null };
const psnEntry = { source: 'psn', game: { id: 2, name: 'PS game', steam_appid: null }, playtime_minutes: 90,
  achievements_unlocked: 7, achievements_total: 20,
  trophies: trophies({ platinum: 1, gold: 1, silver: 2, bronze: 3 }, { platinum: 1, gold: 3, silver: 6, bronze: 10 }) };
const hiddenEntry = { ...psnEntry, game: { id: 3, name: 'Hidden', steam_appid: null }, playtime_minutes: null, trophies: null };

test('library entries split by source and Steam totals never include PlayStation hours', () => {
  const library = [steamEntry, psnEntry, hiddenEntry, { game: { id: 4, name: 'Old row', steam_appid: 11 }, playtime_minutes: 5 }];
  const { steam, psn } = splitBySource(library);
  assert.deepEqual(steam.map((e) => e.game.id), [1, 4]);
  assert.deepEqual(psn.map((e) => e.game.id), [2, 3]);
  assert.equal(summarize(steam).minutes, 605);
  assert.equal(library.length, 4);
});

test('unknown PlayStation hours read as not shared, never as zero', () => {
  assert.equal(playtimeLabel(hiddenEntry), 'Hours not shared');
  assert.equal(playtimeLabel({ playtime_minutes: 0 }), `${hours(0)} h`);
  assert.equal(playtimeLabel(psnEntry), `${hours(90)} h`);
  assert.equal(summarize([hiddenEntry]).minutes, 0);
});

test('trophy summaries count each grade and only the hours PSN shared', () => {
  assert.deepEqual(playStationSummary([psnEntry, hiddenEntry]), {
    games: 2, minutes: 90, withHours: 1, earned: 7, total: 20, platinum: 1, gold: 1, silver: 2, bronze: 3 });
  assert.equal(isPlayStationGame(psnEntry.game), true);
  assert.equal(isPlayStationGame(steamEntry.game), false);
});

test('review verification is labelled by the store it came from', () => {
  assert.equal(reviewVerification({ verified_playtime_minutes: null, verified_achievement_pct: null }), null);
  assert.deepEqual(reviewVerification({ verified_playtime_minutes: 120, verified_achievement_pct: 50 }),
    { badge: 'PC', text: `${hours(120)} h verified · 50% achievements` });
  assert.deepEqual(reviewVerification({ verified_playtime_minutes: null, verified_achievement_pct: 12.6, verified_source: 'psn' }),
    { badge: 'PlayStation', text: '13% trophies' });
  assert.deepEqual(reviewVerification({ verified_playtime_minutes: 0, verified_achievement_pct: null, verified_source: 'psn' }),
    { badge: 'PlayStation', text: `${hours(0)} h verified` });
});

test('account page explains what privacy settings let the last sync see', () => {
  assert.match(privacyNote(null), /first PlayStation sync/);
  assert.match(privacyNote({ trophies_visible: true, playtime_visible: false }), /only trophies.*never zero/);
  assert.match(privacyNote({ trophies_visible: false, playtime_visible: true }), /only PS4 and PS5 play time/);
  assert.match(privacyNote({ trophies_visible: false, playtime_visible: false }), /nothing new/);
  assert.match(privacyNote({ problem: 'account_not_found' }), /couldn’t find/);
  assert.match(privacyNote({ trophies_visible: true, playtime_visible: true }), /Trophies and PS4/);
});

test('failures become sentences without inventing server detail', () => {
  assert.equal(failureText(429, 'Too many requests', '120'), 'Too many attempts. Try again in 120 seconds.');
  assert.equal(failureText(409, 'Code not found in your About Me yet.'), 'Code not found in your About Me yet.');
  assert.equal(failureText(503, null), 'PlayStation is temporarily unavailable. Try again later.');
  assert.equal(failureText(500, { nested: true }), 'That didn’t work. Please try again.');
});
