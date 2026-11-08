import { steamArt, isPlayStationGame, storeArt } from './library.js';
import { placeholderFor } from './covers.js';
import { sendRequest, AUTH_HEADER } from './net.js';
export const $ = (selector) => document.querySelector(selector);
export const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
export const button = (text, className, action) => {
  const node = el('button', className, text); node.type = 'button';
  node.addEventListener('click', action); return node;
};
// The digits of a number in running text, wrapped so they set in tabular
// figures and line up in columns. Units (h, %) stay outside the wrapper.
const NUMBER = /\d(?:[\d.,\u00a0\u202f]*\d)?(?![\p{L}\d])/gu;
export function setNumbers(node, text) {
  node.replaceChildren(); let last = 0;
  for (const match of text.matchAll(NUMBER)) {
    node.append(text.slice(last, match.index), el('span', 'num', match[0])); last = match.index + match[0].length;
  }
  node.append(text.slice(last)); return node;
}
export const numbered = (tag, className, text) => setNumbers(el(tag, className), text);
// Set from /auth/config at startup. On the demo, sign-in is one fixed sample player.
export const demo = { on: false };
async function demoLogin(event) {
  const control = event.currentTarget; control.disabled = true;
  try {
    const response = await sendRequest('/demo/login', { method: 'POST', headers: { [AUTH_HEADER]: '1' } });
    if (response.ok) { location.reload(); return; }
  } catch { /* fall through */ }
  control.textContent = 'Demo unavailable, try again'; control.disabled = false;
}
export const steamLink = (text = 'Sign in') => {
  if (demo.on) { const control = el('button', 'button primary', 'View as demo player'); control.addEventListener('click', demoLogin); return control; }
  const link = el('a', 'button primary', text); link.href = '/account'; return link;
};
export const aborted = (error) => error.name === 'AbortError';
// Deterministic title cover: a flat poster made from the title alone. One
// solid fill, one solid shape in a second colour, the title in the top-left
// corner. It is decorative (aria-hidden): the game's name is always in the
// text beside it or in its button's label, so the name is spoken once.
const SVG_NS = 'http://www.w3.org/2000/svg';
function svgNode(tag, attributes) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
  return node;
}
// Each shape lives in the lower part of a 200 x 300 jacket, under the title.
const SHAPE_DRAW = {
  disc: () => [svgNode('circle', { cx: 112, cy: 212, r: 62 })],
  rise: () => [svgNode('path', { d: 'M10 300a90 90 0 0 1 180 0z' })],
  bars: () => [150, 186, 222, 258].map((y, i) => svgNode('rect', { x: 0, y, width: 200 - i * 34, height: 20 })),
  dots: () => Array.from({ length: 16 }, (_, i) => svgNode('circle', { cx: 46 + (i % 4) * 36, cy: 160 + Math.floor(i / 4) * 36, r: 9 })),
  block: () => [svgNode('rect', { x: 64, y: 150, width: 136, height: 150 })],
  corner: () => [svgNode('path', { d: 'M200 120v180H20A180 180 0 0 1 200 120z' })],
  band: () => [svgNode('rect', { x: 112, y: 150, width: 32, height: 150 }), svgNode('rect', { x: 152, y: 196, width: 32, height: 104 })],
};
export function placeholder(game) {
  const { fill, accent, ink, shape } = placeholderFor(game.name);
  const node = el('span', `cover-placeholder shape-${shape}`); node.setAttribute('aria-hidden', 'true');
  node.dataset.title = game.name || '';
  node.style.setProperty('--ph-fill', fill); node.style.setProperty('--ph-accent', accent); node.style.setProperty('--ph-ink', ink);
  const art = svgNode('svg', { viewBox: '0 0 200 300', preserveAspectRatio: 'xMidYMid slice', class: 'ph-art' });
  art.append(...SHAPE_DRAW[shape]());
  node.append(art, el('span', 'ph-title', game.name || ''));
  return node;
}
export function cover(game) {
  const frame = el('span', 'cover'); frame.append(placeholder(game));
  // Games from other stores have no Steam art. They use IGDB's cover, then
  // Sony's own image, then the title placeholder. The demo's synthetic Steam
  // ids have no art either, so it never asks Steam for any.
  const other = isPlayStationGame(game) ? storeArt(game).cover : null;
  if ((isPlayStationGame(game) && !other) || (demo.on && !other)) return frame;
  const img = el('img');
  // loading is set before src: Firefox ignores lazy loading set after src.
  img.alt = `${game.name} cover art`; img.width = 600; img.height = 900;
  img.loading = 'lazy'; img.decoding = 'async'; img.referrerPolicy = 'no-referrer';
  if (other) {
    img.src = other; img.addEventListener('error', () => img.remove());
    frame.append(img); return frame;
  }
  img.src = steamArt(game.steam_appid, 'library_600x900.jpg');
  // Not every app has portrait library art. The landscape store header exists
  // for nearly all of them, so it is the one fallback before the text placeholder.
  let fallbackUsed = false;
  img.addEventListener('error', () => {
    if (fallbackUsed) { img.remove(); return; }
    fallbackUsed = true; img.classList.add('header-fallback'); img.src = steamArt(game.steam_appid, 'header.jpg');
  });
  frame.append(img); return frame;
}
export function timeLabel(value) {
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
  return Number.isNaN(date.valueOf()) ? '' : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}
