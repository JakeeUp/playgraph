import test from 'node:test';
import assert from 'node:assert/strict';
import { plural, countPair } from '../app/static/library.js';
import { failureMessage, failureOf, sendRequest, CSRF_HEADER } from '../app/static/net.js';
import { createJobPoller, isActiveJob } from '../app/static/jobs.js';

// Just enough DOM for el/numbered/statCell.
class FakeNode {
  constructor(tag) { this.tag = tag; this.children = []; this.className = ''; this.textContent = ''; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
}
globalThis.document = { createElement: (tag) => new FakeNode(tag) };

test('plural follows the count and accepts an irregular form', () => {
  assert.equal(plural(1, 'comment'), 'comment');
  assert.equal(plural(0, 'comment'), 'comments');
  assert.equal(plural(2, 'game'), 'games');
  assert.equal(plural(2, 'person', 'people'), 'people');
});

test('countPair prints earned / total with separators', () => {
  assert.equal(countPair(3, 10), '3 / 10');
  assert.equal(countPair(null, undefined), '0 / 0');
  assert.match(countPair(1200, 2000), /^1[,.  ]?200 \/ 2[,.  ]?000$/);
});

test('one message mapper words 429, server detail, 503 and the rest', () => {
  assert.equal(failureMessage(429, 'ignored', '30'), 'Too many requests. Try again in 30 seconds.');
  assert.equal(failureMessage(429, null, null), 'Too many requests. Try again in 60 seconds.');
  assert.equal(failureMessage(409, 'Already running.'), 'Already running.');
  assert.match(failureMessage(503, null), /security service/);
  assert.equal(failureMessage(503, null, null, { unavailable: 'Down.' }), 'Down.');
  assert.equal(failureMessage(500, { nested: true }), 'This request could not be completed. Please try again.');
  assert.equal(failureMessage(500, '', null, { fallback: 'Nope.' }), 'Nope.');
});

test('failureOf carries the status and Retry-After', () => {
  const response = { status: 429, headers: { get: (name) => (name === 'Retry-After' ? '7' : null) } };
  const error = failureOf(response, { detail: 'x' });
  assert.equal(error.status, 429);
  assert.match(error.message, /7 seconds/);
});

test('sendRequest sets credentials, no-store, JSON type and the CSRF header', async () => {
  let seen;
  globalThis.fetch = async (path, init) => { seen = { path, init }; return {}; };
  await sendRequest('/x', { method: 'POST', csrf: 'tok', body: '{}' });
  assert.equal(seen.init.credentials, 'same-origin');
  assert.equal(seen.init.cache, 'no-store');
  assert.equal(seen.init.headers.get(CSRF_HEADER), 'tok');
  assert.equal(seen.init.headers.get('Content-Type'), 'application/json');
  await sendRequest('/y');
  assert.equal(seen.init.headers.get(CSRF_HEADER), null);
  await sendRequest('/z', { method: 'POST', csrf: '' });
  assert.equal(seen.init.headers.get(CSRF_HEADER), '');
});

test('job poller repeats while a job is active, then settles once', async () => {
  assert.equal(isActiveJob('queued'), true);
  assert.equal(isActiveJob('complete'), false);
  const statuses = ['queued', 'in_progress', 'complete']; const log = [];
  const poller = createJobPoller({
    intervalMs: 1,
    fetchJob: async () => ({ status: statuses.shift() }),
    onActive: (job, opts) => log.push(['active', job.status, opts]),
    onSettled: (job, opts) => { log.push(['done', job.status, opts]); },
    onError: () => log.push(['error']),
  });
  await poller.poll('j1', 'first');
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.deepEqual(log, [['active', 'queued', 'first'], ['active', 'in_progress', undefined], ['done', 'complete', undefined]]);
});

test('job poller drops stale results and reports errors', async () => {
  const log = [];
  const stalePoller = createJobPoller({
    fetchJob: async () => ({ status: 'complete' }), begin: () => () => true,
    onActive: () => log.push('active'), onSettled: () => log.push('done'), onError: () => log.push('error'),
  });
  await stalePoller.poll('j');
  assert.deepEqual(log, []);
  const failing = createJobPoller({
    fetchJob: async () => { throw new Error('boom'); },
    onActive() {}, onSettled() {}, onError: (error) => log.push(error.message),
  });
  await failing.poll('j');
  assert.deepEqual(log, ['boom']);
});

test('stopping a poller cancels the next scheduled read', async () => {
  let reads = 0;
  const poller = createJobPoller({ intervalMs: 5, fetchJob: async () => { reads += 1; return { status: 'queued' }; },
    onActive() {}, onSettled() {}, onError() {} });
  await poller.poll('j'); poller.stop();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(reads, 1);
});
