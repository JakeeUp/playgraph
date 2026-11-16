import test from 'node:test';
import assert from 'node:assert/strict';
import { viewFromHash } from '../app/static/social.js';
import { sendRequest } from '../app/static/net.js';

test('only app tabs can be restored from the address', () => {
  for (const view of ['library', 'software', 'stats', 'feed', 'news', 'explore', 'notifications']) {
    assert.equal(viewFromHash(`#view=${view}`), view);
  }
  for (const hash of ['', null, '#view=admin', '#view=feed&user=1', '#user=1', '#view=Feed']) {
    assert.equal(viewFromHash(hash), null);
  }
});

test('raw image bodies retain their explicit content type and CSRF header', async () => {
  const original = globalThis.fetch;
  const body = new Blob(['image'], { type: 'image/png' });
  globalThis.fetch = async (_, init) => {
    assert.equal(init.headers.get('Content-Type'), 'application/octet-stream');
    assert.equal(init.headers.get('X-CSRF-Token'), 'token');
    assert.equal(init.body, body);
    return new Response('{}');
  };
  try { await sendRequest('/me/profile-images/avatar', { method: 'PUT', body, csrf: 'token',
    headers: { 'Content-Type': 'application/octet-stream' } }); }
  finally { globalThis.fetch = original; }
});
