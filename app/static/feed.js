import { $, el, button, cover, steamLink, aborted, setNumbers, skeletonFeed, emptyState, timeLabel } from './dom.js';
import { plural } from './library.js';

export function createFeed(state, api, gameDialog, browse, report) {
  let requestId = 0;
  let mode = 'for_you';
  let rows = [];
  let anchor = null;
  let libraryAnchor = null;
  let total = 0;
  const list = $('#feed-items');
  async function load(append = false) {
    const request = ++requestId;
    const personalized = Boolean(state.user) && mode === 'for_you';
    $('#feed-for-you').disabled = !state.user;
    $('#feed-for-you').classList.toggle('selected', personalized);
    $('#feed-for-you').setAttribute('aria-pressed', String(personalized));
    $('#feed-latest').classList.toggle('selected', !personalized);
    $('#feed-latest').setAttribute('aria-pressed', String(!personalized));
    $('#feed-description').textContent = personalized
      ? 'Matched to your library and the genres you play, from the latest 500 reviews. Software reviews aren’t included.'
      : 'The latest public game reviews and conversations. Software reviews stay on the Software shelf.';
    $('#more-feed').disabled = true; $('#refresh-feed').disabled = true;
    if (!append) { rows = []; anchor = null; libraryAnchor = null; list.setAttribute('aria-busy', 'true'); list.replaceChildren(...skeletonFeed()); $('#more-feed').hidden = true; }
    try {
      const params = new URLSearchParams({ limit: '20', offset: String(rows.length), q: state.query });
      if (anchor !== null) params.set('before', String(anchor));
      if (personalized && libraryAnchor !== null) params.set('library_before', String(libraryAnchor));
      const data = await api(`${personalized ? '/me/feed' : '/feed'}?${params}`);
      if (request !== requestId || state.view !== 'feed') return;
      anchor = data.before; libraryAnchor = data.library_before ?? null; total = data.total; rows = rows.concat(data.items);
      if (!append) list.replaceChildren();
      list.removeAttribute('aria-busy');
      for (const item of data.items) list.append(feedCard(item));
      clampBodies(list);
      if (!rows.length) {
        const actions = [];
        if (personalized) actions.push(button('See latest reviews', 'button secondary', () => { mode = 'latest'; void load(); }));
        actions.push(button('Find a game to review', 'button primary', browse));
        if (!state.user) actions.push(steamLink('Sign in to review'));
        const empty = emptyState({ title: state.query ? 'No reviews match that game' : 'No reviews yet',
          text: personalized ? 'There are no matching reviews from other players yet. Latest reviews also includes your own posts.' : 'Open a game and write one. It’ll show up here for everyone.', actions });
        list.append(empty);
      }
      $('#more-feed').hidden = rows.length >= total || !data.items.length;
    } catch (error) {
      if (request !== requestId || aborted(error)) return;
      list.removeAttribute('aria-busy');
      if (!append) list.replaceChildren(emptyState({ kind: 'error', title: 'Couldn’t load reviews', text: 'Check your connection, then try again.',
        actions: [button('Try again', 'button secondary', () => void load())] }));
      report(error);
    } finally { if (request === requestId) { $('#more-feed').disabled = false; $('#refresh-feed').disabled = false; } }
  }
  // A review as a line in a single column: the game's cover on the left; on
  // the right "Rosa reviewed Cold Harbor" with the date, the verified stamp,
  // the stars, what they wrote, then the conversation.
  function feedCard(item) {
    const card = el('article', 'feed-card'); const openGame = () => gameDialog.open(item.game);
    const review = gameDialog.reviewCard(item.review, false, { byline: false });
    const artwork = button('', 'feed-cover', openGame);
    artwork.setAttribute('aria-label', `Open ${item.game.name}`); artwork.append(cover(item.game));
    const head = el('div', 'reviewed-line'); const line = el('p');
    line.append(el('strong', '', item.review.author_name || `Player ${item.review.user_id}`), ' reviewed ',
      button(item.game.name, 'title-link', openGame));
    head.append(line, el('span', 'date', timeLabel(item.review.created_at)));
    const body = el('div', 'feed-body'); body.append(head);
    if (item.reason) body.append(el('p', 'feed-reason', item.reason));
    body.append(review);
    // The comment count opens the thread; it stands in for "Show comments".
    const toggle = review.querySelector('.review-actions [aria-expanded]');
    const count = button('', 'text-button feed-comments', () => { toggle?.click(); count.setAttribute('aria-expanded', toggle?.getAttribute('aria-expanded') || 'false'); });
    count.setAttribute('aria-expanded', 'false');
    const paint = () => setNumbers(count, `${item.comment_count} ${plural(item.comment_count, 'comment')}`);
    paint(); review.querySelector('.review-actions')?.prepend(count);
    card.addEventListener('comment-published', () => { item.comment_count += 1; paint(); });
    card.append(artwork, body); return card;
  }
  // Long reviews stop at five lines with a control to read the rest.
  function clampBodies(root) {
    requestAnimationFrame(() => {
      for (const body of root.querySelectorAll('.feed-card .review-body:not([data-clamped])')) {
        body.dataset.clamped = '1';
        if (body.scrollHeight <= body.clientHeight + 2) continue;
        const more = button('Read more', 'text-button feed-more', () => {
          const open = body.classList.toggle('expanded'); more.textContent = open ? 'Show less' : 'Read more';
          more.setAttribute('aria-expanded', String(open));
        });
        more.setAttribute('aria-expanded', 'false'); body.after(more);
      }
    });
  }
  $('#feed-for-you').addEventListener('click', () => { mode = 'for_you'; void load(); });
  $('#feed-latest').addEventListener('click', () => { mode = 'latest'; void load(); });
  $('#more-feed').addEventListener('click', () => void load(true));
  $('#refresh-feed').addEventListener('click', () => void load());
  return { load, clear() { requestId += 1; rows = []; anchor = null; libraryAnchor = null; total = 0; list.replaceChildren(); }, invalidate() { requestId += 1; } };
}
