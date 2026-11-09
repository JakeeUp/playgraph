import test from 'node:test';
import assert from 'node:assert/strict';
import { hashString, placeholderFor, contrast, inkFor, hex, PALETTE, SHAPES } from '../app/static/covers.js';

const NAMES = ['Hollow Ascent', 'Orbital Freight Co.', 'Mire', 'Duskfall Tactics', 'Neon Vector', 'Quarry',
  'The Long Signal', 'Ironline', 'Garden of Forks', 'Cold Harbor', 'Blender', 'ShareX'];
const COLORS = ['sky', 'horizon', 'glow', 'far', 'near'];

test('the same title always gets the same cover', () => {
  assert.deepEqual(placeholderFor('Mire'), placeholderFor('Mire'));
  assert.deepEqual(placeholderFor('Mire'), placeholderFor('  mire '));
  assert.equal(hashString('a'), 0xe40c292c);
});

test('titles spread across the skies and the scenes', () => {
  assert.ok(new Set(NAMES.map((n) => placeholderFor(n).sky)).size >= 6);
  assert.ok(new Set(NAMES.map((n) => placeholderFor(n).shape)).size >= 4);
  for (const n of NAMES) assert.ok(SHAPES.includes(placeholderFor(n).shape));
});

test('every sky top keeps the white title at 4.5:1 or better', () => {
  for (const { sky } of PALETTE) {
    assert.equal(inkFor(sky), '#FFFFFF', sky);
    assert.ok(contrast([255, 255, 255], hex(sky)) >= 4.5, `${sky} -> ${contrast([255, 255, 255], hex(sky)).toFixed(2)}`);
  }
  assert.ok(Math.abs(contrast([255, 255, 255], [0, 0, 0]) - 21) < 0.01);
  // inkFor still picks ink for a light colour.
  assert.equal(inkFor('#D9A441'), '#111111');
});

test('each sky is a full set of distinct colours, lit toward the horizon', () => {
  const luminance = (color) => contrast(hex(color), [0, 0, 0]);
  for (const colors of PALETTE) {
    for (const name of COLORS) assert.match(colors[name], /^#[0-9A-F]{6}$/, `${name} ${colors[name]}`);
    assert.equal(new Set(COLORS.map((name) => colors[name])).size, COLORS.length);
    // The horizon is brighter than the sky, and the near hills darkest of the land.
    assert.ok(luminance(colors.horizon) > luminance(colors.sky), colors.sky);
    assert.ok(luminance(colors.near) < luminance(colors.far), colors.sky);
  }
});

test('the sun stays in the lower middle, clear of the title', () => {
  for (const n of NAMES) {
    const { sunX, sunY } = placeholderFor(n);
    assert.ok(sunX >= 22 && sunX <= 78, `${n} x ${sunX}`);
    assert.ok(sunY >= 50 && sunY <= 65, `${n} y ${sunY}`);
  }
});

test('empty and missing titles still get a cover', () => {
  for (const title of ['', null, undefined, '***']) {
    const cover = placeholderFor(title);
    assert.ok(PALETTE.some((colors) => colors.sky === cover.sky));
    assert.ok(SHAPES.includes(cover.shape));
  }
});
