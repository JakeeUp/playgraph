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
// The digits of a number in running text, so they can be set in the mono face.
// Units (h, %) stay in the text face, so "96.4 h" keeps a normal word space.
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
// Deterministic title cover: gradient, hairline pattern and a big initial. It is
// decorative (aria-hidden): the game's name is always in the text beside it or
// in its button's label, so the accessible name is spoken once.
export function placeholder(game) {
  const { from, to, angle, letter } = placeholderFor(game.name);
  const node = el('span', 'cover-placeholder', letter); node.setAttribute('aria-hidden', 'true');
  node.style.setProperty('--ph-from', from); node.style.setProperty('--ph-to', to); node.style.setProperty('--ph-angle', `${angle}deg`);
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
// One decorative cover in the signed-out welcome wall. Lazy, because the
// wall's CSS hides its third row everywhere and more columns on narrow
// screens; a lazy image that is never rendered is never downloaded.
export function wallArt(appid) {
  const img = el('img'); img.alt = ''; img.width = 600; img.height = 900;
  img.loading = 'lazy'; img.decoding = 'async'; img.referrerPolicy = 'no-referrer';
  img.src = steamArt(appid, 'library_600x900.jpg');
  img.addEventListener('error', () => img.remove(), { once: true });
  return img;
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

// Small stroked icons for empty and error states, drawn with the DOM like the
// star rating so the strict CSP never sees inline markup.
const SVG_NS = 'http://www.w3.org/2000/svg';
const ICONS = {
  shelf: ['M4 20V6M10 20V4M16 20V8', 'M3 20h18', 'm19 20-2-12'],
  chat: ['M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-8l-5 4v-4H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z'],
  warning: ['M12 4 2.8 19.5h18.4z', 'M12 10v4.5', 'M12 17.2v.1'],
};
export function icon(name, className = 'empty-icon') {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('class', className);
  for (const d of ICONS[name]) { const path = document.createElementNS(SVG_NS, 'path'); path.setAttribute('d', d); svg.append(path); }
  return svg;
}

// A loading, empty or error panel. `kind` is 'empty' or 'error'; `actions` are
// already-built buttons or links. Errors are announced, empties are not.
export function emptyState({ kind = 'empty', iconName = 'shelf', title, text, actions = [], className = '' }) {
  const node = el('div', `empty-message is-${kind} ${className}`.trim());
  if (kind === 'error') node.setAttribute('role', 'alert');
  node.append(icon(kind === 'error' ? 'warning' : iconName), el('strong', '', title));
  if (text) node.append(el('span', '', text));
  node.append(...actions); return node;
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
