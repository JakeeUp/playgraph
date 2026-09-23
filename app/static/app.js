import { genresFor, hours, integer, achievementPercent, selectLibrary, summarize, genreBreakdown, steamArt } from './library.js';
import { $, el, button, steamLink, cover, aborted } from './dom.js';
import { createGameDialog } from './game-detail.js';
import { createFeed } from './feed.js';
import { barList } from './chart.js';

// Page copy per view. pageKey() folds the signed-out library and stats views
// into the welcome copy, which is what they have always shown.
const PAGE_COPY = {
  welcome: { eyebrow: 'A LITTLE MORE THAN A BACKLOG', title: 'Your life in games',
    subtitle: 'The ones you love. The ones you finish. The ones you keep coming back to.',
    search: 'Search the catalog...', searchLabel: 'Search games' },
  library: { eyebrow: 'WELCOME BACK, PLAYER', title: 'Your library',
    subtitle: 'Your collection, playtime, and reviews. All in one place.',
    search: 'Search your library...', searchLabel: 'Search games' },
  stats: { eyebrow: 'THE BIGGER PICTURE', title: 'Time well played',
    subtitle: 'A closer look at the games and genres you spend time with.',
    search: 'Search your library...', searchLabel: 'Search games' },
  explore: { eyebrow: 'GOOD GAMES, WAITING TO HAPPEN', title: 'Explore games',
    subtitle: 'Browse games imported into PlayGraph and see what players have to say.',
    search: 'Search the catalog...', searchLabel: 'Search games' },
  software: { eyebrow: 'A SPACE FOR YOUR TOOLS', title: 'Your software',
    subtitle: 'Utilities and creative apps, kept separate from your game collection.',
    search: 'Search software...', searchLabel: 'Search software' },
  feed: { eyebrow: 'GAMES WORTH TALKING ABOUT', title: 'For you',
    subtitle: 'Real reviews, different perspectives, and conversations about your games.',
    search: 'Search reviews by game...', searchLabel: 'Search reviews by game' },
};
// The shelf holds either games or software, and every label follows suit.
const SHELF = {
  game: { own: 'On your shelf', catalog: 'Explore the catalog', all: 'All games', used: 'Played',
    unused: 'Yet to play', mostUsed: 'Most played', plural: 'games', verb: 'played', more: 'Show more games',
    note: 'Games in the PlayGraph catalog. This is the imported collection, not the full Steam store.' },
  software: { own: 'Your applications', catalog: 'Software catalog', all: 'All apps', used: 'Used',
    unused: 'Not used', mostUsed: 'Most used', plural: 'apps', verb: 'used', more: 'Show more apps',
    note: 'Software usage and achievements do not count toward your game stats or For You preferences.' },
};
const state = { user: null, csrf: '', library: [], genres: [], catalog: [], total: 0,
  view: 'library', query: '', filter: 'all', genre: '', sort: 'playtime', shown: 48,
  controller: new AbortController(), generation: 0, catalogRequest: 0,
  pollTimer: null, expiryTimer: null, searchTimer: null, syncing: false };
const privateView = () => Boolean(state.user) && ['library', 'software', 'stats'].includes(state.view);
const gameLibrary = () => state.library.filter((entry) => entry.game.content_kind !== 'software');
const shelfLibrary = () => state.view === 'software'
  ? state.library.filter((entry) => entry.game.content_kind === 'software') : gameLibrary();
const shelfCopy = () => SHELF[state.view === 'software' ? 'software' : 'game'];
const pageKey = () => ['feed', 'software', 'explore'].includes(state.view) ? state.view
  : state.user ? (state.view === 'stats' ? 'stats' : 'library') : 'welcome';
