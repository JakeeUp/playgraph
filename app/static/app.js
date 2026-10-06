import { genresFor, hours, integer, achievementPercent, selectLibrary, summarize, genreBreakdown, steamArt } from './library.js';
import { $, el, button, steamLink, cover, aborted, numbered, setNumbers, timeLabel } from './dom.js';
import { starDisplay } from './rating.js';
import { createGameDialog } from './game-detail.js';
import { createFeed } from './feed.js';
import { createNews } from './news.js';
import { barList } from './chart.js';

// Page copy per view. pageKey() folds the signed-out library and playtime
// views into the welcome copy, which is what they have always shown. `tab`
// names the page in the browser tab.
const PAGE_COPY = {
  welcome: { title: 'Games on PlayGraph', tab: 'Games',
    subtitle: 'Everything imported so far. Open a game to read its reviews.',
    search: 'Search games', searchLabel: 'Search games' },
  library: { title: 'Your library', tab: 'Your library',
    subtitle: 'Your Steam games, with the hours and achievements from your last sync.',
    search: 'Search your library', searchLabel: 'Search your library' },
  stats: { title: 'Your playtime', tab: 'Playtime',
    subtitle: 'Where your hours went, by genre and by game.',
    search: 'Search your library', searchLabel: 'Search your library' },
  explore: { title: 'Explore', tab: 'Explore',
    subtitle: 'Every game imported into PlayGraph so far. Open one to read its reviews.',
    search: 'Search games', searchLabel: 'Search games' },
  software: { title: 'Software', tab: 'Software',
    subtitle: 'Apps on Steam, like Wallpaper Engine or Blender. Their hours stay out of your game stats.',
    search: 'Search software', searchLabel: 'Search software' },
  news: { title: 'For you', tab: 'For you',
    subtitle: 'What’s happening in games right now, from Steam and the big gaming sites.',
    search: 'Search headlines', searchLabel: 'Search headlines' },
  feed: { title: 'Reviews', tab: 'Reviews',
    subtitle: 'Reviews from other players on games you own or genres you play.',
    search: 'Search reviews by game', searchLabel: 'Search reviews by game' },
  feedGuest: { title: 'Reviews', tab: 'Reviews',
    subtitle: 'The latest reviews on PlayGraph. Sign in and connect Steam to get ones picked for your library.',
    search: 'Search reviews by game', searchLabel: 'Search reviews by game' },
};
// The shelf holds either games or software, and every label follows suit.
const SHELF = {
  game: { own: 'Your games', catalog: 'Games in the catalog', all: 'All games', used: 'Played',
    unused: 'Yet to play', mostUsed: 'Most played', plural: 'games', verb: 'played', more: 'Show more games',
    note: 'Games in the PlayGraph catalog. This is the imported collection, not the full Steam store.' },
  software: { own: 'Your software', catalog: 'Software in the catalog', all: 'All apps', used: 'Used',
    unused: 'Not used', mostUsed: 'Most used', plural: 'apps', verb: 'used', more: 'Show more apps',
    note: 'Software usage and achievements do not count toward your game stats or For You preferences.' },
};
// Steam app ids for the drifting cover wall behind the signed-out welcome.
// Well-known games with portrait art, so the first screen looks like games
// people recognise. Any cover that fails to load just drops out of the row.
const WALL_APPS = [1145360, 1245620, 1086940, 1091500, 292030, 413150, 367520, 1174180, 730,
  814380, 1593500, 2050650, 105600, 620, 553850, 1868140, 2358720, 570,
  271590, 1817070, 489830, 374320, 1888930, 588650, 504230, 646570, 268910];
const state = { user: null, csrf: '', library: [], genres: [], catalog: [], total: 0,
  view: 'library', query: '', filter: 'all', genre: '', sort: 'playtime', shown: 48,
  controller: new AbortController(), generation: 0, catalogRequest: 0,
  pollTimer: null, expiryTimer: null, searchTimer: null, syncing: false };
