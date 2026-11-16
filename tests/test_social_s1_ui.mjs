import test from 'node:test';
import assert from 'node:assert/strict';
import { compactCount, handleText, nextLikeCount, likeLookup } from '../app/static/post.js';
import { profileStats, favoriteIds } from '../app/static/social.js';

test('counts shorten like X and never round up past the real number', () => {
  assert.equal(compactCount(0), '0');
  assert.equal(compactCount(999), '999');
  assert.equal(compactCount(1000), '1K');
  assert.equal(compactCount(1250), '1.2K');
  assert.equal(compactCount(1999), '1.9K');
  assert.equal(compactCount(12_345), '12K');
  assert.equal(compactCount(999_999), '999K');
  assert.equal(compactCount(3_400_000), '3.4M');
  assert.equal(compactCount(2_000_000_000), '2B');
  assert.equal(compactCount(-5), '0');
  assert.equal(compactCount('oops'), '0');
});

test('handles show with an @, and nothing while a player has none', () => {
  assert.equal(handleText('rosa'), '@rosa');
  assert.equal(handleText(null), '');
  assert.equal(handleText(''), '');
});

test('a like toggle moves the count by one and never below zero', () => {
  assert.equal(nextLikeCount(4, true), 5);
  assert.equal(nextLikeCount(4, false), 3);
  assert.equal(nextLikeCount(0, false), 0);
  assert.equal(nextLikeCount(undefined, true), 1);
});

test('one like lookup per page, deduplicated and capped at 100', () => {
  assert.equal(likeLookup([{ id: 3 }, { id: 1 }, { id: 3 }]), '3,1');
  assert.equal(likeLookup([]), '');
  const many = Array.from({ length: 150 }, (_, i) => ({ id: i + 1 }));
  assert.equal(likeLookup(many).split(',').length, 100);
});

test('profile stats read like X, with hours from minutes', () => {
  assert.deepEqual(profileStats({ followers: 12, following: 3, reviews: 8, likes_received: 140, verified_minutes: 6125 }), [
    ['Followers', 12], ['Following', 3], ['Reviews', 8], ['Likes', 140], ['Verified hours', 102]]);
  assert.deepEqual(profileStats({ followers: 0, following: 0, reviews: 0 }).slice(3), [['Likes', 0], ['Verified hours', 0]]);
  assert.deepEqual(profileStats(null), []);
});

test('favorite slots keep order, drop empties and refuse repeats', () => {
  assert.deepEqual(favoriteIds(['4', '', '9', '']), [4, 9]);
  assert.deepEqual(favoriteIds(['', '', '', '']), []);
  assert.equal(favoriteIds(['4', '9', '4', '']), null);
});
