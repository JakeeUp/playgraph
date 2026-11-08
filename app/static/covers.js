// Placeholder covers for games without artwork. Pure functions, no DOM: the
// same title always gets the same colors, and every pair keeps white text at
// 4.5:1 or better, pattern and all.
export const INK = [255, 255, 255];
// Hue pairs [light end, dark end]. Dark, slightly desaturated, picked to sit
// next to the slate surfaces and the orange accent without fighting them.
export const PALETTE = [
  ['#14566b', '#0b2a38'], // teal
  ['#34388a', '#181a48'], // indigo
  ['#6a2f7a', '#2f1238'], // plum
  ['#7d2740', '#3a1020'], // wine
  ['#1f6240', '#0f301f'], // forest
  ['#80420f', '#40210a'], // rust
  ['#2f5584', '#162844'], // steel blue
  ['#5a5a16', '#2b2c0b'], // olive
  ['#0f6458', '#0a2f2b'], // sea green
  ['#5b3a8c', '#241643'], // violet
  ['#8a3224', '#42150f'], // brick
  ['#3d4f66', '#1b2531'], // slate
];
// Diagonal hairlines laid over the gradient: white at this alpha.
export const PATTERN_ALPHA = 0.06;
// The monogram's alpha. It is decorative but is still held to 4.5:1.
export const MONOGRAM_ALPHA = 0.9;

/** FNV-1a, 32 bit. Stable across runs and platforms. */
export function hashString(text) {
  let hash = 0x811c9dc5;
  for (const char of String(text)) { hash ^= char.codePointAt(0); hash = Math.imul(hash, 0x01000193) >>> 0; }
  return hash >>> 0;
}
const hex = (value) => [1, 3, 5].map((i) => parseInt(value.slice(i, i + 2), 16));
const channel = (c) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
export const luminance = ([r, g, b]) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
export function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
export const mix = (top, bottom, alpha) => bottom.map((c, i) => Math.round(top[i] * alpha + c * (1 - alpha)));
/** Worst-case contrast of the ink on one palette pair, with the pattern and the monogram blended in. */
export function pairContrast(pair) {
  return Math.min(...pair.flatMap((color) => {
    const base = hex(color); const lined = mix(INK, base, PATTERN_ALPHA);
    return [contrast(INK, base), contrast(INK, lined), contrast(mix(INK, base, MONOGRAM_ALPHA), lined)];
  }));
}
/** The first letter or digit of a title, upper-cased, for the big monogram. */
export function monogram(title) {
  const match = String(title || '').match(/[\p{L}\p{N}]/u);
  return match ? match[0].toLocaleUpperCase() : '?';
}
/** Everything a placeholder needs, decided by the title alone. */
export function placeholderFor(title) {
  const hash = hashString(String(title || '').trim().toLocaleLowerCase());
  const [from, to] = PALETTE[hash % PALETTE.length];
  return { from, to, angle: 135 + ((hash >>> 8) % 4) * 15, letter: monogram(title) };
}
