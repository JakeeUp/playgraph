import { $, el, button, aborted, loadingState } from './dom.js';
import { steamArt } from './library.js';

// "3 h ago" for today, a date after that.
export function ageLabel(value, now = Date.now()) {
  const time = Date.parse(value);
  if (Number.isNaN(time)) return '';
  const minutes = Math.max(0, Math.round((now - time) / 60000));
  if (minutes < 60) return `${Math.max(1, minutes)} min ago`;
  if (minutes < 24 * 60) return `${Math.round(minutes / 60)} h ago`;
  return new Date(time).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

// The picture repeats the title link, so it is hidden from the keyboard and
// screen readers. A Steam post without its own picture shows the game's art;
// a picture that fails to load drops out instead of leaving an empty box.
function newsImage(item) {
  const src = item.image || (item.appid ? steamArt(item.appid, 'header.jpg') : '');
  if (!src) return null;
  const link = el('a', 'news-image'); link.href = item.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
  link.tabIndex = -1; link.setAttribute('aria-hidden', 'true');
  const img = el('img'); img.alt = ''; img.loading = 'lazy'; img.decoding = 'async'; img.referrerPolicy = 'no-referrer'; img.src = src;
  img.addEventListener('error', () => link.remove(), { once: true });
  link.append(img); return link;
}

function newsCard(item) {
  const row = el('article', 'news-row');
  const meta = el('p', 'news-meta'); meta.append(el('span', 'news-source', item.source));
  if (item.appid) meta.append(el('span', 'news-tag', 'Steam update'));
  meta.append(el('span', 'date', ageLabel(item.published_at)));
  // Links leave PlayGraph, so they open in a new tab and send no referrer.
  const link = el('a', 'news-title', item.title); link.href = item.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
  const heading = el('h3', 'news-heading'); heading.append(link);
  const image = newsImage(item); if (image) row.append(image);
  row.append(meta, heading);
  if (item.summary) row.append(el('p', 'news-summary', item.summary));
  return row;
}

// Explicit paging keeps focus and the reading position under the reader's control.
export function createNews(state, api) {
  const list = $('#news-items'); const more = $('#news-more'); const status = $('#news-status');
  let items = null; let next = 0; let loading = false; let requestId = 0;
  const cards = new Map();
  const control = button('More headlines', 'button secondary', () => void loadMore()); control.hidden = true;
  const note = el('span', 'helper'); more.replaceChildren(control, note);

  function render() {
    if (!items) return;  // still loading: keep that message
    const query = state.query.trim().toLocaleLowerCase();
    const shown = items.filter((item) => !query || `${item.title} ${item.source}`.toLocaleLowerCase().includes(query));
    list.replaceChildren(...shown.map((item) => cards.get(item.url)));
    if (!shown.length) list.append(el('p', 'empty-message', query ? 'No loaded headlines match that search.' : 'No headlines right now.'));
    control.hidden = next === null; control.setAttribute('aria-disabled', String(loading)); control.textContent = 'More headlines';
    note.textContent = next === null && items.length ? 'You’re all caught up.' : query && next !== null ? 'Search checks loaded headlines. Load more to search further.' : '';
    status.textContent = `${shown.length} ${query ? 'matching ' : ''}headlines shown. ${items.length} loaded.`;
  }
  function add(page) {
    for (const item of page) {
      // A refresh between pages can shift the list; never show a story twice.
      if (cards.has(item.url)) continue;
      cards.set(item.url, newsCard(item)); items.push(item);
    }
  }
  async function loadMore() {
    if (loading || !items || next === null) return;
    const request = requestId; loading = true;
    control.setAttribute('aria-disabled', 'true'); status.textContent = 'Loading more headlines…';
    try {
      const data = await api(`/news?offset=${next}`);
      if (request !== requestId) return;
      const previous = items.length;
      const focused = document.activeElement === control;
      add(data.items); next = data.next; render();
      if (focused) {
        const first = items.slice(previous).find((item) => cards.get(item.url)?.isConnected);
        if (first) cards.get(first.url).querySelector('.news-title')?.focus();
        else if (control.hidden) (list.querySelector('.news-title') || $('#search')).focus();
      }
    } catch (error) {
      if (request !== requestId || aborted(error)) return;
      note.textContent = 'Couldn’t load more headlines. Try again.'; status.textContent = note.textContent; control.textContent = 'Try again';
    } finally { if (request === requestId) { loading = false; control.setAttribute('aria-disabled', 'false'); } }
  }
  async function load() {
    if (items) { render(); return; }
    const request = ++requestId;
    list.replaceChildren(loadingState('Loading headlines…'));
    try {
      const data = await api('/news');
      if (request !== requestId) return;
      items = []; add(data.items); next = data.next; render();
    } catch (error) {
      if (request !== requestId || aborted(error)) return;
      const empty = el('div', 'empty-message');
      empty.append(el('strong', '', 'Couldn’t load headlines'), el('span', '', error.message || 'Try again in a moment.'),
        button('Try again', 'button secondary', () => void load()));
      list.replaceChildren(empty);
    }
  }
  return { load, render, invalidate() { requestId += 1; loading = false; control.setAttribute('aria-disabled', 'false'); } };
}
