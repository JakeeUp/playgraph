import assert from 'node:assert/strict';
import test, { afterEach } from 'node:test';
import { accessibility, initAccessibility, focusHeading } from '../app/static/accessibility.js';
import { actionCountLabel } from '../app/static/post.js';
import { barList } from '../app/static/chart.js';
import { createNews } from '../app/static/news.js';

const original = Object.fromEntries(['document', 'matchMedia', 'localStorage', 'IntersectionObserver', 'scrollTo'].map((key) => [key, globalThis[key]]));
afterEach(() => { for (const [key, value] of Object.entries(original)) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; } });

class Node {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.checked = false; this.disabled = false;
    this.hidden = false; this.textContent = ''; this.className = ''; this.isConnected = false;
    this.classList = { add() {}, toggle() {} }; this.style = { setProperty() {} };
  }
  setAttribute(key, value) { this[key] = String(value); }
  removeAttribute(key) { delete this[key]; }
  toggleAttribute(key, on) { if (on) this[key] = ''; else delete this[key]; }
  addEventListener(event, action) { this.listeners[event] = action; }
  append(...children) { this.children.push(...children); this.connect(this.isConnected); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  connect(value) { this.isConnected = value; for (const child of this.children) if (child instanceof Node) child.connect(value); }
  querySelector(selector) {
    for (const child of this.children) {
      if (!(child instanceof Node)) continue;
      if (selector.startsWith('.') ? child.className.split(' ').includes(selector.slice(1)) : child.tagName === selector.toUpperCase()) return child;
      const found = child.querySelector(selector); if (found) return found;
    }
    return null;
  }
  closest() { return this.hiddenAncestor ? {} : null; }
  focus() { document.activeElement = this; }
  scrollIntoView(options) { this.scrolled = options; }
}

function setup({ saved = null, systemMotion = false, blocked = false } = {}) {
  const ids = new Map(['a11y-motion', 'a11y-contrast', 'a11y-shortcuts', 'a11y-motion-help', 'accessibility-settings', 'accessibility-open',
    'search', 'news-items', 'news-more', 'news-status'].map((key) => [key, new Node()]));
  const summary = new Node('summary'); ids.get('accessibility-settings').append(summary);
  const hint = new Node('kbd'); const html = new Node('html'); const body = new Node('body');
  const media = { matches: systemMotion, addEventListener(event, action) { this[event] = action; } };
  let stored = saved;
  globalThis.document = { documentElement: html, body, activeElement: body, getElementById: (id) => ids.get(id) || null,
    querySelector: (selector) => selector === '.search kbd' ? hint : ids.get(selector.slice(1)), createElement: (tag) => new Node(tag) };
  for (const node of ids.values()) node.connect(true);
  globalThis.matchMedia = () => media;
  globalThis.localStorage = { getItem() { if (blocked) throw new Error('Blocked'); return stored; }, setItem(_, value) { if (blocked) throw new Error('Blocked'); stored = value; } };
  return { ids, hint, html, media, summary, saved: () => stored };
}

test('motion, contrast and shortcut choices apply immediately and persist safely', () => {
  const ui = setup(); initAccessibility();
  for (const id of ['a11y-motion', 'a11y-contrast']) { ui.ids.get(id).checked = true; ui.ids.get(id).listeners.change(); }
  assert.equal(ui.html['data-reduced-motion'], ''); assert.equal(ui.html['data-high-contrast'], '');
  ui.ids.get('a11y-shortcuts').checked = false; ui.ids.get('a11y-shortcuts').listeners.change();
  assert.equal(accessibility.shortcuts, false); assert.equal(ui.hint.hidden, true);
  assert.equal(ui.ids.get('search')['aria-keyshortcuts'], undefined);
  const reloaded = setup({ saved: ui.saved() }); initAccessibility();
  assert.equal(reloaded.ids.get('a11y-motion').checked, true);
  assert.equal(reloaded.ids.get('a11y-contrast').checked, true);
  assert.equal(reloaded.ids.get('a11y-shortcuts').checked, false);
});

test('device reduced motion wins and follows later device preference changes', () => {
  const ui = setup({ systemMotion: true }); initAccessibility();
  assert.equal(ui.ids.get('a11y-motion').checked, true); assert.equal(ui.ids.get('a11y-motion').disabled, true);
  ui.media.matches = false; ui.media.change();
  assert.equal(ui.ids.get('a11y-motion').checked, false); assert.equal(ui.ids.get('a11y-motion').disabled, false);
  assert.equal(ui.html['data-reduced-motion'], undefined);
});

