import { el, button } from './dom.js';

export function ratingAtPosition(position, width) {
  if (!(width > 0) || !Number.isFinite(position)) return 0;
  return Math.max(0.5, Math.min(5, Math.ceil(position / width * 10) / 2));
}

export function starDisplay(value) {
  const stars = el('span', 'rating-stars'); stars.setAttribute('aria-hidden', 'true');
  function shape() {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', 'M12 2 15.1 8.3 22 9.3 17 14.2 18.2 21.1 12 17.8 5.8 21.1 7 14.2 2 9.3 8.9 8.3Z');
    svg.append(path); return svg;
  }
  for (let index = 0; index < 5; index += 1) {
    const star = el('span', 'rating-star');
    const fill = el('span', `star-fill ${value >= index + 1 ? 'full' : value >= index + 0.5 ? 'half' : ''}`);
    fill.append(shape()); star.append(shape(), fill);
    stars.append(star);
  }
  return stars;
}

export function createRatingPicker() {
  const root = el('div', 'rating-picker');
  const label = el('label', '', 'Your rating');
  const track = el('span', 'rating-track');
  const range = el('input', 'rating-input');
  range.type = 'range'; range.min = '0'; range.max = '5'; range.step = '0.5'; range.value = '0'; range.name = 'rating';
  range.setAttribute('aria-label', 'Your rating, zero to five stars in half-star steps');
  const display = el('output', 'rating-value', 'Not rated');
  let dragging = false;
  let startingValue = 0;
  function paint(value) {
    track.querySelector('.rating-stars')?.remove();
    track.prepend(starDisplay(value));
    display.textContent = value ? `${value} / 5` : 'Not rated';
  }
  function commit(value) {
    range.value = String(value);
    range.setAttribute('aria-valuetext', value ? `${value} out of 5 stars` : 'Not rated');
    paint(value);
  }
  function pointerValue(event) {
    const rect = track.getBoundingClientRect();
    return ratingAtPosition(event.clientX - rect.left, rect.width);
  }
  range.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || !event.isPrimary) return;
    event.preventDefault(); range.focus(); startingValue = Number(range.value); dragging = true;
    range.setPointerCapture(event.pointerId); commit(pointerValue(event));
  });
  range.addEventListener('pointermove', (event) => {
    if (dragging) commit(pointerValue(event)); else if (event.pointerType === 'mouse') paint(pointerValue(event));
  });
  range.addEventListener('pointerup', (event) => {
    if (!dragging) return;
    commit(pointerValue(event)); dragging = false;
    if (range.hasPointerCapture(event.pointerId)) range.releasePointerCapture(event.pointerId);
  });
  range.addEventListener('pointercancel', () => { dragging = false; commit(startingValue); });
  range.addEventListener('pointerleave', () => { if (!dragging) paint(Number(range.value)); });
  range.addEventListener('input', () => commit(Number(range.value)));
  range.addEventListener('blur', () => paint(Number(range.value)));
  track.append(range); label.append(track);
  root.append(label, display, button('Clear', 'text-button rating-clear', () => { commit(0); range.focus(); }),
    el('p', 'helper', 'Drag, tap, or use the arrow keys. Half-stars welcome.'));
  commit(0);
  return { element: root, input: range, value: () => Number(range.value) };
}
