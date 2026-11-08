// Generated covers for games without artwork. Pure functions, no DOM: the
// same title always gets the same cover. Each one is a flat poster: one
// solid fill, one solid shape in a second colour, and the title set in the
// top-left corner in white or ink, whichever reads better on the fill. No
// gradients, no texture, no transparency, so the text contrast is exactly
// the fill's contrast with the ink.
export const WHITE = [255, 255, 255];
export const INK = [17, 17, 17];

// Twelve fills, each with two partner colours for its shape: one from the
// set and the paper tone, picked by the title.
export const PAPER = '#F6F6F3';
export const PALETTE = [
  { fill: '#2B4C7E', partner: '#D9A441' },
  { fill: '#C0392B', partner: '#1B2A49' },
  { fill: '#1F7A5A', partner: '#D9A441' },
  { fill: '#D9A441', partner: '#1B2A49' },
  { fill: '#6B3FA0', partner: '#D9A441' },
  { fill: '#E4572E', partner: '#1B2A49' },
  { fill: '#17A2B8', partner: '#1B2A49' },
  { fill: '#3D3D3A', partner: '#E4572E' },
  { fill: '#8C5A2B', partner: '#D9A441' },
  { fill: '#A4BF3C', partner: '#1F7A5A' },
  { fill: '#D64F7A', partner: '#1B2A49' },
  { fill: '#1B2A49', partner: '#17A2B8' },
];
// One flat shape per cover, drawn by dom.js as SVG in the lower part of the
// jacket so it never runs under the title.
export const SHAPES = ['disc', 'rise', 'bars', 'dots', 'block', 'corner', 'band'];

/** FNV-1a, 32 bit. Stable across runs and platforms. */
export function hashString(text) {
  let hash = 0x811c9dc5;
  for (const char of String(text)) { hash ^= char.codePointAt(0); hash = Math.imul(hash, 0x01000193) >>> 0; }
  return hash >>> 0;
}
export const hex = (value) => [1, 3, 5].map((i) => parseInt(value.slice(i, i + 2), 16));
const channel = (c) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
export const luminance = ([r, g, b]) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
export function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
/** White or ink, whichever has more contrast on the fill. */
export function inkFor(fill) {
  const rgb = hex(fill);
  return contrast(WHITE, rgb) >= contrast(INK, rgb) ? '#FFFFFF' : '#111111';
}

/** Everything a generated cover needs, decided by the title alone. */
export function placeholderFor(title) {
  const hash = hashString(String(title || '').trim().toLocaleLowerCase());
  const { fill, partner } = PALETTE[hash % PALETTE.length];
  const shape = SHAPES[(hash >>> 8) % SHAPES.length];
  const accent = (hash >>> 16) % 3 === 0 ? PAPER : partner;
  return { fill, accent, ink: inkFor(fill), shape };
}
