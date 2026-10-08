import { steamArt, isPlayStationGame, storeArt } from './library.js';
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
// A number in running text, with its h or % unit, so it can be set in the mono face.
const NUMBER = /\d(?:[\d.,\u00a0\u202f]*\d)?(?:\s?(?:h|%))?(?![\p{L}\d])/gu;
export function setNumbers(node, text) {
  node.replaceChildren(); let last = 0;
  for (const match of text.matchAll(NUMBER)) {
    node.append(text.slice(last, match.index), el('span', 'num', match[0])); last = match.index + match[0].length;
  }
  node.append(text.slice(last)); return node;
}
export const numbered = (tag, className, text) => setNumbers(el(tag, className), text);
export const steamLink = (text = 'Sign in') => {
  const link = el('a', 'button primary', text); link.href = '/account'; return link;
};
export const aborted = (error) => error.name === 'AbortError';
export function cover(game) {
  const frame = el('span', 'cover'); frame.append(el('span', 'cover-placeholder', game.name));
  // Games from other stores have no Steam art. They use IGDB's cover, then
  // Sony's own image, then the title placeholder.
  const other = isPlayStationGame(game) ? storeArt(game).cover : null;
  if (isPlayStationGame(game) && !other) return frame;
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