const privateView = () => Boolean(state.user) && ['library', 'software', 'stats'].includes(state.view);
const gameLibrary = () => state.library.filter((entry) => entry.game.content_kind !== 'software');
const shelfLibrary = () => state.view === 'software'
  ? state.library.filter((entry) => entry.game.content_kind === 'software') : gameLibrary();
const shelfCopy = () => SHELF[state.view === 'software' ? 'software' : 'game'];
const pageKey = () => state.view === 'feed' && !state.user ? 'feedGuest'
  : ['feed', 'news', 'software', 'explore'].includes(state.view) ? state.view
  : state.user ? (state.view === 'stats' ? 'stats' : 'library') : 'welcome';
const gameDialog = createGameDialog(state, api, report);
const news = createNews(state, api);
const feed = createFeed(state, api, gameDialog, () => navigate(state.user ? 'library' : 'explore'), report);
document.addEventListener('review-changed', () => { if (state.view === 'feed') void feed.load(); else feed.invalidate(); });
const sessionChannel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('playgraph-session') : null;
let sessionReady = false;
let checkingSession = false;
function notify(message, error = false) {
  $('#notice').textContent = message; $('#notice').classList.toggle('error', error); $('#notice').hidden = !message;
}
function report(error) { if (!aborted(error)) notify(error.message || 'Something went wrong. Please try again.', true); }
async function api(path, { anonymous = false, ...options } = {}) {
  const generation = state.generation;
  const headers = new Headers(options.headers);
  if (options.body) headers.set('Content-Type', 'application/json');
  if (options.method && options.method !== 'GET') headers.set('X-CSRF-Token', state.csrf);
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin', cache: 'no-store', signal: state.controller.signal });
  if (generation !== state.generation) throw new DOMException('Session changed', 'AbortError');
  if (response.status === 401 && anonymous) return null;
  if (response.status === 401 && state.user) {
    clearSession(); notify('Your session ended. Sign in again to continue.', true);
    void loadCatalog(); throw new DOMException('Session ended', 'AbortError');
  }
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (generation !== state.generation) throw new DOMException('Session changed', 'AbortError');
  if (!response.ok) {
    const detail = typeof body?.detail === 'string' ? body.detail : null;
    const error = new Error(response.status === 429 ? `Too many requests. Try again in ${response.headers.get('Retry-After') || '60'} seconds.`
      : detail || (response.status === 503 ? 'PlayGraph cannot reach its security service. Check that Redis is available, then refresh.' : 'This request could not be completed. Please try again.'));
    error.status = response.status; throw error;
  }
  return body;
}
function stopPolling() {
  clearTimeout(state.pollTimer); state.pollTimer = null; state.syncing = false;
  $('#sync-button').disabled = false; $('#sync-button').textContent = 'Sync Steam';
}
function clearSession() {
  stopPolling(); clearTimeout(state.expiryTimer); clearTimeout(state.searchTimer);
  state.controller.abort(); state.controller = new AbortController(); state.generation += 1; state.catalogRequest += 1;
  Object.assign(state, { user: null, csrf: '', library: [], genres: [], catalog: [], total: 0,
    query: '', filter: 'all', genre: '', view: 'library', shown: 48 });
  $('#search').value = ''; $('#genre').replaceChildren(new Option('All genres', ''));
  gameDialog.clear(); feed.clear(); $('#summary').replaceChildren(); $('#insights').replaceChildren();
  renderShell(); renderCollection();
}
function renderShell() {
  const own = privateView();
  const software = state.view === 'software';
  const isFeed = state.view === 'feed';
  const isNews = state.view === 'news';
  document.querySelectorAll('[data-view]').forEach((item) => {
    const active = item.dataset.view === state.view; item.classList.toggle('active', active);
    if (active) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
  });
  const account = $('#account'); account.replaceChildren();
  if (state.user) {
    const manage = el('a', 'account-manage');
    manage.href = '/account'; manage.title = 'Manage account and sign-in';
    manage.setAttribute('aria-label', `Manage account for ${state.user.display_name}`);
    manage.append(el('span', 'avatar', state.user.display_name.slice(0, 1).toLocaleUpperCase()),
      el('span', 'account-name', state.user.display_name));
    account.append(manage, button('Sign out', 'signout', signOut));
  }
  else account.append(steamLink('Sign in'));
  $('#nav-count').textContent = state.user ? integer(gameLibrary().length) : '';
  renderBacklog();
  $('#welcome').hidden = Boolean(state.user) || software || isFeed || isNews;
  $('#summary').hidden = !own || !shelfLibrary().length;
  $('#insights').hidden = !own || software || !gameLibrary().length;
  $('#collection').hidden = isFeed || isNews || (state.view === 'stats' && Boolean(state.user) && gameLibrary().length > 0);
  $('#feed').hidden = !isFeed; $('#news').hidden = !isNews;
  $('#sync-button').hidden = !state.user || !state.hasSteam; $('#filters').hidden = !own;
  $('#sort').disabled = !own; $('#sort').value = own ? state.sort : 'name';
  const copy = PAGE_COPY[pageKey()]; const shelf = shelfCopy();
  // The welcome hero has its own headline, so the plain page heading steps aside.
  $('.page-heading').hidden = pageKey() === 'welcome';
  if ($('#welcome').hidden === false) { buildWall(); void loadFrontReviews(); }
  if (!own) setHero(null);
  $('#page-title').textContent = copy.title;
  $('#page-subtitle').textContent = copy.subtitle;
  // An open game page owns the tab title until it closes.
  if (!$('#game-dialog').open) document.title = `${copy.tab} | PlayGraph`;
  $('#collection-title').textContent = own ? shelf.own : shelf.catalog;
  $('#collection-note').hidden = own && !software;
  $('#collection-note').textContent = shelf.note;
  $('#search').placeholder = copy.search;
  $('#search').setAttribute('aria-label', copy.searchLabel);
  $('#load-more').textContent = shelf.more;
  document.querySelector('[data-filter="all"]').textContent = shelf.all;
  document.querySelector('[data-filter="played"]').textContent = shelf.used;
  document.querySelector('[data-filter="unplayed"]').textContent = shelf.unused;
  $('#sort').querySelector('[value="playtime"]').textContent = shelf.mostUsed;
  updateGenres();
  if (own) renderInsights();
}
function buildWall() {
  const wall = $('.welcome-wall'); if (wall.childElementCount) return;
  const perRow = Math.ceil(WALL_APPS.length / 3);
  for (let row = 0; row < 3; row += 1) {
    const strip = el('div', 'wall-row');
    for (const appid of WALL_APPS.slice(row * perRow, (row + 1) * perRow)) {
      const img = el('img'); img.alt = ''; img.width = 600; img.height = 900;
      img.decoding = 'async'; img.referrerPolicy = 'no-referrer';
      img.src = steamArt(appid, 'library_600x900.jpg');
      img.addEventListener('error', () => img.remove(), { once: true });
      strip.append(img);
    }
    wall.append(strip);
  }
}
// The three newest public reviews, on the signed-out front door. They show
// what the site is for better than a list of features would. Loaded once;
// with no reviews yet the section just stays hidden.
let frontReviewsLoaded = false;
async function loadFrontReviews() {
  if (frontReviewsLoaded) return; frontReviewsLoaded = true;
  try {
    const data = await api('/feed?limit=3&offset=0&q=', { anonymous: true });
    const items = data?.items ?? []; if (!items.length) return;
    $('#front-reviews-list').replaceChildren(...items.map(({ review, game }) => {
      const row = el('li', 'front-review');
      const art = button('', '', () => gameDialog.open(game)); art.setAttribute('aria-label', `Open ${game.name}`); art.append(cover(game));
      const body = el('div'); const head = el('div', 'front-review-head');
      head.append(button(game.name, 'front-review-title', () => gameDialog.open(game)), starDisplay(review.rating),
        el('span', 'sr-only', `Rated ${review.rating} out of 5.`));
      if (review.verified_playtime_minutes != null) head.append(el('span', 'hours-block', `${hours(review.verified_playtime_minutes)} h played`));
      else head.append(el('span', 'helper', 'No Steam hours'));
      const by = el('p', 'front-review-by', `${review.author_name} · `); by.append(el('span', 'num', timeLabel(review.created_at)));
      body.append(head, el('p', 'front-review-body', review.body), by); row.append(art, body); return row;
    }));
    $('#front-reviews').hidden = false;
  } catch { frontReviewsLoaded = false; }  // Optional extra: the catalog below still works without it.
}
// The library page heading sits on the wide hero art of your most played
// game. The URL comes from the numeric app id, like every other cover.
function setHero(game) {
  const heading = $('.page-heading'); const appid = game?.steam_appid;
  if (heading.dataset.hero === String(appid ?? '')) return;
  heading.dataset.hero = String(appid ?? '');
  heading.querySelector('.hero-art')?.remove(); heading.classList.toggle('has-hero', Boolean(appid));
  if (!appid) return;
  const art = el('img', 'hero-art'); art.alt = ''; art.decoding = 'async'; art.referrerPolicy = 'no-referrer';
  // Not every game has hero art. The store header, blurred, still gives the color.
  art.addEventListener('error', () => {
    if (art.classList.contains('soft')) { art.remove(); return; }
    art.classList.add('soft'); art.src = steamArt(appid, 'header.jpg');
  });
  art.src = steamArt(appid, 'library_hero.jpg'); heading.prepend(art);
}
// The three counts in the header. Played and yet to play come from Steam
// playtime; finished is not tracked, so it is not shown.
function renderBacklog() {
  const backlog = $('#backlog'); const games = gameLibrary();
  backlog.hidden = !state.user || !games.length; if (backlog.hidden) { backlog.replaceChildren(); return; }
  const summary = summarize(games);
  backlog.replaceChildren(...[['Played', integer(summary.played), 'played'], ['Yet to play', integer(summary.games - summary.played), ''],
    ['Hours', hours(summary.minutes), 'hours']].map(([label, value, tone]) => {
    const item = el('div'); item.append(el('dt', '', label), el('dd', `num ${tone}`.trim(), value)); return item;
  }));
}
function updateGenres() {
  const names = [...new Set(shelfLibrary().flatMap((entry) => genresFor(entry.game)))].sort();
  $('#genre').replaceChildren(new Option('All genres', ''), ...names.map((name) => new Option(name, name)));
  if (!names.includes(state.genre)) state.genre = '';
  $('#genre').value = state.genre;
}
function stat(label, value, unit, note) {
  const node = el('div', 'stat'); const number = el('span', 'stat-value', value);
  if (unit) number.append(el('span', 'stat-unit', unit));
  node.append(el('span', 'stat-label', label), number, numbered('span', 'stat-note', note)); return node;
}
function renderInsights() {
  const summary = summarize(shelfLibrary());
  if (state.view === 'software') {
    $('#summary').replaceChildren(stat('Apps in your library', integer(summary.games), '', 'Synced from Steam'),
      stat('Time in apps', hours(summary.minutes), 'hrs', 'Not counted as playtime'),
      stat('Apps used', integer(summary.played), '', `${integer(summary.games - summary.played)} not used`),
      stat('App achievements', integer(summary.unlocked), '', 'Not counted with games'));
    return;
  }
  $('#summary').replaceChildren(stat('Games in your library', integer(summary.games), '', 'Synced from Steam'),
    stat('Total playtime', hours(summary.minutes), 'hrs', 'All time, from Steam'),
    stat('Games played', integer(summary.played), '', `${integer(summary.games - summary.played)} not played yet`),
    stat('Achievements unlocked', integer(summary.unlocked), '', `Across ${integer(summary.achievementGames)} games with achievements`));
  const insights = $('#insights'); insights.classList.toggle('expanded', state.view === 'stats');
  const top = selectLibrary(gameLibrary()).find((entry) => entry.playtime_minutes > 0);
  setHero(state.view === 'software' ? null : top?.game);
  insights.replaceChildren(...(top ? [spotlight(top)] : []), genreCard(),
    ...(state.view === 'stats' ? [mostPlayedCard()] : []));
}
function spotlight(entry) {
  const card = el('article', 'spotlight');
  const open = button('', 'spotlight-cover', () => gameDialog.open(entry.game));
  open.setAttribute('aria-label', `Open ${entry.game.name}`); open.append(cover(entry.game));
  const copy = el('div', 'spotlight-copy');
  copy.append(el('h3', '', entry.game.name), numbered('p', 'spotlight-hours', `${hours(entry.playtime_minutes)} h played`));
  if (achievementPercent(entry) != null) {
    copy.append(numbered('p', 'spotlight-meta', `${integer(entry.achievements_unlocked)} of ${integer(entry.achievements_total)} achievements unlocked`));
  }
  copy.append(button('Open game page', 'text-button', () => gameDialog.open(entry.game)));
  const body = el('div', 'spotlight-body'); body.append(open, copy);
  card.append(el('h2', '', 'Most played'), body); return card;
}
function genreCard() {
  const full = state.view === 'stats';
  const card = el('article', 'insight-card genre-card'); const heading = el('div', 'section-heading');
  heading.append(el('h2', '', 'Top genres'));
  if (!full) heading.append(button('See all playtime', 'text-button', () => navigate('stats')));
  card.append(heading);
  const genres = full ? state.genres : state.genres.slice(0, 5);
  if (!genres.length) card.append(el('p', 'helper', 'Genres show up after your first sync.'));
  else card.append(barList(genres.map((genre) => {
    const games = `${integer(genre.game_count)} ${genre.game_count === 1 ? 'game' : 'games'}`;
    return { label: genre.genre, value: genre.total_minutes, text: `${hours(genre.total_minutes)} h`, detail: games,
      name: `${genre.genre}: ${hours(genre.total_minutes)} hours across ${games}. Show these games.`,
      onSelect: () => showGenre(genre.genre) };
  })));
  card.append(el('p', 'card-footnote', 'Select a genre to see its games. A game can carry several genres and its hours count toward each, so these totals overlap.'));
  return card;
}
function mostPlayedCard() {
  const card = el('article', 'insight-card most-played'); card.append(el('h2', '', 'Top 10 by playtime'));
  const top = selectLibrary(gameLibrary()).filter((entry) => entry.playtime_minutes > 0).slice(0, 10);
  card.append(barList(top.map((entry, index) => {
    const lead = el('span', 'bar-lead'); lead.append(el('span', 'bar-rank', String(index + 1)), cover(entry.game));
    const percent = achievementPercent(entry);
    return { label: entry.game.name, value: entry.playtime_minutes, text: `${hours(entry.playtime_minutes)} h`, lead,
      detail: percent == null ? 'No achievement data' : `${Math.round(percent)}% of achievements`,
      name: `Open ${entry.game.name}, ${hours(entry.playtime_minutes)} hours played`,
      onSelect: () => gameDialog.open(entry.game) };
  })));
  return card;
}
function showGenre(genre) {
  navigate('library');
  state.genre = genre; $('#genre').value = genre; renderCollection();
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  $('#collection').scrollIntoView({ behavior: still ? 'auto' : 'smooth', block: 'start' });
}
function renderCollection() {
  const own = privateView(); const filtered = own ? selectLibrary(shelfLibrary(), state) : state.catalog.map((game) => ({ game }));
  const total = own ? filtered.length : state.total; const visible = own ? filtered.slice(0, state.shown) : filtered;
  const grid = $('#games'); grid.replaceChildren(); const shelf = shelfCopy();
  for (const entry of visible) {
    const game = entry.game; const card = el('article', 'game-card'); const open = button('', '', () => gameDialog.open(game));
    open.setAttribute('aria-label', `Open ${game.name}${own ? `, ${hours(entry.playtime_minutes)} hours ${shelf.verb}` : ''}`);
    const art = cover(game); const pct = achievementPercent(entry);
    if (own && pct === 100) art.append(numbered('span', 'cover-badge', '100%'));
    else if (own && entry.playtime_minutes === 0) art.append(el('span', 'cover-badge', shelf.unused));
    const meta = el('span', 'game-meta'); meta.append(el('span', 'genre-text', genresFor(game)[0] || 'Steam'));
    if (own) meta.append(el('span', entry.playtime_minutes > 0 ? 'num played' : 'num', `${hours(entry.playtime_minutes)} h`));
    open.append(art, el('h3', 'game-name', game.name), meta); card.append(open); grid.append(card);
  }
  if (!visible.length) {
    const filtering = state.query || state.genre || state.filter !== 'all'; const empty = el('div', 'empty-message');
    empty.append(el('strong', '', filtering ? 'No matches' : own ? 'No games yet' : 'No games in the catalog yet'));
    empty.append(el('span', '', filtering ? 'Try another search or clear your filters.'
      : own ? (state.hasSteam ? 'Sync Steam to bring in your games. Your Steam game details need to be public.' : 'Connect Steam to bring in your library and put verified hours on your reviews.') : 'Games show up here once someone syncs a Steam library.'));
    if (filtering) empty.append(button('Clear filters', 'button secondary', resetFilters));
    else if (state.user && state.hasSteam) empty.append(button('Sync Steam', 'button primary', syncLibrary));
    else if (state.user) { const connect = el('a', 'button primary', 'Connect Steam'); connect.href = '/account'; empty.append(connect); }
    else empty.append(steamLink('Create an account'));
    grid.append(empty);
  }
  $('#result-count').textContent = integer(total); $('#load-more').hidden = visible.length >= total;
  setNumbers($('#shown-count'), total ? `${integer(visible.length)} of ${integer(total)} ${shelf.plural}` : '');
  document.querySelectorAll('[data-filter]').forEach((chip) => { chip.classList.toggle('selected', chip.dataset.filter === state.filter); chip.setAttribute('aria-pressed', String(chip.dataset.filter === state.filter)); });
}
async function loadCatalog(append = false) {
  const request = ++state.catalogRequest; const generation = state.generation; const offset = append ? state.catalog.length : 0;
  $('#load-more').disabled = true;
  if (!append) { state.catalog = []; state.total = 0; $('#games').replaceChildren(el('p', 'empty-message', 'Loading games…')); }
  try {
    const data = await api(`/games?kind=${state.view === 'software' ? 'software' : 'game'}&limit=48&offset=${offset}&q=${encodeURIComponent(state.query)}`);
    if (request !== state.catalogRequest || generation !== state.generation || privateView()) return;
    state.catalog = append ? state.catalog.concat(data.games) : data.games; state.total = data.total; renderCollection();
  } catch (error) {
    if (request !== state.catalogRequest || aborted(error)) return;
    if (!append) $('#games').replaceChildren(el('p', 'empty-message', 'Couldn’t load the catalog. Refresh to try again.')); report(error);
  } finally { if (request === state.catalogRequest) $('#load-more').disabled = false; }
}
async function loadLibrary() {
  const generation = state.generation;
  const library = await api('/me/library');
  if (!state.user || generation !== state.generation) return;
  state.library = library; state.genres = genreBreakdown(library);
  renderShell(); if (privateView()) renderCollection();
}
function navigate(view) {
  feed.invalidate();
  Object.assign(state, { view, query: '', genre: '', filter: 'all', shown: 48 }); state.catalogRequest += 1;
  $('#search').value = ''; $('#genre').value = ''; clearTimeout(state.searchTimer); renderShell();
  if (view === 'feed') void feed.load(); else if (view === 'news') void news.load();
  else if (privateView()) renderCollection(); else void loadCatalog();
}
function resetFilters() {
  Object.assign(state, { query: '', genre: '', filter: 'all', shown: 48 }); $('#search').value = ''; $('#genre').value = '';
  if (privateView()) renderCollection(); else void loadCatalog();
}
async function signOut() {
  const control = $('.signout'); if (control) control.disabled = true;
  try { const result = await api('/auth/logout', { method: 'POST' }); clearSession(); sessionChannel?.postMessage('session-changed'); notify(result?.provider_signed_out === false ? 'Signed out of PlayGraph. Clerk could not be reached; open Sign in to finish provider sign-out.' : 'You are signed out. Your library is private to your account.'); await loadCatalog(); }
  catch (error) { report(error); if (control?.isConnected) control.disabled = false; }
}
async function syncLibrary() {
  if (!state.user || !state.hasSteam || state.syncing) return;
  state.syncing = true; $('#sync-button').disabled = true; $('#sync-button').textContent = 'Syncing…';
  try {
    const job = await api('/me/sync', { method: 'POST' }); notify('Steam sync queued. You can keep browsing while your games update.'); void pollSync(job.job_id);
  } catch (error) {
    if (error.status === 409 && state.user) { notify('Checking your existing Steam sync...'); void pollSync(`sync-json-user-${state.user.id}`); }
    else { stopPolling(); report(error); }
  }
}
async function pollSync(jobId, restore = false) {
  const generation = state.generation;
  try {
    const job = await api(`/me/sync/status/${encodeURIComponent(jobId)}`); if (!state.user || generation !== state.generation) return;
    if (['queued', 'in_progress', 'deferred'].includes(job.status)) {
      state.syncing = true; $('#sync-button').disabled = true; $('#sync-button').textContent = 'Syncing…';
      notify(job.status === 'in_progress' ? 'Syncing with Steam. Large libraries can take several minutes; achievement checks run one game at a time.' : 'Steam sync is queued. Keep the worker running and we will update your library when it finishes.');
      state.pollTimer = setTimeout(() => void pollSync(jobId), 5000);
    } else {
      stopPolling(); if (restore) return;
      if (job.status === 'complete') { await loadLibrary(); if (state.user && generation === state.generation) notify(`Your library is up to date. ${integer(job.result?.games_synced ?? state.library.length)} games synced.`); }
      else notify(job.status === 'failed' ? 'The sync failed. Check the worker and Steam availability, then try again.' : 'This sync is no longer in the queue. Start a new sync when you are ready.', true);
    }
  } catch (error) { if (generation === state.generation) { stopPolling(); if (!restore) report(error); } }
}
document.querySelectorAll('[data-view]').forEach((node) => node.addEventListener('click', () => navigate(node.dataset.view)));
document.querySelectorAll('[data-filter]').forEach((node) => node.addEventListener('click', () => { state.filter = node.dataset.filter; state.shown = 48; renderCollection(); }));
document.querySelectorAll('.platform-switch .in-dev').forEach((node) => node.addEventListener('click', () =>
  notify(`${node.firstChild.textContent.trim()} libraries are in development. Only Steam games can be imported for now.`)));
