import test from 'node:test';
import assert from 'node:assert/strict';
import { hashString, placeholderFor, contrast, inkFor, hex, PALETTE, SHAPES, PAPER } from '../app/static/covers.js';

const NAMES = ['Hollow Ascent', 'Orbital Freight Co.', 'Mire', 'Duskfall Tactics', 'Neon Vector', 'Quarry',
  'The Long Signal', 'Ironline', 'Garden of Forks', 'Cold Harbor', 'Blender', 'ShareX'];

test('the same title always gets the same cover', () => {
  assert.deepEqual(placeholderFor('Mire'), placeholderFor('Mire'));
  assert.deepEqual(placeholderFor('Mire'), placeholderFor('  mire '));
  assert.equal(hashString('a'), 0xe40c292c);
});

test('titles spread across the fills and the shapes', () => {
  assert.ok(new Set(NAMES.map((n) => placeholderFor(n).fill)).size >= 6);
  assert.ok(new Set(NAMES.map((n) => placeholderFor(n).shape)).size >= 4);
  for (const n of NAMES) assert.ok(SHAPES.includes(placeholderFor(n).shape));
});

test('every fill keeps its title at 4.5:1 or better with the chosen ink', () => {
  for (const { fill } of PALETTE) {
    const ink = hex(inkFor(fill));
    assert.ok(contrast(ink, hex(fill)) >= 4.5, `${fill} -> ${contrast(ink, hex(fill)).toFixed(2)}`);
  }
  assert.ok(Math.abs(contrast([255, 255, 255], [0, 0, 0]) - 21) < 0.01);
  // The light fills take ink, the dark ones white.
  assert.equal(inkFor('#D9A441'), '#111111');
  assert.equal(inkFor('#1B2A49'), '#FFFFFF');
});

test('the shape is always a flat second colour that differs from the fill', () => {
  for (const n of NAMES) {
    const { fill, accent } = placeholderFor(n);
    assert.match(fill, /^#[0-9A-F]{6}$/); assert.match(accent, /^#[0-9A-F]{6}$/);
    assert.notEqual(fill, accent);
    assert.ok(accent === PAPER || PALETTE.some((pair) => pair.fill === fill && pair.partner === accent));
  }
});

test('empty and missing titles still get a cover', () => {
  for (const title of ['', null, undefined, '***']) {
    const cover = placeholderFor(title);
    assert.ok(PALETTE.some((pair) => pair.fill === cover.fill));
  }
});
