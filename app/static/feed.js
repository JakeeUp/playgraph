import { $, el, button, steamLink, aborted, skeletonFeed, emptyState, loadingState } from './dom.js';
import { authorLink, reportControl } from './profile.js';
import { postCard, loadLikes, loadReposts, loadBookmarks, clampBodies } from './post.js';
import { trendGameRow, suggestionRow } from './explore.js';
import { ageLabel } from './news.js';

export function createFeed(state, api, gameDialog, browse, report) {
  let requestId = 0;
  let mode = 'for_you';
  let rows = [];
  let anchor = null;
  let repostAnchor = null;
  let libraryAnchor = null;
  let total = 0;
  const list = $('#feed-items');
  let railState = 'idle';
  // The rail is the right column. It is not the feed, so search and paging
  // leave it alone. Refresh asks for it again.
  async function loadRail(force = false) {
    const rail = $('#feed-rail');
    if (!rail || railState === 'loading' || (railState === 'ready' && !force)) return;
    railState = 'loading';
    try {
      const [headlines, trends, suggested] = await Promise.all([
        api('/news?offset=0').catch(() => null),
        api('/explore').catch(() => null),
        api('/explore/suggestions').catch(() => null),
      ]);
      if (state.view !== 'feed' || !rail.isConnected) { railState = 'idle'; return; }
      const blocks = [];
      const stories = (headlines?.items || []).slice(0, 4);
      if (stories.length) blocks.push(railBlock('What’s happening', 'All headlines', 'news', stories.map(newsRow)));
      const games = (trends?.games || []).slice(0, 5);
      if (games.length) blocks.push(railBlock('Trending', 'See Explore', 'explore', games.map((entry) => trendGameRow(entry, (game) => gameDialog.open(game)))));
      const people = (suggested?.items || []).slice(0, 4);
      if (people.length) blocks.push(railBlock('Who to follow', 'See Explore', 'explore', people.map((person) => suggestionRow(person, { state, api, report }))));
      rail.replaceChildren(...(blocks.length ? blocks : [el('p', 'helper', 'Nothing else to show right now.')]));
      railState = blocks.length ? 'ready' : 'idle';
    } catch (error) {
      if (aborted(error)) return;
      railState = 'idle';
    }
  }
  function railBlock(title, moreLabel, view, rows) {
    const section = el('section', 'side-block');
    const head = el('div', 'rail-head');
    head.append(el('h2', '', title));
    if (moreLabel) head.append(button(moreLabel, 'text-button', () => document.querySelector(`.topnav [data-view="${view}"]`)?.click()));
    section.append(head, ...rows);
    return section;
  }
  function newsRow(item) {
    const row = el('a', 'rail-news');
    row.href = item.url; row.target = '_blank'; row.rel = 'noopener noreferrer';
    const text = el('span', 'rail-copy');
    text.append(el('span', 'rail-title', item.title));
    const meta = [item.source, ageLabel(item.published_at)].filter(Boolean).join(' · ');
    if (meta) text.append(el('span', 'rail-meta', meta));
    row.append(text); return row;
  }
  async function load(append = false) {
    const request = ++requestId;
    // Signed out, only the latest reviews exist.
    const active = state.user ? mode : 'latest';
    const personalized = active === 'for_you';
    $('#feed-for-you').disabled = !state.user;
    $('#feed-following').hidden = !state.user;
    for (const [id, name] of [['#feed-for-you', 'for_you'], ['#feed-following', 'following'], ['#feed-latest', 'latest']]) {
      $(id).classList.toggle('selected', active === name); $(id).setAttribute('aria-pressed', String(active === name));
    }
    $('#feed-description').textContent = personalized
      ? 'Matched to your library and the genres you play, from the latest 500 reviews. Software reviews aren’t included.'
      : active === 'following' ? 'Reviews, reposts and quotes from players you follow, newest first.'
      : 'The latest public game reviews and conversations. Software reviews stay on the Software shelf.';
    $('#more-feed').disabled = true; $('#refresh-feed').disabled = true;
    if (!append) { rows = []; anchor = null; repostAnchor = null; libraryAnchor = null; list.setAttribute('aria-busy', 'true'); list.replaceChildren(loadingState('Loading reviews…'), ...skeletonFeed()); $('#more-feed').hidden = true; void loadRail(); }
    try {
      const params = new URLSearchParams({ limit: '20', offset: String(rows.length), q: state.query });
      if (anchor !== null) params.set('before', String(anchor));
      if (active === 'following' && repostAnchor !== null) params.set('repost_before', String(repostAnchor));
      if (personalized && libraryAnchor !== null) params.set('library_before', String(libraryAnchor));
      const data = await api(`${personalized ? '/me/feed' : active === 'following' ? '/me/following-feed' : '/feed'}?${params}`);
      if (request !== requestId || state.view !== 'feed') return;
      anchor = data.before; repostAnchor = data.repost_before ?? null; libraryAnchor = data.library_before ?? null; total = data.total; rows = rows.concat(data.items);
      if (!append) list.replaceChildren();
      list.removeAttribute('aria-busy');
      for (const item of data.items) list.append(feedCard(item));
      clampBodies(list);
      if (state.user && data.items.length) {
        const reviews = data.items.map((item) => item.review);
        void loadLikes(api, state.liked, reviews).catch(report);
        void loadReposts(api, state.reposted, reviews).catch(report);
        void loadBookmarks(api, state.bookmarked, reviews).catch(report);
      }
      if (!rows.length) {
        const actions = [];
        if (active !== 'latest') actions.push(button('See latest reviews', 'button secondary', () => { mode = 'latest'; void load(); }));
        if (active !== 'following') actions.push(button('Find a game to review', 'button primary', browse));
        if (!state.user) actions.push(steamLink('Sign in to review'));
        const following = active === 'following' && !state.query;
        const empty = emptyState({ title: state.query ? 'No reviews match that game' : following ? 'Nothing from people you follow yet' : 'No reviews yet',
          text: following ? 'Open a reviewer’s name in Latest reviews to see their profile and follow them.'
            : personalized ? 'There are no matching reviews from other players yet. Latest reviews also includes your own posts.' : 'Open a game and write one. It’ll show up here for everyone.', actions });
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
  const feedCard = (item) => postCard(item, { gameDialog, api, state, report, authorLink,
    quoteReport: (id) => reportControl(api, 'repost', id) });
  $('#feed-for-you').addEventListener('click', () => { mode = 'for_you'; void load(); });
  $('#feed-latest').addEventListener('click', () => { mode = 'latest'; void load(); });
  $('#feed-following').addEventListener('click', () => { mode = 'following'; void load(); });
  $('#more-feed').addEventListener('click', () => void load(true));
  $('#refresh-feed').addEventListener('click', () => { void loadRail(true); void load(); });
  return { load, clear() { requestId += 1; rows = []; anchor = null; repostAnchor = null; libraryAnchor = null; total = 0; list.replaceChildren(); }, invalidate() { requestId += 1; } };
}
