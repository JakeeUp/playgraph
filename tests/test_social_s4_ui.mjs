import assert from 'node:assert/strict';
import test from 'node:test';
import { notificationText } from '../app/static/social.js';

test('notifications group names without exposing a whole liker list', () => {
  const actors = [{ id: 2, display_name: 'Alice' }];
  assert.equal(notificationText({ kind: 'like', actor_count: 13, actors }), 'Alice and 12 others liked your review');
  assert.equal(notificationText({ kind: 'repost', actor_count: 2, actors }), 'Alice and 1 other reposted your review');
  for (const [kind, action] of Object.entries({ quote: 'quoted your review', comment: 'replied to your review',
    follow: 'followed you', follow_accepted: 'accepted your follow request' })) {
    assert.equal(notificationText({ kind, actor_count: 1, actors }), `Alice ${action}`);
  }
});
