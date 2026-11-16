import { genresFor, hours, integer, achievementPercent, selectLibrary, summarize, genreBreakdown,
  splitBySource, playStationSummary, playtimeLabel, plural, hoursContext } from './library.js';
import { $, el, button, steamLink, demo, cover, coverWall, aborted, numbered, setNumbers, timeLabel, skeletonGrid, emptyState,
  stamp, hoursBar, loadingState } from './dom.js';
import { starDisplay } from './rating.js';
import { createGameDialog } from './game-detail.js';
import { createFeed } from './feed.js';
import { createNews } from './news.js';
import { barList } from './chart.js';
import { sendRequest, readBody, failureOf } from './net.js';
import { createJobPoller } from './jobs.js';
import { createProfile } from './profile.js';
import { userFromHash, viewFromHash, profileHref } from './social.js';
import { avatarFor } from './post.js';
import { createExplore } from './explore.js';
import { createNotifications } from './notifications.js';
import { STATUSES, statusLabel, indexStatuses, filterByStatus, statusCounts } from './status.js';

// Page copy per view. pageKey() folds the signed-out library and playtime
// views into the welcome copy, which is what they have always shown. `tab`
// names the page in the browser tab.
const PAGE_COPY = {
  welcome: { title: 'Games on PlayGraph', tab: 'Games',
    subtitle: 'Everything imported so far. Open a game to read its reviews.',
    search: 'Search games', searchLabel: 'Search games' },
  library: { title: 'Library', tab: 'Library',
    subtitle: 'Your Steam games, with the hours and achievements from your last sync.',
    search: 'Search your library', searchLabel: 'Search your library' },
  stats: { title: 'Playtime', tab: 'Playtime',
    subtitle: 'Where your hours went, by genre and by game.',
    search: 'Search your library', searchLabel: 'Search your library' },
  explore: { title: 'Explore', tab: 'Explore',
    subtitle: 'Trending this week, players to follow, and the rest of the catalog.',
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
  profile: { title: 'Player', tab: 'Player', subtitle: '',
    search: 'Search games', searchLabel: 'Search games' },
  notifications: { title: 'Notifications', tab: 'Notifications', subtitle: 'Likes, replies, reposts and new followers. Kept for 90 days.',
    search: 'Search games', searchLabel: 'Search games' },
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
// state.library holds Steam entries only, so every Steam view and total is
// exactly what it was. PlayStation entries sit apart in state.psnLibrary and
// get their own shelf behind the platform switch; the two are never summed.
const state = { user: null, csrf: '', library: [], psnLibrary: [], platform: 'steam', genres: [], catalog: [], total: 0,
  view: viewFromHash(location.hash) || (userFromHash(location.hash) ? 'profile' : 'library'), query: '', filter: 'all', status: '', genre: '', sort: 'playtime', shown: 48, statuses: new Map(), liked: new Set(), reposted: new Set(), bookmarked: new Set(),
  controller: new AbortController(), generation: 0, catalogRequest: 0,
  expiryTimer: null, searchTimer: null, syncing: false };
const privateView = () => Boolean(state.user) && ['library', 'software', 'stats'].includes(state.view);
const gameLibrary = () => state.library.filter((entry) => entry.game.content_kind !== 'software');
const psnShelf = () => state.view === 'library' && state.platform === 'psn' && state.psnLibrary.length > 0;
const shelfLibrary = () => state.view === 'software'
  ? state.library.filter((entry) => entry.game.content_kind === 'software') : psnShelf() ? state.psnLibrary : gameLibrary();
const shelfCopy = () => SHELF[state.view === 'software' ? 'software' : 'game'];
const pageKey = () => state.view === 'feed' && !state.user ? 'feedGuest'
  : ['feed', 'news', 'software', 'explore', 'profile', 'notifications'].includes(state.view) ? state.view
  : state.user ? (state.view === 'stats' ? 'stats' : 'library') : 'welcome';
const gameDialog = createGameDialog(state, api, report);
const news = createNews(state, api);
const feed = createFeed(state, api, gameDialog, () => navigate(state.user ? 'library' : 'explore'), report);
const profile = createProfile(state, api, gameDialog, report);
const explore = createExplore(state, api, gameDialog, report);
const notifications = createNotifications(state, api, gameDialog, report);
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
  const method = options.method && options.method !== 'GET' ? { csrf: state.csrf } : {};
  const response = await sendRequest(path, { ...options, ...method, signal: state.controller.signal });
  if (generation !== state.generation) throw new DOMException('Session changed', 'AbortError');
  if (response.status === 401 && anonymous) return null;
  if (response.status === 401 && state.user) {
    clearSession(); notify('Your session ended. Sign in again to continue.', true);
    void loadCatalog(); throw new DOMException('Session ended', 'AbortError');
  }
  const body = await readBody(response);
  if (generation !== state.generation) throw new DOMException('Session changed', 'AbortError');
  if (!response.ok) throw failureOf(response, body);
  return body;
}
function stopPolling() {
  syncPoller.stop(); state.syncing = false;
  $('#sync-button').disabled = false; $('#sync-button').textContent = 'Sync Steam';
}
function clearSession() {
  notifications.clear();
  stopPolling(); clearTimeout(state.expiryTimer); clearTimeout(state.searchTimer);
  state.controller.abort(); state.controller = new AbortController(); state.generation += 1; state.catalogRequest += 1;
  Object.assign(state, { user: null, csrf: '', library: [], psnLibrary: [], platform: 'steam', genres: [], catalog: [], total: 0,
    query: '', filter: 'all', status: '', statuses: new Map(), liked: new Set(), reposted: new Set(), bookmarked: new Set(), genre: '', view: 'library', shown: 48 });
  $('#search').value = ''; $('#genre').replaceChildren(new Option('All genres', ''));
  gameDialog.clear(); feed.clear(); profile.clear(); explore.clear(); $('#summary').replaceChildren(); $('#insights').replaceChildren();
  renderShell(); renderCollection();
}
function renderShell() {
  const own = privateView();
  const software = state.view === 'software';
  const isFeed = state.view === 'feed';
  const isNews = state.view === 'news';
  const isProfile = state.view === 'profile';
  const isNotifications = state.view === 'notifications';
  const onPsn = psnShelf();
  document.querySelectorAll('[data-view]').forEach((item) => {
    const active = item.dataset.view === state.view; item.classList.toggle('active', active);
    if (active) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
  });
  const account = $('#account'); account.replaceChildren();
  if (state.user) {
    const manage = el('a', 'account-manage');
    manage.href = profileHref(state.user.id); manage.title = 'Your profile and game room';
    manage.setAttribute('aria-label', `Open profile for ${state.user.display_name}`);
    const avatar = avatarFor(state.user.display_name, state.user.id, 'avatar', state.user.avatar_url);
    manage.append(avatar,
      el('span', 'account-name', state.user.display_name));
    account.append(manage, button('Sign out', 'signout', signOut));
  }
  else account.append(steamLink('Sign in'));
  $('#notification-bell').hidden = !state.user;
  $('#notifications').hidden = !isNotifications;
  $('.search').hidden = isNotifications;
  $('#nav-count').textContent = state.user ? integer(gameLibrary().length) : '';
  const isExplore = state.view === 'explore';
  $('#welcome').hidden = Boolean(state.user) || software || isFeed || isNews || isProfile || isExplore || isNotifications;
  $('#cta-band').hidden = $('#welcome').hidden;
  $('#summary').hidden = !own || !shelfLibrary().length;
  $('#insights').hidden = !own || software || !(onPsn ? state.psnLibrary : gameLibrary()).length;
  $('#collection').hidden = isFeed || isNews || isProfile || isNotifications || (state.view === 'stats' && Boolean(state.user) && gameLibrary().length > 0);
  $('#feed').hidden = !isFeed; $('#news').hidden = !isNews; $('#profile').hidden = !isProfile;
  $('#explore-social').hidden = !isExplore;
  $('#sync-button').hidden = demo.on || !state.user || !state.hasSteam || onPsn || isNotifications; $('#filters').hidden = !own;
  $('#sort').disabled = !own; $('#sort').value = own ? state.sort : 'name';
  const copy = PAGE_COPY[pageKey()]; const shelf = shelfCopy();
  // The welcome hero has its own headline, so the plain page heading steps aside.
  // A profile carries its own heading, the player's name.
  $('.page-heading').hidden = pageKey() === 'welcome' || isProfile;
  if ($('#welcome').hidden === false) void loadFrontReviews();
  // Your own shelf is named for you: it is a library to show off.
  const first = state.user?.display_name?.trim().split(/\s+/)[0];
  $('#page-title').textContent = own && first && pageKey() === 'library' && !software ? `${first}’s library` : copy.title;
  $('#page-subtitle').textContent = onPsn
    ? 'Your PlayStation games: trophies, and PS4 and PS5 hours when your PSN privacy settings share them.' : copy.subtitle;
  // An open game page owns the tab title until it closes.
  if (!$('#game-dialog').open && !isProfile) document.title = `${copy.tab} | PlayGraph`;
  $('#collection-title').textContent = onPsn ? 'Your PlayStation games' : own ? shelf.own : shelf.catalog;
  $('#collection-note').hidden = own && !software && !onPsn;
  $('#collection-note').textContent = onPsn ? PSN_NOTE : shelf.note;
  renderPlatformSwitch();
  $('#search').placeholder = matchMedia('(max-width: 560px)').matches ? 'Search' : copy.search;
  $('#search').setAttribute('aria-label', copy.searchLabel);
  $('#load-more').textContent = shelf.more;
  $('#sort').querySelector('[value="playtime"]').textContent = shelf.mostUsed;
  updateGenres();
  if (own) renderInsights(); else setHero(null);
}
// Said plainly wherever PlayStation data appears: it is public profile data
// read through Sony's unofficial app interface, not a Sony-approved link.
const PSN_NOTE = 'From your public PSN profile, read through PlayStation’s unofficial app interface. Not a Sony-approved connection. Sync it from your account page.';
// The platform switch only appears once this account has PlayStation games:
// with one platform there is nothing to switch between.
function renderPlatformSwitch() {
  const pc = $('#platform-pc'); const ps = $('#platform-psn');
  const available = Boolean(state.user) && state.psnLibrary.length > 0;
  $('.platform-switch').hidden = !available;
  const onPsn = psnShelf();
  pc.classList.toggle('selected', !onPsn); pc.setAttribute('aria-pressed', String(!onPsn));
  ps.classList.toggle('selected', onPsn); ps.setAttribute('aria-pressed', String(onPsn));
}
function selectPlatform(platform) {
  if (state.view !== 'library') navigate('library');
  Object.assign(state, { platform, query: '', genre: '', filter: 'all', status: '', shown: 48 });
  $('#search').value = ''; $('#genre').value = ''; renderShell(); renderCollection();
}
// The newest public reviews, on the signed-out front door: the first one at
// true size beside the headline, as proof of what a review here is; a row of
// covers from the latest eight; and four more as plain rows. Loaded once;
// with no reviews yet those parts just stay hidden.
let frontReviewsLoaded = false;
function reviewStars(review) {
  const wrap = el('span', 'review-rating'); wrap.append(starDisplay(review.rating));
  wrap.setAttribute('role', 'img'); wrap.setAttribute('aria-label', `Rated ${review.rating} out of 5`); return wrap;
}
// The stamp when the review has verified hours, a plain note when it does not.
function reviewProof(review) {
  const line = el('div', 'review-proof');
  if (review.verified_playtime_minutes != null) line.append(stamp(`${hours(review.verified_playtime_minutes)} h verified`));
  else line.append(el('span', 'helper', 'No verified hours'));
  line.append(reviewStars(review)); return line;
}
function frontArt(game, className = 'front-cover') {
  const art = button('', className, () => gameDialog.open(game)); art.setAttribute('aria-label', `Open ${game.name}`); art.append(cover(game)); return art;
}
// "Rosa Okafor reviewed Cold Harbor", with the date at the end of the line.
function reviewedLine(review, game) {
  const head = el('div', 'reviewed-line');
  const line = el('p'); line.append(el('strong', '', review.author_name), ' reviewed ', button(game.name, 'title-link', () => gameDialog.open(game)));
  head.append(line, el('span', 'date', timeLabel(review.created_at))); return head;
}
function frontRow({ review, game }, className) {
  const row = el('article', className); const body = el('div', 'front-body');
  body.append(reviewedLine(review, game), reviewProof(review));
  if (review.body) body.append(el('p', 'front-review-body', review.body));
  row.append(frontArt(game), body); return row;
}
// One cover in the straight row under the lettered list, captioned like a
// receipt: who reviewed it and with how many verified hours.
function tickerItem({ review, game }) {
  const item = frontArt(game, 'ticker-item');
  const first = (review.author_name || 'A player').split(' ')[0];
  const caption = el('span', 'ticker-caption');
  caption.append(el('span', 'ticker-name', game.name), numbered('span', '', review.verified_playtime_minutes != null
    ? `${first}, ${hours(review.verified_playtime_minutes)} h verified` : `${first}, no verified hours`));
  item.setAttribute('aria-label', `Open ${game.name}, reviewed by ${first}`);
  item.append(caption); return item;
}
async function loadFrontReviews() {
  if (frontReviewsLoaded) return; frontReviewsLoaded = true;
  try {
    const data = await api('/feed?limit=8&offset=0&q=', { anonymous: true });
    const items = data?.items ?? []; if (!items.length) return;
    const proof = frontRow(items[0], 'proof-card'); $('#front-proof').replaceChildren(proof); $('#front-proof').hidden = false;
    const seen = new Set(); const distinct = items.filter(({ game }) => !seen.has(game.id) && seen.add(game.id));
    $('#cover-row').replaceChildren(...distinct.slice(0, 6).map(tickerItem)); $('#cover-row-block').hidden = distinct.length < 3;
    $('#front-reviews-list').replaceChildren(...items.slice(1, 5).map((item) => frontRow(item, 'front-review')));
    $('#front-reviews').hidden = items.length < 2;
    void proofContext(items[0], proof);
  } catch { frontReviewsLoaded = false; }  // Optional extra: the catalog below still works without it.
}
// The hero review's hours bar needs everyone else's hours on the same game.
async function proofContext({ review, game }, card) {
  try {
    const reviews = await api(`/games/${game.id}/reviews?limit=20&offset=0`, { anonymous: true });
    const context = hoursContext(review.verified_playtime_minutes, (reviews || []).map((row) => row.verified_playtime_minutes));
    if (context && card.isConnected) card.querySelector('.review-proof').after(hoursBar(context));
  } catch { /* the stamp alone still says it */ }
}
function updateGenres() {
  const names = [...new Set(shelfLibrary().flatMap((entry) => genresFor(entry.game)))].sort();
  $('#genre').replaceChildren(new Option('All genres', ''), ...names.map((name) => new Option(name, name)));
  if (!names.includes(state.genre)) state.genre = '';
  $('#genre').value = state.genre;
}
// The stat strip under the page title: four figures, each a label over a number.
function renderStrip(cells) {
  $('#summary').replaceChildren(...cells.map(([label, value]) => {
    const cell = el('div'); cell.append(el('dt', '', label), numbered('dd', '', value)); return cell;
  }));
}
// A section is a rule, a title and its content; an action sits at the right.
function sectionHead(title, action) {
  const head = el('div', 'section-heading'); head.append(el('h2', '', title)); if (action) head.append(action); return head;
}
function renderInsights() {
  const insights = $('#insights');
  if (psnShelf()) {
    // PlayStation stats on their own: trophies, and only the hours PSN shared.
    const ps = playStationSummary(state.psnLibrary);
    renderStrip([['Hours shared', hours(ps.minutes)], ['Games', integer(ps.games)], ['Trophies', integer(ps.earned)], ['Platinum', integer(ps.platinum)]]);
    // The most played games, or the most trophies when PSN hides every game's hours.
    const rank = (entry) => [entry.playtime_minutes || 0, entry.achievements_unlocked || 0];
    const ranked = [...state.psnLibrary].sort((a, b) => {
      const [ah, at] = rank(a); const [bh, bt] = rank(b); return bh - ah || bt - at;
    });
    const byHours = ranked[0]?.playtime_minutes > 0;
    insights.classList.remove('expanded'); setHero(ranked.map((entry) => entry.game));
    insights.replaceChildren(rankedSection(byHours ? 'Most played' : 'Most trophies', ranked.slice(0, 5),
      byHours ? (entry) => entry.playtime_minutes || 0 : (entry) => entry.achievements_unlocked || 0,
      byHours ? playtimeLabel : (entry) => `${integer(entry.achievements_unlocked)} trophies`));
    return;
  }
  if (!shelfLibrary().length) { setHero(null); return; }
  const summary = summarize(shelfLibrary()); const software = state.view === 'software';
  renderStrip([['Hours', hours(summary.minutes)], [software ? 'Apps' : 'Games', integer(summary.games)],
    [software ? 'Used' : 'Played', integer(summary.played)], ['Achievements', integer(summary.unlocked)]]);
  const full = state.view === 'stats';
  const ranked = selectLibrary(gameLibrary()).filter((entry) => entry.playtime_minutes > 0).slice(0, full ? 10 : 5);
  setHero(software ? null : [...gameLibrary()].sort((a, b) => b.playtime_minutes - a.playtime_minutes).map((entry) => entry.game));
  if (software) return;
  insights.classList.toggle('expanded', full);
  insights.replaceChildren(rankedSection(full ? 'Top 10 by playtime' : 'Most played', ranked,
    (entry) => entry.playtime_minutes, (entry) => `${hours(entry.playtime_minutes)} h`), genreSection(full));
}
// Your library heading sits on a tilted wall of your own covers, most played
// first, drifting slowly behind your name.
function setHero(games) {
  const list = ['library', 'stats'].includes(state.view) ? (games || []).filter(Boolean).slice(0, 27) : [];
  const heading = $('.page-heading'); const key = list.map((game) => game.id).join(',');
  if (heading.dataset.hero === key) return;
  heading.dataset.hero = key; heading.querySelector('.cover-wall')?.remove();
  heading.classList.toggle('has-hero', Boolean(key));
  if (key) heading.prepend(coverWall(list));
}
// The front door and the closing band share one wall built from the catalog.
function fillWalls() {
  const games = state.catalog.slice(0, 27); const key = games.map((game) => game.id).join(',');
  if (!key || $('#welcome').dataset.wall === key) return;
  $('#welcome').dataset.wall = key;
  $('#welcome-wall').replaceChildren(coverWall(games));
  $('#cta-wall').replaceChildren(coverWall([...games].reverse(), { rows: 2 }));
}
// A ranked list: muted rank, small cover, title, a flat bar and the value.
function rankedSection(title, entries, value, text) {
  const section = el('section', 'insight most-played'); section.append(sectionHead(title));
  if (!entries.length) { section.append(el('p', 'helper', 'Nothing played yet.')); return section; }
  section.append(barList(entries.map((entry, index) => {
    const lead = el('span', 'bar-lead'); lead.append(el('span', 'bar-rank num', String(index + 1)), cover(entry.game));
    const percent = achievementPercent(entry); const psn = entry.source === 'psn';
    return { label: entry.game.name, value: value(entry), text: text(entry), lead,
      detail: percent == null ? 'No achievement data' : `${Math.round(percent)}% of ${psn ? 'trophies' : 'achievements'}`,
      name: `Open ${entry.game.name}, ${text(entry)}`, onSelect: () => gameDialog.open(entry.game) };
  })));
  return section;
}
function genreSection(full) {
  const section = el('section', 'insight genre-card');
  section.append(sectionHead('Top genres', full ? null : button('See all playtime', 'text-button', () => navigate('stats'))));
  const genres = full ? state.genres : state.genres.slice(0, 5);
  if (!genres.length) section.append(el('p', 'helper', 'Genres show up after your first sync.'));
  else section.append(barList(genres.map((genre) => {
    const games = `${integer(genre.game_count)} ${plural(genre.game_count, 'game')}`;
    return { label: genre.genre, value: genre.total_minutes, text: `${hours(genre.total_minutes)} h`, detail: games,
      name: `${genre.genre}: ${hours(genre.total_minutes)} hours across ${games}. Show these games.`,
      onSelect: () => showGenre(genre.genre) };
  })));
  section.append(el('p', 'card-footnote', 'A game can carry several genres and its hours count toward each, so these totals overlap.'));
  return section;
}
function showGenre(genre) {
  navigate('library');
  state.genre = genre; $('#genre').value = genre; renderCollection();
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  $('#collection').scrollIntoView({ behavior: still ? 'auto' : 'smooth', block: 'start' });
}
function renderCollection() {
  const own = privateView(); const filtered = own ? ownShelf() : state.catalog.map((game) => ({ game }));
  const total = own ? filtered.length : state.total; const visible = own ? filtered.slice(0, state.shown) : filtered;
  const grid = $('#games'); grid.replaceChildren(); grid.removeAttribute('aria-busy'); const shelf = shelfCopy();
  if (own) { renderFilterCounts(shelf); renderStatusFilter(); }
  for (const entry of visible) {
    const game = entry.game; const card = el('article', 'game-card'); const open = button('', '', () => gameDialog.open(game));
    const psn = entry.source === 'psn';
    open.setAttribute('aria-label', `Open ${game.name}${!own ? '' : entry.unowned
      ? `, not in your library, ${statusLabel(state.statuses.get(game.id)?.status)}` : psn
      ? `, PlayStation, ${entry.playtime_minutes == null ? 'hours not shared' : `${hours(entry.playtime_minutes)} hours played`}`
      : `, ${hours(entry.playtime_minutes)} hours ${shelf.verb}`}`);
    const art = cover(game); const badge = own && coverBadge(entry); if (badge) art.append(badge);
    open.append(art, el('h3', 'game-name', game.name), numbered('span', 'game-meta', caption(entry, own, shelf)));
    card.append(open); grid.append(card);
  }
  if (!visible.length) {
    const filtering = state.query || state.genre || state.filter !== 'all' || state.status;
    const actions = [];
    if (filtering) actions.push(button('Clear filters', 'button secondary', resetFilters));
    else if (state.user && state.hasSteam) actions.push(button('Sync Steam', 'button primary', syncLibrary));
    else if (state.user) { const connect = el('a', 'button primary', 'Connect Steam'); connect.href = '/account'; actions.push(connect); }
    else actions.push(steamLink('Create an account'));
    const empty = emptyState({ title: filtering ? 'No matches' : own ? 'No games yet' : 'No games in the catalog yet',
      text: filtering ? 'Try another search or clear your filters.'
        : own ? (state.hasSteam ? 'Sync Steam to bring in your games. Your Steam game details need to be public.' : 'Connect Steam to bring in your library and put verified hours on your reviews.') : 'Games show up here once someone syncs a Steam library.',
      actions });
    grid.append(empty);
  }
  $('#result-count').textContent = integer(total); $('#load-more').hidden = visible.length >= total;
  setNumbers($('#shown-count'), total ? `${integer(visible.length)} of ${integer(total)} ${shelf.plural}` : '');
  document.querySelectorAll('[data-filter]').forEach((chip) => { chip.classList.toggle('selected', chip.dataset.filter === state.filter); chip.setAttribute('aria-pressed', String(chip.dataset.filter === state.filter)); });
}
// Your shelf after search, genre, played and status filters. Choosing a
// status also brings in games with that status you don't own (your backlog),
// unless the played filter is on, since those games have no hours.
function ownShelf() {
  const kind = state.filter !== 'all' || psnShelf() ? null : state.view === 'software' ? 'software' : 'game';
  const narrowed = filterByStatus(selectLibrary(shelfLibrary(), state), state.statuses, state.status, kind);
  return state.status ? selectLibrary(narrowed, { ...state, filter: 'all' }) : narrowed;
}
// The status menu, with how many games carry each status.
function renderStatusFilter() {
  const counts = statusCounts(state.statuses); const select = $('#status-filter');
  for (const option of select.options) {
    const known = STATUSES.find(({ value }) => value === option.value);
    if (known) option.textContent = `${known.label} (${integer(counts[known.value])})`;
  }
  select.value = state.status;
}
// A small tag in the corner of a cover on your own shelf: a gold 100% or
// platinum, or "New" for a game not started yet. The same facts are in the
// caption and the button label, so the tag stays out of the reading order.
function coverBadge(entry) {
  const platinum = entry.source === 'psn' && entry.trophies?.earned.platinum > 0;
  const label = platinum ? 'Platinum' : achievementPercent(entry) === 100 ? '100%' : entry.playtime_minutes === 0 ? 'New' : null;
  if (!label) return null;
  const tag = el('span', `cover-badge${label === 'New' ? '' : ' gold'}`, label); tag.setAttribute('aria-hidden', 'true'); return tag;
}
// The line under a cover: your hours (and achievement share) on your own
// shelf, the first genre in the catalog.
function caption(entry, own, shelf) {
  const game = entry.game;
  if (!own) return genresFor(game)[0] || 'Steam';
  const status = statusLabel(state.statuses.get(game.id)?.status);
  if (entry.unowned) return `Not owned · ${status}`;
  const parts = [];
  if (entry.source === 'psn') parts.push('PlayStation', playtimeLabel(entry));
  else parts.push(entry.playtime_minutes > 0 ? `${hours(entry.playtime_minutes)} h` : shelf.unused);
  const pct = achievementPercent(entry);
  if (entry.source === 'psn' && entry.trophies?.earned.platinum > 0) parts.push('Platinum');
  else if (pct != null && entry.playtime_minutes !== 0) parts.push(`${Math.round(pct)}%`);
  if (status) parts.push(status);
  return parts.join(' · ');
}
// "All games 12", "Played 9", "Yet to play 3": counts follow the search and genre.
function renderFilterCounts(shelf) {
  const base = selectLibrary(shelfLibrary(), { ...state, filter: 'all' });
  const counts = { all: base.length, played: base.filter((entry) => entry.playtime_minutes > 0).length,
    unplayed: base.filter((entry) => entry.playtime_minutes === 0).length };
  const labels = { all: shelf.all, played: shelf.used, unplayed: shelf.unused };
  document.querySelectorAll('[data-filter]').forEach((chip) => {
    chip.replaceChildren(labels[chip.dataset.filter], ' ', el('span', 'tab-count num', integer(counts[chip.dataset.filter])));
  });
}
async function loadCatalog(append = false) {
  const request = ++state.catalogRequest; const generation = state.generation; const offset = append ? state.catalog.length : 0;
  $('#load-more').disabled = true;
  if (!append) { state.catalog = []; state.total = 0; $('#result-count').textContent = ''; const games = $('#games'); games.setAttribute('aria-busy', 'true'); games.replaceChildren(loadingState('Loading games…'), ...skeletonGrid()); }
  try {
    const data = await api(`/games?kind=${state.view === 'software' ? 'software' : 'game'}&limit=48&offset=${offset}&q=${encodeURIComponent(state.query)}`);
    if (request !== state.catalogRequest || generation !== state.generation || privateView()) return;
    state.catalog = append ? state.catalog.concat(data.games) : data.games; state.total = data.total; renderCollection();
    if (!append && state.view === 'library') fillWalls();
  } catch (error) {
    if (request !== state.catalogRequest || aborted(error)) return;
    if (!append) {
      const games = $('#games'); games.removeAttribute('aria-busy');
      games.replaceChildren(emptyState({ kind: 'error', title: 'Couldn’t load the catalog', text: 'Check your connection, then try again.',
        actions: [button('Try again', 'button secondary', () => void loadCatalog())] }));
    }
    report(error);
  } finally { if (request === state.catalogRequest) $('#load-more').disabled = false; }
}
async function loadLibrary() {
  const generation = state.generation;
  // Statuses are extra: the library still shows if they fail to load.
  const [library, statuses] = await Promise.all([api('/me/library'), api('/me/statuses').catch((error) => {
    if (aborted(error)) throw error; return [];
  })]);
  if (!state.user || generation !== state.generation) return;
  const { steam, psn } = splitBySource(library);
  state.library = steam; state.psnLibrary = psn; state.genres = genreBreakdown(steam); state.statuses = indexStatuses(statuses);
  renderShell(); if (privateView()) renderCollection();
}
function navigate(view, remember = true) {
  if (view === 'notifications' && !state.user) view = 'library';
  feed.invalidate();
  if (remember && location.hash !== `#view=${view}`) history.pushState(null, '', `#view=${view}`);
  Object.assign(state, { view, query: '', genre: '', filter: 'all', status: '', shown: 48 }); state.catalogRequest += 1;
  if (view !== 'library') state.platform = 'steam';
  $('#search').value = ''; $('#genre').value = ''; clearTimeout(state.searchTimer); renderShell();
  if (view === 'feed') return feed.load(); else if (view === 'news') return news.load();
  else if (view === 'notifications') return notifications.load();
  else if (view === 'explore') return Promise.all([explore.load(), loadCatalog()]);
  else if (privateView()) renderCollection(); else return loadCatalog();
}
function resetFilters() {
  Object.assign(state, { query: '', genre: '', filter: 'all', status: '', shown: 48 }); $('#search').value = ''; $('#genre').value = '';
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
const syncPoller = createJobPoller({
  fetchJob: (jobId) => api(`/me/sync/status/${encodeURIComponent(jobId)}`),
  begin: () => { const generation = state.generation; return () => !state.user || generation !== state.generation; },
  onActive: (job) => {
    state.syncing = true; $('#sync-button').disabled = true; $('#sync-button').textContent = 'Syncing…';
    notify(job.status === 'in_progress' ? 'Syncing with Steam. Large libraries can take several minutes; achievement checks run one game at a time.' : 'Steam sync is queued. Keep the worker running and we will update your library when it finishes.');
  },
  onSettled: async (job, restore) => {
    const generation = state.generation;
    stopPolling(); if (restore) return;
    if (job.status === 'complete') { await loadLibrary(); if (state.user && generation === state.generation) notify(`Your library is up to date. ${integer(job.result?.games_synced ?? state.library.length)} games synced.`); }
    else notify(job.status === 'failed' ? 'The sync failed. Check the worker and Steam availability, then try again.' : 'This sync is no longer in the queue. Start a new sync when you are ready.', true);
  },
  onError: (error, restore) => { stopPolling(); if (!restore) report(error); },
});
const pollSync = (jobId, restore = false) => syncPoller.poll(jobId, restore);
document.querySelectorAll('[data-view]').forEach((node) => node.addEventListener('click', () => navigate(node.dataset.view)));
document.querySelectorAll('[data-filter]').forEach((node) => node.addEventListener('click', () => { state.filter = node.dataset.filter; state.shown = 48; renderCollection(); }));
$('#status-filter').addEventListener('change', (event) => { state.status = event.target.value; state.shown = 48; renderCollection(); });
document.addEventListener('status-changed', () => {
  if (privateView()) renderCollection();
  else if (state.view === 'profile' && state.user?.id === userFromHash(location.hash)) void profile.open(state.user.id);
});
$('#platform-pc').addEventListener('click', () => { if (state.platform !== 'steam') selectPlatform('steam'); });
$('#platform-psn').addEventListener('click', () => { if (state.user && state.psnLibrary.length) selectPlatform('psn'); });
$('#genre').addEventListener('change', (event) => { state.genre = event.target.value; state.shown = 48; renderCollection(); });
$('#sort').addEventListener('change', (event) => { state.sort = event.target.value; renderCollection(); });
$('#search').addEventListener('input', (event) => {
  // Searching from a profile searches the catalog.
  if (state.view === 'profile') { const typed = event.target.value; navigate('explore'); event.target.value = typed; }
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
  notifications.start();
  state.hasSteam = session.has_steam; state.authProvider = session.auth_provider;
  state.hasPsn = Boolean(session.has_psn); state.psnEnabled = Boolean(session.psn_enabled);
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
      if (state.user && state.hasSteam && !demo.on) void pollSync(`sync-json-user-${state.user.id}`, true);
    }
  } catch (error) { report(error); } finally { checkingSession = false; }
}
sessionChannel?.addEventListener('message', (event) => { if (event.data === 'session-changed') void checkSession(); });
window.addEventListener('focus', () => void checkSession());
document.addEventListener('visibilitychange', () => { if (!document.hidden) void checkSession(); });
async function applyDemo() {
  try {
    const config = await sendRequest('/auth/config').then((r) => r.json());
    if (!config.demo) return;
    demo.on = true; $('#demo-banner').hidden = false;
    document.querySelectorAll('[data-view="news"]').forEach((item) => item.remove());
    // Every way in becomes the demo player: the hero action and the closing band.
    document.querySelectorAll('[data-enter]').forEach((node) => {
      const enter = steamLink(); enter.className = node.className; node.replaceWith(enter);
    });
    $('.welcome-actions').nextElementSibling?.remove();
    $('#cta-title').textContent = 'See what a library with verified hours looks like.';
    $('#cta-note').textContent = 'Sample players and reviews. No account needed.';
  } catch { /* not the demo, or config unreachable: behave normally */ }
}
async function start() {
  await applyDemo();
  const loginError = new URLSearchParams(location.search).get('login_error');
  if (loginError === 'invite') {
    notify('PlayGraph is in a private beta right now. Ask for an invite to join.', true); history.replaceState(null, '', '/app');
  } else if (loginError !== null) {
    notify('Steam sign-in could not be completed. Start again using Connect Steam in this browser. If it repeats, check that the address matches APP_BASE_URL.', true); history.replaceState(null, '', '/app');
  }
  const steamConnected = new URLSearchParams(location.search).get('steam') === 'connected';
  if (steamConnected) history.replaceState(null, '', '/app');
  try {
    const session = await api('/auth/session', { anonymous: true });
    if (session) {
      acceptSession(session); sessionChannel?.postMessage('session-changed');
      renderShell(); $('#games').replaceChildren(loadingState('Loading your library…')); await loadLibrary();
      // Straight back from connecting Steam: start the first sync for them.
      if (steamConnected && state.hasSteam) { notify('Steam connected. Bringing in your library now.'); void syncLibrary(); }
      else if (state.user && state.hasSteam && !demo.on) void pollSync(`sync-json-user-${state.user.id}`, true);
    } else { renderShell(); await loadCatalog(); }
  } catch (error) { report(error); if (!state.user) { renderShell(); await loadCatalog(); } }
}
let linkRequest = 0;
async function openLinkedReview() {
  const request = ++linkRequest;
  const view = viewFromHash(location.hash);
  if (view) { await navigate(view, false); return; }
  const userId = userFromHash(location.hash);
  if (userId) { await showProfile(userId); return; }
  const match = /^#(review|game)=([1-9][0-9]{0,9})$/.exec(location.hash);
  if (!match) { if (!location.hash && state.view !== 'library') await navigate('library', false); return; }
  try {
    if (match[1] === 'review') await gameDialog.openThread(Number(match[2]));
    else {
      const game = await api(`/games/${match[2]}`);
      if (request === linkRequest) await gameDialog.open(game);
    }
  } catch (error) { if (request === linkRequest) report(error); }
}
// Author names link to #user=ID. The profile replaces the current view,
// closing a game page if one is open.
function showProfile(id) {
  if ($('#game-dialog').open) $('#game-dialog').close();
  feed.invalidate(); clearTimeout(state.searchTimer); state.catalogRequest += 1;
  Object.assign(state, { view: 'profile', query: '', genre: '', filter: 'all', status: '' }); $('#search').value = '';
  renderShell(); window.scrollTo(0, 0); return profile.open(id);
}
window.addEventListener('hashchange', () => void openLinkedReview());
await start();
sessionReady = true;
await openLinkedReview();
document.body.classList.remove('app-booting'); $('#page-loading').hidden = true;
