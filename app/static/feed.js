import { $, el, button, cover, steamLink, aborted } from './dom.js';

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
      ? 'Reviews from other players, matched to your game library and genres. Ranked within the latest 500 reviews. Software is excluded.'
      : 'The latest public game reviews and conversations. Software reviews stay on the Software shelf.';
    $('#more-feed').disabled = true; $('#refresh-feed').disabled = true;
    if (!append) { rows = []; anchor = null; libraryAnchor = null; list.replaceChildren(el('p', 'empty-message', 'Loading reviews...')); $('#more-feed').hidden = true; }
    try {
      const params = new URLSearchParams({ limit: '20', offset: String(rows.length), q: state.query });
      if (anchor !== null) params.set('before', String(anchor));
      if (personalized && libraryAnchor !== null) params.set('library_before', String(libraryAnchor));
      const data = await api(`${personalized ? '/me/feed' : '/feed'}?${params}`);
      if (request !== requestId || state.view !== 'feed') return;
      anchor = data.before; libraryAnchor = data.library_before ?? null; total = data.total; rows = rows.concat(data.items);
      if (!append) list.replaceChildren();
      for (const item of data.items) {
        // Two columns: the cover, then everything about it. The heading, review
        // and comment count all live in the body so the grid never has to guess.
        const card = el('article', 'feed-card');
        const artwork = button('', 'feed-cover', () => gameDialog.open(item.game));
        artwork.setAttribute('aria-label', `Open ${item.game.name}`); artwork.append(cover(item.game));
        const body = el('div', 'feed-body'); const heading = el('header', 'feed-game');
        heading.append(button(item.game.name, 'feed-title', () => gameDialog.open(item.game)), el('p', 'feed-reason', item.reason));
        const count = el('p', 'feed-count', `${item.comment_count} ${item.comment_count === 1 ? 'comment' : 'comments'}`);
        card.addEventListener('comment-published', () => {
          item.comment_count += 1;
          count.textContent = `${item.comment_count} ${item.comment_count === 1 ? 'comment' : 'comments'}`;
        });
        body.append(heading, gameDialog.reviewCard(item.review), count); card.append(artwork, body);
        list.append(card);
      }
      if (!rows.length) {
        const empty = el('div', 'empty-message feed-empty');
        empty.append(el('strong', '', state.query ? 'No reviews match that game' : 'The conversation starts here'),
          el('p', '', personalized ? 'There are no matching reviews from other players yet. Latest reviews also includes your own posts.' : 'Open a game and publish a review. It will appear here for others to read and discuss.'));
        if (personalized) empty.append(button('See latest reviews', 'button secondary', () => { mode = 'latest'; void load(); }));
        empty.append(button('Find a game to review', 'button primary', browse));
        if (!state.user) empty.append(steamLink('Sign in to review'));
        list.append(empty);
      }
      $('#more-feed').hidden = rows.length >= total || !data.items.length;
    } catch (error) {
      if (request !== requestId || aborted(error)) return;
      if (!append) list.replaceChildren(el('p', 'empty-message', 'The feed could not load. Use Refresh to try again.'));
      report(error);
    } finally { if (request === requestId) { $('#more-feed').disabled = false; $('#refresh-feed').disabled = false; } }
  }
  $('#feed-for-you').addEventListener('click', () => { mode = 'for_you'; void load(); });
  $('#feed-latest').addEventListener('click', () => { mode = 'latest'; void load(); });
  $('#more-feed').addEventListener('click', () => void load(true));
  $('#refresh-feed').addEventListener('click', () => void load());
  return { load, clear() { requestId += 1; rows = []; anchor = null; libraryAnchor = null; total = 0; list.replaceChildren(); }, invalidate() { requestId += 1; } };
}