const gameDialog = createGameDialog(state, api, report);
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
  $('#sync-button').disabled = false; $('#sync-button').textContent = '↻ Sync Steam';
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
  else { const link = steamLink('Sign in / Create account'); link.classList.add('compact'); account.append(link); }
  $('#nav-count').textContent = state.user ? integer(gameLibrary().length) : '';
  $('#welcome').hidden = Boolean(state.user) || software || isFeed;
  $('#summary').hidden = !own || !shelfLibrary().length;
  $('#insights').hidden = !own || software || !gameLibrary().length;
  $('#collection').hidden = isFeed || (state.view === 'stats' && Boolean(state.user) && gameLibrary().length > 0);
  $('#feed').hidden = !isFeed;
  $('#sync-button').hidden = !state.user || !state.hasSteam; $('#filters').hidden = !own;
  $('#sort').disabled = !own; $('#sort').value = own ? state.sort : 'name';
  const copy = PAGE_COPY[pageKey()]; const shelf = shelfCopy();
  $('#page-title').replaceChildren(document.createTextNode(copy.title), el('span', 'accent', '.'));
  $('#eyebrow').textContent = copy.eyebrow;
  $('#page-subtitle').textContent = copy.subtitle;
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
function updateGenres() {
  const names = [...new Set(shelfLibrary().flatMap((entry) => genresFor(entry.game)))].sort();
  $('#genre').replaceChildren(new Option('All genres', ''), ...names.map((name) => new Option(name, name)));
  if (!names.includes(state.genre)) state.genre = '';
  $('#genre').value = state.genre;
}
function stat(label, value, unit, note) {
  const node = el('div', 'stat'); const number = el('span', 'stat-value', value);
  if (unit) number.append(el('span', 'stat-unit', unit));
  node.append(el('span', 'stat-label', label), number, el('span', 'stat-note', note)); return node;
}
function renderInsights() {
  const summary = summarize(shelfLibrary());
  if (state.view === 'software') {
    $('#summary').replaceChildren(stat('Software applications', integer(summary.games), '', 'Synced from Steam'),
      stat('Recorded usage', hours(summary.minutes), 'hrs', 'Separate from gaming time'),
      stat('Apps you have used', integer(summary.played), '', `${integer(summary.games - summary.played)} not used`),
      stat('Software achievements', integer(summary.unlocked), '', 'Separate from game achievements'));
    return;
  }
  $('#summary').replaceChildren(stat('Games in your library', integer(summary.games), '', 'Synced from Steam'),
    stat('Total playtime', hours(summary.minutes), 'hrs', 'Lifetime playtime'),
    stat('Games you have played', integer(summary.played), '', `${integer(summary.games - summary.played)} still to discover`),
    stat('Achievements unlocked', integer(summary.unlocked), '', `Available data from ${integer(summary.achievementGames)} games`));
  const insights = $('#insights'); insights.classList.toggle('expanded', state.view === 'stats');
  const top = selectLibrary(gameLibrary()).find((entry) => entry.playtime_minutes > 0);
  insights.replaceChildren(...(top ? [spotlight(top)] : []), genreCard(),
    ...(state.view === 'stats' ? [mostPlayedCard()] : []));
}
function spotlight(entry) {
  const card = el('article', 'spotlight');
  // Store hero art, blurred behind the card. Decorative, so a missing image
  // simply leaves the plain surface.
  const backdrop = el('img', 'spotlight-backdrop');
  backdrop.alt = ''; backdrop.decoding = 'async'; backdrop.referrerPolicy = 'no-referrer';
  backdrop.addEventListener('error', () => backdrop.remove());
  backdrop.src = steamArt(entry.game.steam_appid, 'library_hero.jpg');
  const open = button('', 'spotlight-cover', () => gameDialog.open(entry.game));
  open.setAttribute('aria-label', `Open ${entry.game.name}`); open.append(cover(entry.game));
  const copy = el('div', 'spotlight-copy');
  copy.append(el('p', 'eyebrow', 'THE ONE YOU KEEP COMING BACK TO'), el('h2', '', entry.game.name),
    el('p', 'spotlight-hours', `${hours(entry.playtime_minutes)} hours. And counting.`));
  if (achievementPercent(entry) != null) {
    copy.append(el('p', 'spotlight-meta', `${integer(entry.achievements_unlocked)} of ${integer(entry.achievements_total)} achievements unlocked`));
  }
  copy.append(button('See game & reviews ↗', 'text-button', () => gameDialog.open(entry.game)));
  card.append(backdrop, open, copy); return card;
}
function genreCard() {
  const full = state.view === 'stats';
  const card = el('article', 'insight-card genre-card'); const heading = el('div', 'section-heading');
  heading.append(el('h2', '', 'Your kind of games'));
  if (!full) heading.append(button('All stats ↗', 'text-button', () => navigate('stats')));
  card.append(heading);
  const genres = full ? state.genres : state.genres.slice(0, 5);
  if (!genres.length) card.append(el('p', 'helper', 'Genre data will appear after your library sync.'));
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
  const card = el('article', 'insight-card most-played'); card.append(el('h2', '', 'Your most played'));
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
    if (own && pct === 100) art.append(el('span', 'cover-badge', '✓ 100%'));
    else if (own && entry.playtime_minutes === 0) art.append(el('span', 'cover-badge', shelf.unused));
    const meta = el('span', 'game-meta'); meta.append(el('span', 'genre-text', genresFor(game)[0] || 'Steam'));
    if (own) meta.append(el('span', entry.playtime_minutes > 0 ? 'played' : '', `${hours(entry.playtime_minutes)} h`));
    open.append(art, el('h3', 'game-name', game.name), meta); card.append(open); grid.append(card);
  }
  if (!visible.length) {
    const filtering = state.query || state.genre || state.filter !== 'all'; const empty = el('div', 'empty-message');
    empty.append(el('strong', '', filtering ? 'Nothing on this shelf yet' : own ? 'Your library starts here' : 'The catalog is waiting'));
    empty.append(el('span', '', filtering ? 'Try another search or clear your filters.'
      : own ? (state.hasSteam ? 'Sync Steam to bring in your games. Your game details must be visible to Steam’s API.' : 'Your PlayGraph account is ready. Browse the catalog to rate games and write reviews. Steam linking is coming next.') : 'Sign in to rate games and join the conversation.'));
    if (filtering) empty.append(button('Clear filters', 'button secondary', resetFilters));
    else if (state.user && state.hasSteam) empty.append(button('Sync my library', 'button primary', syncLibrary));
    else if (state.user) empty.append(button('Explore games', 'button primary', () => navigate('explore')));
    else empty.append(steamLink('Create your account'));
    grid.append(empty);
  }
  $('#result-count').textContent = integer(total); $('#load-more').hidden = visible.length >= total;
  $('#shown-count').textContent = total ? `${integer(visible.length)} of ${integer(total)} ${shelf.plural}` : '';
  document.querySelectorAll('[data-filter]').forEach((chip) => { chip.classList.toggle('selected', chip.dataset.filter === state.filter); chip.setAttribute('aria-pressed', String(chip.dataset.filter === state.filter)); });
}
async function loadCatalog(append = false) {
  const request = ++state.catalogRequest; const generation = state.generation; const offset = append ? state.catalog.length : 0;
  $('#load-more').disabled = true;
  if (!append) { state.catalog = []; state.total = 0; $('#games').replaceChildren(el('p', 'empty-message', 'Finding games...')); }
  try {
    const data = await api(`/games?kind=${state.view === 'software' ? 'software' : 'game'}&limit=48&offset=${offset}&q=${encodeURIComponent(state.query)}`);
    if (request !== state.catalogRequest || generation !== state.generation || privateView()) return;
    state.catalog = append ? state.catalog.concat(data.games) : data.games; state.total = data.total; renderCollection();
  } catch (error) {
    if (request !== state.catalogRequest || aborted(error)) return;
    if (!append) $('#games').replaceChildren(el('p', 'empty-message', 'The catalog is unavailable. Refresh to try again.')); report(error);
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
  if (view === 'feed') void feed.load(); else if (privateView()) renderCollection(); else void loadCatalog();
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
  state.syncing = true; $('#sync-button').disabled = true; $('#sync-button').textContent = '↻ Syncing...';
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
      state.syncing = true; $('#sync-button').disabled = true; $('#sync-button').textContent = '↻ Syncing...';
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
document.getElementById('tab-log')?.addEventListener('click', () => { navigate('library'); $('#search').focus(); });
document.querySelectorAll('[data-filter]').forEach((node) => node.addEventListener('click', () => { state.filter = node.dataset.filter; state.shown = 48; renderCollection(); }));
$('#genre').addEventListener('change', (event) => { state.genre = event.target.value; state.shown = 48; renderCollection(); });
$('#sort').addEventListener('change', (event) => { state.sort = event.target.value; renderCollection(); });
$('#search').addEventListener('input', (event) => {
  state.query = event.target.value; state.shown = 48; clearTimeout(state.searchTimer); state.catalogRequest += 1;
  if (state.user && state.view === 'stats') { state.view = 'library'; renderShell(); }
  if (state.view === 'feed') { feed.invalidate(); state.searchTimer = setTimeout(() => void feed.load(), 280); }
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
  try {
    const session = await api('/auth/session', { anonymous: true });
    if (session) {
      acceptSession(session); sessionChannel?.postMessage('session-changed');
      renderShell(); await loadLibrary(); if (state.user && state.hasSteam) void pollSync(`sync-json-user-${state.user.id}`, true);
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
