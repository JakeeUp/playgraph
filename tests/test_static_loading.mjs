import test from 'node:test';
import assert from 'node:assert/strict';

// A minimal stand-in for the few DOM calls these modules make. Each fake
// element records whether lazy loading was already set when src was assigned.
class FakeElement {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.hidden = false; this.textContent = ''; this.classList = { add() {}, toggle() {} }; this.dataset = {}; }
  set src(value) { this.srcSetWhileLazy = this.loading === 'lazy'; this._src = value; }
  get src() { return this._src; }
  append(...nodes) { this.children.push(...nodes); }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  remove() { this.removed = true; }
}
const elements = new Map();
globalThis.document = {
  createElement: (tag) => new FakeElement(tag),
  querySelector: (selector) => { if (!elements.has(selector)) elements.set(selector, new FakeElement('div')); return elements.get(selector); },
  head: new FakeElement('head'),
  documentElement: new FakeElement('html'),
};

test('cover art and welcome wall images are lazy before their source is set', async () => {
  const { cover, wallArt } = await import('../app/static/dom.js');
  const frame = cover({ name: 'Alpha', steam_appid: 620 });
  const img = frame.children.find((node) => node.tagName === 'IMG');
  assert.equal(img.loading, 'lazy');
  assert.equal(img.srcSetWhileLazy, true);
  assert.equal(img.width, 600); assert.equal(img.height, 900);

  const wall = wallArt(570);
  assert.equal(wall.loading, 'lazy');
  assert.equal(wall.srcSetWhileLazy, true);
  assert.equal(wall.decoding, 'async');
  assert.equal(wall.alt, '');
  assert.equal(wall.width, 600); assert.equal(wall.height, 900);
  assert.match(wall.src, /\/570\/library_600x900\.jpg$/);
  wall.listeners.error();
  assert.equal(wall.removed, true);
});

test('a PlayStation game keeps the title placeholder and never requests Steam art', async () => {
  const { cover } = await import('../app/static/dom.js');
  const frame = cover({ name: 'PS only', steam_appid: null });
  assert.equal(frame.children.some((node) => node.tagName === 'IMG'), false);
  assert.equal(frame.children[0].textContent, 'PS only');
});

test('the account page asks for its settings and the session at the same time', async () => {
  const requested = [];
  let releaseConfig;
  globalThis.location = { search: '' };
  globalThis.history = { replaceState() {} };
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' });
  globalThis.fetch = (url) => {
    requested.push(url);
    if (url === '/auth/config') return new Promise((resolve) => { releaseConfig = resolve; });
    // A failing session check must not surface when sign-in is disabled.
    return Promise.resolve({ ok: false, status: 503, json: async () => ({}) });
  };
  await import('../app/static/account.js');
  assert.deepEqual(requested.sort(), ['/auth/config', '/auth/session']);
  releaseConfig({ ok: true, json: async () => ({ enabled: false }) });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.match(document.querySelector('#account-status').textContent, /not enabled/);
  assert.equal(document.querySelector('#retry-account').hidden, true);
});
