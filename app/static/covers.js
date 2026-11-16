// Generated covers for games without artwork. Pure functions, no DOM: the
// same title always gets the same cover. Each one is a small piece of key
// art: a dusk sky fading to a lit horizon, a glowing sun or moon, two layers
// of silhouette landscape, and the title set at the top over the darkest part
// of the sky. The title is always white; every sky top keeps it at 4.5:1.
export const WHITE = [255, 255, 255];
export const INK = [17, 17, 17];

// Twelve skies. sky is the top of the gradient (the title sits on it),
// horizon the bottom, glow the sun, far and near the two landscape layers.
export const PALETTE = [
  { sky: '#2A1B3D', horizon: '#F08A5D', glow: '#FFD27A', far: '#6B2F4A', near: '#1E1222' },
  { sky: '#0F2A3A', horizon: '#2F7F86', glow: '#F3E9C6', far: '#1D4B56', near: '#0B1A22' },
  { sky: '#3A1C3F', horizon: '#E07A8F', glow: '#FFE0B0', far: '#7A3557', near: '#1F0F20' },
  { sky: '#1F3B2D', horizon: '#C9B46A', glow: '#FFF1C1', far: '#3D5A3A', near: '#13221A' },
  { sky: '#1B1640', horizon: '#7B5FC7', glow: '#F6D6FF', far: '#3E2F7A', near: '#110D26' },
  { sky: '#2B0F0F', horizon: '#D9572B', glow: '#FFC36B', far: '#6E2416', near: '#1A0909' },
  { sky: '#0D1B3D', horizon: '#3F78C2', glow: '#FFE9B8', far: '#23477F', near: '#0A1328' },
  { sky: '#3B2430', horizon: '#F2A07B', glow: '#FFF0C9', far: '#8A4B4F', near: '#24141B' },
  { sky: '#12302F', horizon: '#5FB3A1', glow: '#F0FFE8', far: '#2A6A60', near: '#0C1D1C' },
  { sky: '#2F2412', horizon: '#E0A63A', glow: '#FFF2C2', far: '#6E4D1C', near: '#1B140A' },
  { sky: '#261229', horizon: '#A8508F', glow: '#FFD6E8', far: '#5A2350', near: '#170A19' },
  { sky: '#1A2233', horizon: '#8AA3C7', glow: '#FFFFFF', far: '#4A5D80', near: '#121826' },
];
// The landscape under the sky, drawn by dom.js as SVG in a 200 x 300 jacket.
export const SHAPES = ['peaks', 'hills', 'city', 'forest', 'waves', 'dunes', 'islands'];

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
  const colors = PALETTE[hash % PALETTE.length];
  const shape = SHAPES[(hash >>> 8) % SHAPES.length];
  // The sun sits somewhere along the lower middle, never under the title.
  const sunX = 22 + ((hash >>> 16) % 57);           // 22% to 78% across
  const sunY = 50 + ((hash >>> 24) % 16);           // 50% to 65% down
  return { ...colors, ink: inkFor(colors.sky), shape, sunX, sunY };
}