test('malformed preferences and blocked browser storage never stop the app', () => {
  setup({ saved: '{broken' }); assert.doesNotThrow(initAccessibility); assert.equal(accessibility.shortcuts, true);
  const ui = setup({ blocked: true }); initAccessibility();
  ui.ids.get('a11y-motion').checked = true;
  assert.doesNotThrow(() => ui.ids.get('a11y-motion').listeners.change());
  assert.equal(ui.html['data-reduced-motion'], '');
});

test('the header control opens settings and gives keyboard focus to its summary', () => {
  const ui = setup(); initAccessibility(); ui.ids.get('accessibility-open').listeners.click();
  assert.equal(ui.ids.get('accessibility-settings').open, true);
  assert.equal(document.activeElement, ui.summary);
  ui.ids.get('accessibility-settings').listeners.toggle();
  assert.equal(ui.ids.get('accessibility-open')['aria-expanded'], 'true');
});

test('page-heading focus is visible and never moves into a hidden view', () => {
  setup(); let scrolled = null; globalThis.scrollTo = (options) => { scrolled = options; };
  const heading = new Node('h1'); focusHeading(heading);
  assert.equal(document.activeElement, heading); assert.equal(heading.tabIndex, -1);
  // The page opens at its top; the heading itself is never scrolled.
  assert.deepEqual(scrolled, { top: 0, behavior: 'instant' }); assert.equal(heading.scrolled, undefined);
  const hidden = new Node('h1'); hidden.hiddenAncestor = true; focusHeading(hidden);
  assert.equal(document.activeElement, heading); assert.doesNotThrow(() => focusHeading(null));
});

test('social action names include the visible compact count and the exact total', () => {
  assert.equal(actionCountLabel('Like', 1240, 'likes'), 'Like, 1.2K likes (1,240 total)');
  assert.equal(actionCountLabel('Unlike', 1, 'like'), 'Unlike, 1 like');
  assert.equal(actionCountLabel('Reply', 0, 'comments'), 'Reply, 0 comments');
});

test('chart rows keep visible names and include the detail without a conflicting label override', () => {
  setup(); const lead = new Node(); const list = barList([{ label: 'Hades', value: 12, text: '12 h', detail: '720 minutes played', lead, onSelect() {} }]);
  const hit = list.children[0].children[0];
  assert.equal(hit.tagName, 'BUTTON'); assert.equal(hit['aria-label'], undefined);
  assert.equal(lead['aria-hidden'], 'true'); assert.equal(hit.children.at(-1).textContent, '720 minutes played');
});

const article = (title) => ({ title, source: 'Source', url: `https://example.com/${title}`, published_at: '2026-01-01T00:00:00Z' });
const settle = () => new Promise((resolve) => setImmediate(resolve));

test('headlines only page explicitly and focus the first new headline after loading', async () => {
  const ui = setup(); let calls = 0; let finish;
  globalThis.IntersectionObserver = class { constructor() { throw new Error('Paging must not be automatic'); } };
  const news = createNews({ query: '' }, async () => ++calls === 1 ? { items: [article('First')], next: 1 } : new Promise((resolve) => { finish = resolve; }));
  await news.load(); assert.equal(calls, 1);
  const control = ui.ids.get('news-more').children[0]; control.focus(); control.listeners.click();
  assert.equal(control['aria-disabled'], 'true'); assert.equal(control.disabled, false);
  finish({ items: [article('Second')], next: null }); await settle();
  assert.equal(calls, 2); assert.equal(document.activeElement.textContent, 'Second'); assert.equal(control.hidden, true);
  assert.match(ui.ids.get('news-status').textContent, /2 headlines shown/);
});

test('a late headline response never steals focus after the reader moves elsewhere', async () => {
  const ui = setup(); let calls = 0; let finish;
  const news = createNews({ query: '' }, async () => ++calls === 1 ? { items: [article('First')], next: 1 } : new Promise((resolve) => { finish = resolve; }));
  await news.load(); const control = ui.ids.get('news-more').children[0]; control.focus(); control.listeners.click();
  const other = new Node('button'); other.focus(); finish({ items: [article('Second')], next: null }); await settle();
  assert.equal(document.activeElement, other);
});

test('failed headline pages offer a reachable retry without losing earlier stories', async () => {
  const ui = setup(); let calls = 0;
  const news = createNews({ query: '' }, async () => { calls += 1; if (calls === 2) throw new Error('Unavailable'); return { items: [article(calls === 1 ? 'First' : 'Second')], next: calls === 1 ? 1 : null }; });
  await news.load(); const control = ui.ids.get('news-more').children[0]; control.focus(); control.listeners.click(); await settle();
  assert.equal(control.textContent, 'Try again'); assert.equal(control['aria-disabled'], 'false');
  assert.equal(ui.ids.get('news-items').children.length, 1);
  control.listeners.click(); await settle(); assert.equal(ui.ids.get('news-items').children.length, 2);
});
