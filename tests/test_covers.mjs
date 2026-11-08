import test from 'node:test';
import assert from 'node:assert/strict';
import { hashString, placeholderFor, monogram, contrast, pairContrast, PALETTE } from '../app/static/covers.js';

test('the same title always gets the same placeholder', () => {
  assert.deepEqual(placeholderFor('Mire'), placeholderFor('Mire'));
  assert.deepEqual(placeholderFor('Mire'), placeholderFor('  mire '));
  assert.equal(hashString('a'), 0xe40c292c);
});

test('titles spread across the palette', () => {
  const names = ['Hollow Ascent', 'Orbital Freight Co.', 'Mire', 'Duskfall Tactics', 'Neon Vector', 'Quarry',
    'The Long Signal', 'Ironline', 'Garden of Forks', 'Cold Harbor', 'Blender', 'ShareX'];
  assert.ok(new Set(names.map((n) => placeholderFor(n).from)).size >= 6);
});

test('every palette pair keeps white text at 4.5:1 or better', () => {
  for (const pair of PALETTE) assert.ok(pairContrast(pair) >= 4.5, `${pair} -> ${pairContrast(pair).toFixed(2)}`);
  assert.ok(Math.abs(contrast([255, 255, 255], [0, 0, 0]) - 21) < 0.01);
});

test('monogram skips punctuation and handles empty titles', () => {
  assert.equal(monogram('"quarry"'), 'Q');
  assert.equal(monogram('éclair'), 'É');
  assert.equal(monogram('***'), '?');
  assert.equal(monogram(null), '?');
});
