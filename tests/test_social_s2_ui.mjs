import test from 'node:test';
import assert from 'node:assert/strict';
import { quoteText, nextLikeCount } from '../app/static/post.js';

test('a quote collapses whitespace and stops at 280 characters', () => {
  assert.equal(quoteText('  hello   there \n'), 'hello there');
  assert.equal(quoteText('   '), '');
  assert.equal(quoteText(null), '');
  assert.equal(quoteText('x'.repeat(300)).length, 280);
});

test('a repost moves the public count the same way a like does', () => {
  assert.equal(nextLikeCount(0, true), 1);
  assert.equal(nextLikeCount(2, false), 1);
});
