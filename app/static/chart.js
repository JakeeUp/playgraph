import { el } from './dom.js';

/**
 * Bar length as a fraction of the largest value in its set. Every bar in a
 * list shares one baseline and one maximum, so lengths compare honestly.
 * @param {number} value
 * @param {number} max
 * @returns {number} Between 0 and 1.
 */
export const barFraction = (value, max) =>
  max > 0 && Number.isFinite(value) ? Math.min(1, Math.max(0, value / max)) : 0;

let tip = null;

function showTip(anchor, row) {
  tip ??= document.body.appendChild(el('div', 'chart-tip'));
  // The same numbers are already visible in the row and in its accessible
  // name, so the tooltip is a visual extra that assistive tech can skip.
  tip.setAttribute('aria-hidden', 'true');
  tip.replaceChildren(el('strong', '', row.text), el('span', '', row.detail));
  tip.hidden = false;
  const box = anchor.getBoundingClientRect();
  const width = tip.offsetWidth;
  const left = Math.min(window.innerWidth - width - 8, Math.max(8, box.left + box.width / 2 - width / 2));
  const above = box.top - tip.offsetHeight - 8;
  tip.style.left = `${left}px`;
  tip.style.top = `${above > 8 ? above : box.bottom + 8}px`;
}

const hideTip = () => { if (tip) tip.hidden = true; };

/**
 * A ranked horizontal bar list: one series in one color, the value at each
 * bar's tip, and a hover/focus tooltip for the detail line. Rows with
 * onSelect become buttons; the whole row is the hit target.
 * @param {Array<{label: string, value: number, text: string, detail: string,
 *   lead?: Node, onSelect?: () => void, name?: string}>} rows
 * @returns {HTMLOListElement}
 */
export function barList(rows) {
  const max = Math.max(0, ...rows.map((row) => row.value));
  const list = el('ol', 'bar-list');
  for (const row of rows) {
    const hit = el(row.onSelect ? 'button' : 'div', 'bar-hit');
    if (row.onSelect) { hit.type = 'button'; hit.addEventListener('click', row.onSelect); }
    else hit.tabIndex = 0;
    hit.setAttribute('aria-label', row.name ?? `${row.label}, ${row.text}, ${row.detail}`);
    const track = el('span', 'bar-track');
    const bar = el('span', 'bar');
    bar.style.setProperty('--fraction', String(barFraction(row.value, max)));
    track.append(bar, el('span', 'bar-value', row.text));
    if (row.lead) hit.append(row.lead);
    hit.append(el('span', 'bar-label', row.label), track);
    for (const [event, action] of [['pointerenter', () => showTip(hit, row)], ['focus', () => showTip(hit, row)],
      ['pointerleave', hideTip], ['blur', hideTip]]) hit.addEventListener(event, action);
    const item = el('li', 'bar-row'); item.append(hit); list.append(item);
  }
  return list;
}

// Guarded so the pure helpers above still import under Node's test runner.
globalThis.addEventListener?.('scroll', hideTip, { passive: true });
globalThis.addEventListener?.('keydown', (event) => { if (event.key === 'Escape') hideTip(); });
