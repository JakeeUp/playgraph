import test from 'node:test';
import assert from 'node:assert/strict';
import { userFromHash, profileHref, countsLine, limitedNote, bioRemaining, REPORT_REASONS, VISIBILITY } from '../app/static/social.js';

test('a profile link is read only when it is exactly a bounded user ID', () => {
  assert.equal(userFromHash('#user=12'), 12);
  assert.equal(userFromHash('#user=2147483647'), 2147483647);
  for (const hash of ['', '#user=0', '#user=012', '#user=-1', '#user=2147483648', '#user=12&x=1',
    '#user=12 ', '#review=12', '#user=99999999999', null, undefined]) {
    assert.equal(userFromHash(hash), null, String(hash));
  }
  assert.equal(profileHref('7'), '/app#user=7');
  assert.equal(profileHref('7"><script>'), '/app#user=NaN');
});

test('counts read as one line with singulars', () => {
  assert.equal(countsLine({ followers: 1, following: 0, reviews: 1 }), '1 follower · 0 following · 1 review');
  assert.equal(countsLine({ followers: 1200, following: 3, reviews: 0 }), '1,200 followers · 3 following · 0 reviews');
  assert.equal(countsLine(undefined), '');
});

test('a limited profile explains itself, and a full one says nothing', () => {
  assert.equal(limitedNote({ limited: false }), '');
  assert.match(limitedNote({ limited: true, viewer: { blocked: true } }), /You blocked/);
  assert.match(limitedNote({ limited: true, viewer: { blocked: false } }), /followers only\.$/);
  assert.match(limitedNote({ limited: true, viewer: { requested: true } }), /request is waiting/);
});

test('the bio counter counts what the server keeps', () => {
  assert.equal(bioRemaining(''), 280);
  assert.equal(bioRemaining('  a   b \n c  '), 275);
  assert.equal(bioRemaining('x'.repeat(300)), -20);
});

test('report reasons and visibility choices match the server', () => {
  assert.deepEqual(REPORT_REASONS.map(([value]) => value), ['spam', 'harassment', 'hate', 'sexual', 'spoilers', 'other']);
  assert.deepEqual(Object.keys(VISIBILITY), ['public', 'followers', 'private']);
});