export function formError(target, error) {
  if (!aborted(error) && target.isConnected) { target.textContent = error.message; target.hidden = false; }
}

// Copy text to the clipboard. True when it worked, false when the browser said no.
export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

// A loading, empty or error block: a title, one sentence saying why, and an
// action. `kind` is 'empty' or 'error'; `actions` are already-built buttons
// or links. Errors are announced, empties are not.
export function emptyState({ kind = 'empty', title, text, actions = [], className = '' }) {
  const node = el('div', `empty-message is-${kind} ${className}`.trim());
  if (kind === 'error') node.setAttribute('role', 'alert');
  node.append(el('strong', '', title));
  if (text) node.append(el('span', '', text));
  if (actions.length) { const row = el('div', 'empty-actions'); row.append(...actions); node.append(row); }
  return node;
}

// The verified-hours stamp: a check, the hours and the word "verified", in
// the one green the interface has. Hours come from the reviewer's linked
// library at the moment of posting.
export function stamp(text) {
  const node = el('span', 'stamp');
  const check = svgNode('svg', { viewBox: '0 0 16 16', 'aria-hidden': 'true', class: 'stamp-check' });
  check.append(svgNode('path', { d: 'M3.5 8.5 6.5 11.5 12.5 4.5' }));
  node.append(check, numbered('span', '', text)); return node;
}
// The proportional hours bar under a stamp: this reviewer's hours against the
// longest verified playtime on the game, and a short label against the median.
export function hoursBar(context) {
  const row = el('div', 'hours-context');
  const track = el('span', 'hours-track'); track.setAttribute('aria-hidden', 'true');
  const fill = el('span', 'hours-fill'); fill.style.setProperty('--fraction', String(context.fraction)); track.append(fill);
  row.append(track, numbered('span', 'hours-label', context.label)); return row;
}

// Cover-shaped placeholders with the same footprint as real cards, so the grid
// does not jump when the games arrive. The status text is for screen readers.
export function skeletonGrid(count = 12) {
  const cards = Array.from({ length: count }, () => {
    const card = el('div', 'skeleton-card'); card.setAttribute('aria-hidden', 'true');
    const art = el('span', 'cover skeleton');
    card.append(art, el('span', 'skeleton-line name skeleton'), el('span', 'skeleton-line short skeleton'));
    return card;
  });
  return [el('span', 'sr-only', 'Loading games…'), ...cards];
}

// Placeholder review rows for the feed: a cover and three lines each.
export function skeletonFeed(count = 3) {
  const rows = Array.from({ length: count }, () => {
    const row = el('div', 'feed-card skeleton-feed'); row.setAttribute('aria-hidden', 'true');
    const lines = el('div', 'skeleton-lines');
    lines.append(el('span', 'skeleton-line short skeleton'), el('span', 'skeleton-line skeleton'), el('span', 'skeleton-line skeleton'));
    row.append(el('span', 'skeleton-cover skeleton'), lines); return row;
  });
  return [el('span', 'sr-only', 'Loading reviews…'), ...rows];
}
