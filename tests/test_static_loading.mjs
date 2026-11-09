import test from 'node:test';
import assert from 'node:assert/strict';

// A minimal stand-in for the few DOM calls these modules make. Each fake
// element records whether lazy loading was already set when src was assigned.
class FakeElement {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.hidden = false; this.textContent = ''; this.classList = { add() {}, toggle() {} }; this.dataset = {}; this.props = {}; this.style = { setProperty: (name, value) => { this.props[name] = value; } }; }
  setAttribute(name, value) { this[name] = value; }
  set src(value) { this.srcSetWhileLazy = this.loading === 'lazy'; this._src = value; }
  get src() { return this._src; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  remove() { this.removed = true; }
  querySelector(selector) { return this.children.find((node) => node.class === selector.slice(1) || node.className === selector.slice(1)) || null; }
}
const elements = new Map();
globalThis.document = {
  createElement: (tag) => new FakeElement(tag),
  createElementNS: (_ns, tag) => new FakeElement(tag),
  querySelector: (selector) => { if (!elements.has(selector)) elements.set(selector, new FakeElement('div')); return elements.get(selector); },
  head: new FakeElement('head'),
  documentElement: new FakeElement('html'),
};

test('cover art is lazy before its source is set and falls back to the store header', async () => {
  const { cover } = await import('../app/static/dom.js');
  const frame = cover({ name: 'Alpha', steam_appid: 620 });
  const img = frame.children.find((node) => node.tagName === 'IMG');
  assert.equal(img.loading, 'lazy');
  assert.equal(img.srcSetWhileLazy, true);
  assert.equal(img.decoding, 'async');
  assert.equal(img.width, 600); assert.equal(img.height, 900);
  assert.match(img.src, /\/620\/library_600x900\.jpg$/);
  img.listeners.error();
  assert.match(img.src, /\/620\/header\.jpg$/);
  img.listeners.error();
  assert.equal(img.removed, true);
});

test('a PlayStation game keeps the title placeholder and never requests Steam art', async () => {
  const { cover } = await import('../app/static/dom.js');
  const frame = cover({ name: 'PS only', steam_appid: null });
  assert.equal(frame.children.some((node) => node.tagName === 'IMG'), false);
  assert.equal(frame.children[0].dataset.title, 'PS only');
  assert.equal(frame.children[0]['aria-hidden'], 'true');
});

test('in demo mode a Steam game gets the placeholder and no Steam request', async () => {
  const { cover, demo } = await import('../app/static/dom.js');
  demo.on = true;
  try {
    const frame = cover({ name: 'Synthetic', steam_appid: 9000001 });
    assert.equal(frame.children.some((node) => node.tagName === 'IMG'), false);
  } finally { demo.on = false; }
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

test('a generated cover is key art: sky colours, a stars layer, two hills and the title', async () => {
  const { placeholder } = await import('../app/static/dom.js');
  const { placeholderFor } = await import('../app/static/covers.js');
  const node = placeholder({ name: 'Cold Harbor' });
  const expected = placeholderFor('Cold Harbor');
  assert.equal(node.className, `cover-placeholder scene-${expected.shape}`);
  assert.equal(node.dataset.title, 'Cold Harbor');
  assert.equal(node['aria-hidden'], 'true');
  for (const name of ['sky', 'horizon', 'glow', 'far', 'near', 'ink']) assert.equal(node.props[`--ph-${name}`], expected[name]);
  assert.equal(node.props['--ph-sun-x'], `${expected.sunX}%`);
  assert.equal(node.props['--ph-sun-y'], `${expected.sunY}%`);
  const [art, title] = node.children;
  assert.equal(art.tagName, 'SVG');
  assert.deepEqual(art.children.map((child) => child.class), ['ph-stars', 'ph-far', 'ph-near']);
  for (const hill of art.children.slice(1)) assert.match(hill.d, /^M0 300V\d/);
  assert.equal(title.className, 'ph-title'); assert.equal(title.textContent, 'Cold Harbor');
});

test('demo hero art is the generated scene, stretched wide, and never a Steam request', async () => {
  const { heroArt, demo } = await import('../app/static/dom.js');
  demo.on = true;
  try {
    const hero = heroArt({ name: 'Synthetic', steam_appid: 9000001 });
    assert.notEqual(hero.tagName, 'IMG');
    assert.equal(hero['aria-hidden'], 'true');
    assert.equal(hero.children[0].preserveAspectRatio, 'none');
  } finally { demo.on = false; }
});

test('hero art asks for the wide Steam art, then the softened header, then gives up', async () => {
  const { heroArt } = await import('../app/static/dom.js');
  const added = [];
  const hero = heroArt({ name: 'Alpha', steam_appid: 620 });
  hero.classList = { add: (name) => added.push(name), contains: (name) => added.includes(name), toggle() {} };
  assert.equal(hero.tagName, 'IMG'); assert.equal(hero.alt, '');
  assert.match(hero.src, /\/620\/library_hero\.jpg$/);
  hero.listeners.error();
  assert.deepEqual(added, ['soft']); assert.match(hero.src, /\/620\/header\.jpg$/);
  hero.listeners.error();
  assert.equal(hero.removed, true);
});

test('the cover wall is decorative rows that hold each set of covers twice', async () => {
  const { coverWall } = await import('../app/static/dom.js');
  const games = Array.from({ length: 5 }, (_, i) => ({ id: i + 1, name: `Game ${i + 1}`, steam_appid: null }));
  const wall = coverWall(games, { rows: 3, perRow: 4 });
  assert.equal(wall.className, 'cover-wall'); assert.equal(wall['aria-hidden'], 'true');
  assert.equal(wall.children.length, 3);
  for (const row of wall.children) {
    const [track] = row.children; assert.equal(track.className, 'wall-track');
    assert.equal(track.children.length, 8);
    // The second half repeats the first, so sliding by half loops without a seam.
    const titles = track.children.map((frame) => frame.children[0].dataset.title);
    assert.deepEqual(titles.slice(4), titles.slice(0, 4));
  }
  assert.equal(coverWall([]).children.length, 0);
});

test('the verified stamp and hours bar carry their numbers', async () => {
  const { stamp, hoursBar } = await import('../app/static/dom.js');
  const node = stamp('152 h verified');
  assert.equal(node.className, 'stamp');
  assert.equal(node.children[0].tagName, 'SVG');
  const bar = hoursBar({ fraction: 0.25, label: 'About the median' });
  assert.equal(bar.className, 'hours-context');
  const [track] = bar.children;
  assert.equal(track['aria-hidden'], 'true');
  assert.equal(track.children[0].props['--fraction'], '0.25');
});
