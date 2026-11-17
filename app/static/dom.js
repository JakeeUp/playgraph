import { steamArt, storeArt } from './library.js';
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
// With PlayGraph accounts (Clerk) switched off, Steam is the only way in, so
// every sign-in action goes straight there instead of to an account page that
// has nothing else to offer.
export const signIn = { steamOnly: false };
export const steamLink = (text = 'Sign in') => {
  if (demo.on) { const control = el('button', 'button primary', 'View as demo player'); control.addEventListener('click', demoLogin); return control; }
  if (signIn.steamOnly) { const link = el('a', 'button primary', 'Sign in with Steam'); link.href = '/auth/steam/login?ui=1'; return link; }
  const link = el('a', 'button primary', text); link.href = '/account'; return link;
};
export const aborted = (error) => error.name === 'AbortError';
// Deterministic title cover: a little piece of key art made from the title
// alone. The sky and the sun are CSS gradients; the two landscape layers are
// SVG. It is decorative (aria-hidden): the game's name is always in the text
// beside it or in its button's label, so the name is spoken once.
const SVG_NS = 'http://www.w3.org/2000/svg';
function svgNode(tag, attributes) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
  return node;
}
// Rows of shapes along a baseline, for skylines and tree lines.
const skyline = (base, widths, heights) => {
  let x = 0; let d = `M0 300V${base}`;
  widths.forEach((w, i) => { const top = base - heights[i % heights.length]; d += `H${x}V${top}H${x + w}V${base}`; x += w; });
  return `${d}H200V300Z`;
};
const pines = (base, size, step, offset) => {
  let d = `M0 300V${base}`;
  for (let x = offset - step; x < 200 + step; x += step) {
    const h = size * (0.75 + ((x * 7) % 5) / 10);
    d += `L${x} ${base}L${x + step / 2} ${base - h}L${x + step} ${base}`;
  }
  return `${d}L200 ${base}V300Z`;
};
// Each scene is [far, near], both resting on the bottom of a 200 x 300 jacket.
const SCENES = {
  peaks: () => ['M0 300V214L32 168 54 192 92 132 124 182 150 150 178 178 200 160V300Z',
    'M0 300V252L38 216 70 240 112 204 150 246 182 224 200 236V300Z'],
  hills: () => ['M0 300V222Q50 178 102 210T200 196V300Z', 'M0 300V254Q64 214 124 246T200 238V300Z'],
  city: () => [skyline(236, [14, 18, 12, 22, 16, 20, 14, 24, 18, 22, 20], [42, 70, 30, 88, 54, 36, 76, 48, 96, 40, 60]),
    skyline(266, [26, 20, 30, 24, 28, 22, 30, 20], [28, 44, 20, 52, 34, 26, 40, 30])],
  forest: () => [pines(232, 40, 22, 6), pines(262, 58, 30, 0)],
  waves: () => ['M0 300V230Q25 218 50 230T100 230T150 230T200 230V300Z',
    'M0 300V256Q33 242 66 256T133 256T200 256V300Z'],
  dunes: () => ['M0 300V226C60 196 110 236 200 206V300Z', 'M0 300V250C70 266 120 222 200 252V300Z'],
  islands: () => ['M0 300V246H40Q66 204 96 236Q120 214 148 246H200V300Z', 'M0 300V262H200V300Z'],
};
// A few small stars, the same ones for the same title.
const STARS = [[30, 64], [62, 96], [146, 58], [172, 104], [104, 82], [18, 132], [186, 140], [128, 120]];
export function placeholder(game) {
  const art = placeholderFor(game.name);
  const node = el('span', `cover-placeholder scene-${art.shape}`); node.setAttribute('aria-hidden', 'true');
  node.dataset.title = game.name || '';
  for (const name of ['sky', 'horizon', 'glow', 'far', 'near', 'ink']) node.style.setProperty(`--ph-${name}`, art[name]);
  node.style.setProperty('--ph-sun-x', `${art.sunX}%`); node.style.setProperty('--ph-sun-y', `${art.sunY}%`);
  const svg = svgNode('svg', { viewBox: '0 0 200 300', preserveAspectRatio: 'xMidYMax slice', class: 'ph-art' });
  const stars = svgNode('g', { class: 'ph-stars' });
  STARS.forEach(([x, y], i) => { if ((art.sunX + i) % 3) stars.append(svgNode('circle', { cx: x, cy: y, r: i % 2 ? 0.9 : 1.4 })); });
  const [far, near] = SCENES[art.shape]();
  svg.append(stars, svgNode('path', { d: far, class: 'ph-far' }), svgNode('path', { d: near, class: 'ph-near' }));
  node.append(svg, el('span', 'ph-title', game.name || ''));
  return node;
}
// Wide art for a banner behind a page heading or a game page: the game's own
// hero art when it has some, otherwise its generated scene stretched wide and
// softened. Always decorative.
export function heroArt(game) {
  const appid = game?.steam_appid; const other = appid == null ? storeArt(game) : null;
  if ((other && !other.hero) || (demo.on && !other?.hero)) {
    const scene = placeholder(game); scene.classList.add('hero-art', 'scene');
    // Stretched wide, the landscape squashes into a low horizon instead of
    // being cropped to nothing but ground.
    scene.querySelector('.ph-art').setAttribute('preserveAspectRatio', 'none'); return scene;
  }
  const art = el('img', 'hero-art'); art.alt = ''; art.decoding = 'async'; art.referrerPolicy = 'no-referrer';
  if (other) {
    art.classList.toggle('soft', other.soft); art.addEventListener('error', () => art.remove());
    art.src = other.hero; return art;
  }
  // Not every game has hero art. The store header, softened, still gives the colour.
  art.addEventListener('error', () => {
    if (art.classList.contains('soft')) { art.remove(); return; }
    art.classList.add('soft'); art.src = steamArt(appid, 'header.jpg');
  });
  art.src = steamArt(appid, 'library_hero.jpg'); return art;
}
// The signature: a tilted wall of covers drifting slowly behind a heading.
// Each row holds its covers twice, so sliding it by half its width loops
// without a seam; alternate rows run the other way. Decorative only.
export function coverWall(games, { rows = 3, perRow = 9 } = {}) {
  const wall = el('div', 'cover-wall'); wall.setAttribute('aria-hidden', 'true');
  const pool = games.filter(Boolean); if (!pool.length) return wall;
  for (let row = 0; row < rows; row += 1) {
    const strip = el('div', 'wall-row'); const track = el('div', 'wall-track');
    const picks = Array.from({ length: perRow }, (_, i) => pool[(row * perRow + i * (rows + 1) + row) % pool.length]);
    for (const game of [...picks, ...picks]) { const art = cover(game); art.classList.add('wall-cover'); track.append(art); }
    strip.append(track); wall.append(strip);
  }
  return wall;
}
export function cover(game) {
  const frame = el('span', 'cover'); frame.append(placeholder(game));
  // Games from other stores have no Steam art. They use IGDB's cover, then
  // Sony's own image, then the title placeholder. The demo's synthetic Steam
  // ids have no art either, so it never asks Steam for any.
  const other = game?.steam_appid == null ? storeArt(game).cover : null;
  if ((game?.steam_appid == null && !other) || (demo.on && !other)) return frame;
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

export function loadingState(text = 'Loading…') {
  const node = el('div', 'loading-state'); node.setAttribute('role', 'status');
  const ring = el('span', 'loading-ring'); ring.setAttribute('aria-hidden', 'true');
  node.append(ring, el('span', '', text)); return node;
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
