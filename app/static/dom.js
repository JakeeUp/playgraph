import { steamArt } from './library.js';
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
export const steamLink = (text = 'Connect Steam') => {
  const link = el('a', 'button primary', text); link.href = '/auth/steam/login?ui=1'; return link;
};
export const aborted = (error) => error.name === 'AbortError';
export function cover(game) {
  const frame = el('span', 'cover'); frame.append(el('span', 'cover-placeholder', game.name));
  const img = el('img');
  img.src = steamArt(game.steam_appid, 'library_600x900.jpg');
  img.alt = ''; img.loading = 'lazy'; img.decoding = 'async'; img.referrerPolicy = 'no-referrer';
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