$('#genre').addEventListener('change', (event) => { state.genre = event.target.value; state.shown = 48; renderCollection(); });
$('#sort').addEventListener('change', (event) => { state.sort = event.target.value; renderCollection(); });
$('#search').addEventListener('input', (event) => {
  state.query = event.target.value; state.shown = 48; clearTimeout(state.searchTimer); state.catalogRequest += 1;
  if (state.user && state.view === 'stats') { state.view = 'library'; renderShell(); }
  if (state.view === 'news') news.render();
  else if (state.view === 'feed') { feed.invalidate(); state.searchTimer = setTimeout(() => void feed.load(), 280); }
  else if (privateView()) renderCollection(); else state.searchTimer = setTimeout(() => void loadCatalog(), 280);
});
for (const mode of ['grid', 'list']) $('#'+mode+'-view').addEventListener('click', () => {
  $('#games').classList.toggle('game-list', mode === 'list'); $('#grid-view').setAttribute('aria-pressed', String(mode === 'grid')); $('#list-view').setAttribute('aria-pressed', String(mode === 'list'));
});
$('#load-more').addEventListener('click', () => { if (privateView()) { state.shown += 48; renderCollection(); } else void loadCatalog(true); });
$('#sync-button').addEventListener('click', syncLibrary);
document.addEventListener('keydown', (event) => {
  if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !$('#game-dialog').open
    && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) { event.preventDefault(); $('#search').focus(); }
});
window.addEventListener('pagehide', () => { stopPolling(); state.controller.abort(); });
window.addEventListener('pageshow', (event) => { if (event.persisted) location.reload(); });
function acceptSession(session) {
  state.user = session.user; state.csrf = session.csrf_token;
  state.hasSteam = session.has_steam; state.authProvider = session.auth_provider;
  clearTimeout(state.expiryTimer);
  state.expiryTimer = setTimeout(() => { clearSession(); notify('Your session ended. Sign in to continue.', true); void loadCatalog(); }, Math.max(0, session.expires_at * 1000 - Date.now()));
}
async function checkSession() {
  if (!sessionReady || checkingSession || document.hidden) return;
  checkingSession = true;
  try {
    const session = await api('/auth/session', { anonymous: true });
    if (!session && state.user) { clearSession(); notify('You are signed out.'); await loadCatalog(); }
    else if (session && (session.user.id !== state.user?.id || session.csrf_token !== state.csrf)) {
      clearSession(); acceptSession(session); renderShell(); await loadLibrary();
      if (state.user && state.hasSteam) void pollSync(`sync-json-user-${state.user.id}`, true);
    }
  } catch (error) { report(error); } finally { checkingSession = false; }
}
sessionChannel?.addEventListener('message', (event) => { if (event.data === 'session-changed') void checkSession(); });
window.addEventListener('focus', () => void checkSession());
document.addEventListener('visibilitychange', () => { if (!document.hidden) void checkSession(); });
async function start() {
  if (new URLSearchParams(location.search).has('login_error')) {
    notify('Steam sign-in could not be completed. Start again using Connect Steam in this browser. If it repeats, check that the address matches APP_BASE_URL.', true); history.replaceState(null, '', '/app');
  }
  const steamConnected = new URLSearchParams(location.search).get('steam') === 'connected';
  if (steamConnected) history.replaceState(null, '', '/app');
  try {
    const session = await api('/auth/session', { anonymous: true });
    if (session) {
      acceptSession(session); sessionChannel?.postMessage('session-changed');
      renderShell(); await loadLibrary();
      // Straight back from connecting Steam: start the first sync for them.
      if (steamConnected && state.hasSteam) { notify('Steam connected. Bringing in your library now.'); void syncLibrary(); }
      else if (state.user && state.hasSteam) void pollSync(`sync-json-user-${state.user.id}`, true);
    } else { renderShell(); await loadCatalog(); }
  } catch (error) { report(error); if (!state.user) { renderShell(); await loadCatalog(); } }
}
await start();
sessionReady = true;
let linkRequest = 0;
async function openLinkedReview() {
  const request = ++linkRequest;
  const match = /^#(review|game)=([1-9][0-9]{0,9})$/.exec(location.hash);
  if (!match) return;
  try {
    if (match[1] === 'review') await gameDialog.openThread(Number(match[2]));
    else {
      const game = await api(`/games/${match[2]}`);
      if (request === linkRequest) await gameDialog.open(game);
    }
  } catch (error) { if (request === linkRequest) report(error); }
}
window.addEventListener('hashchange', () => void openLinkedReview());
await openLinkedReview();
