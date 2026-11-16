import { genresFor, hours, countPair, achievementPercent, isPlayStationGame, playtimeLabel, hoursContext, TROPHY_GRADES } from './library.js';
import { $, el, button, cover, heroArt, demo, steamLink, timeLabel, formError, numbered, copyText, stamp, hoursBar } from './dom.js';
import { createRatingPicker, starDisplay } from './rating.js';
import { authorLink, reportControl } from './profile.js';
import { STATUSES, withStatus } from './status.js';
import { likeButton, loadLikes, badgeFor, avatarFor } from './post.js';

// One label/value pair in a <dl>: <div><dt>label</dt><dd>value</dd></div>.
// Digits in the value set in tabular figures; `className` tags the <dd>.
export function statCell(label, value, className = '') {
  const cell = el('div'); cell.append(el('dt', '', label), numbered('dd', className, value)); return cell;
}

// Your numbers sit under the art: hours and achievements when the game is in
// your library, PlayStation hours and trophies on their own lines, otherwise a
// plain note. Steam and PlayStation numbers are never added together.
function renderStats(state, game, entry, psnEntry) {
  const stats = el('dl', 'detail-stats'); const software = game.content_kind === 'software';
  if (entry) {
    const pct = achievementPercent(entry);
    stats.append(statCell(software ? 'Time used' : 'Played', `${hours(entry.playtime_minutes)} h`, entry.playtime_minutes > 0 ? 'played' : ''),
      statCell('Achievements', pct == null ? 'None' : countPair(entry.achievements_unlocked, entry.achievements_total)));
  }
  if (psnEntry) {
    // The server sums the grades into achievements_unlocked/total for PSN rows.
    const trophyCount = psnEntry.trophies ? countPair(psnEntry.achievements_unlocked, psnEntry.achievements_total) : 'None';
    stats.append(statCell('PlayStation hours', playtimeLabel(psnEntry), psnEntry.playtime_minutes > 0 ? 'played' : ''),
      statCell('Trophies', trophyCount));
  }
  if (!entry && !psnEntry) {
    stats.append(statCell('Your library', !state.user ? 'Sign in to see your hours'
      : isPlayStationGame(game) ? 'Not in your PlayStation library' : 'Not in your Steam library'));
  }
  return stats;
}

// Reference sheet: label column, value column, hairline rows. The platform
// list is returned too, as IGDB replaces it once the game details arrive.
function renderMetadata(game, software, playstation) {
  const fields = el('dl', 'metadata-table');
  const platforms = el('ul', 'platform-badges'); platforms.append(el('li', 'platform-badge', playstation ? 'PlayStation' : 'PC'));
  const rows = [['Genres', genresFor(game).join(', ') || 'Not listed'], ['Category', software ? 'Software' : 'Game'],
    ['Platforms', platforms], ['Source', playstation ? 'Public PSN profile' : 'Steam']];
  for (const [name, value] of rows) {
    const dd = el('dd'); dd.append(value); fields.append(el('dt', '', name), dd);
  }
  return { fields, platforms };
}

export function createGameDialog(state, api, report = () => {}) {
  let requestId = 0; let pageTitle = document.title;
  const dialog = $('#game-dialog'); const content = $('#detail-content');
  $('#close-dialog').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { requestId += 1; content.replaceChildren(); document.title = pageTitle; });
  async function open(game, createdReview = null, threadReview = null, editRequested = false) {
    const request = ++requestId; content.replaceChildren();
    if (!dialog.open) pageTitle = document.title;
    document.title = `${game.name} | PlayGraph`;
    const entry = state.library.find((row) => row.game.id === game.id);
    // PlayStation numbers live on their own entry and are shown on their own
    // lines; they are never added to Steam hours or achievements.
    const psnEntry = (state.psnLibrary || []).find((row) => row.game.id === game.id);
    const playstation = isPlayStationGame(game);
    const software = game.content_kind === 'software';
    const profile = el('div', 'game-profile');
    const aside = el('aside', 'game-profile-aside'); aside.append(cover(game));
    const main = el('div', 'game-profile-main');
    const header = el('header', 'detail-header');
    const info = el('div', 'detail-info');
    const title = el('h2', '', game.name); title.id = 'detail-title';
    const ratings = el('div', 'detail-ratings'); ratings.hidden = true;
    info.append(title, factsRow(game, entry, psnEntry, software, playstation), ratings);
    aside.append(renderStats(state, game, entry, psnEntry));
    if (psnEntry?.trophies) aside.append(trophyGrades(psnEntry.trophies));
    const { fields, platforms } = renderMetadata(game, software, playstation);
    const about = el('div', 'detail-about');
    info.append(fields, about, ...syncNotes(entry, psnEntry, playstation));
    void loadDetails(game, request, { platforms, fields, about });
    header.append(info); main.append(header); profile.append(aside, main);
    // The page opens on the game's own art, darkened into the room.
    const banner = el('div', 'detail-banner'); banner.setAttribute('aria-hidden', 'true'); banner.append(heroArt(game));
    content.append(banner, profile);
    const reviews = renderReviews(game, request, { createdReview, threadReview, editRequested, ratings });
    main.append(reviews.tabs, ...reviews.panels); reviews.selectTab(createdReview ? 1 : 0);
    const actions = el('div', 'detail-actions');
    actions.append(button(state.user ? 'Your review' : 'Write a review', 'button primary', () => { reviews.selectTab(1); reviews.controls[1].focus(); }));
    let store = null;
    if (!playstation && !demo.on) {
      store = el('a', 'text-link', 'View on Steam'); store.href = `https://store.steampowered.com/app/${Number(game.steam_appid)}/`;
      store.target = '_blank'; store.rel = 'noopener noreferrer';
    }
    const share = button('Copy game link', 'text-link', async () => {
      if (await copyText(`${location.origin}/app#game=${game.id}`)) share.textContent = 'Link copied';
      else report(new Error('Could not copy the link. Try again with clipboard access enabled.'));
    });
    const links = el('div', 'detail-links'); if (store) links.append(store); links.append(share);
    aside.append(actions);
    if (state.user) aside.append(statusPicker(game));
    aside.append(links);
    if (!dialog.open) dialog.showModal(); dialog.scrollTop = 0; $('#close-dialog').focus();
    await reviews.load();
  }
  // Your status for this game. Saved as soon as it changes; a sync never
  // touches it. The demo is read-only, so there it only shows.
  function statusPicker(game) {
    const wrap = el('div', 'status-picker');
    const label = el('label', 'status-label', 'Your status'); const select = el('select');
    select.id = 'game-status'; label.htmlFor = select.id;
    select.append(new Option('No status', ''), ...STATUSES.map(({ value, label: text }) => new Option(text, value)));
    let saved = state.statuses?.get(game.id)?.status || ''; select.value = saved;
    const problem = el('p', 'form-error'); problem.setAttribute('role', 'alert'); problem.hidden = true;
    const field = el('span', 'select'); field.append(select);
    wrap.append(label, field, problem);
    if (demo.on) { select.disabled = true; wrap.append(el('p', 'helper', 'The demo is read-only.')); return wrap; }
    select.addEventListener('change', async () => {
      const chosen = select.value; select.disabled = true; problem.hidden = true;
      try {
        const row = chosen ? await api(`/me/games/${game.id}/status`, { method: 'PUT', body: JSON.stringify({ status: chosen }) })
          : (await api(`/me/games/${game.id}/status`, { method: 'DELETE' }), null);
        state.statuses = withStatus(state.statuses || new Map(), game.id, row); saved = chosen;
        document.dispatchEvent(new CustomEvent('status-changed'));
      } catch (failure) { select.value = saved; formError(problem, failure); } finally { select.disabled = false; }
    });
    return wrap;
  }
  // One quiet line of facts under the title: the genres, what it is and where it is from.
  function factsRow(game, entry, psnEntry, software, playstation) {
    const facts = el('p', 'detail-facts');
    const genres = genresFor(game).slice(0, 3).join(', ');
    const parts = [...(genres ? [genres] : []), software ? 'Software' : 'Game', ...(playstation ? ['PlayStation'] : ['PC', 'Steam'])];
    for (const part of parts) facts.append(el('span', '', part));
    return facts;
  }
  // The community rating, shown only when every review has loaded, so the
  // average and the bars describe all of them and not just the first page.
  function ratingSummary(target, reviews) {
    if (!reviews.length) { target.hidden = true; return; }
    const average = reviews.reduce((sum, review) => sum + review.rating, 0) / reviews.length;
    const rounded = Math.round(average * 2) / 2;
    const stars = el('span', 'review-rating'); stars.append(starDisplay(rounded));
    stars.setAttribute('role', 'img'); stars.setAttribute('aria-label', `Average ${average.toFixed(1)} out of 5`);
    const score = el('span', 'rating-score num', average.toFixed(1));
    score.setAttribute('aria-hidden', 'true');
    const count = numbered('span', 'rating-count', `${reviews.length} ${reviews.length === 1 ? 'review' : 'reviews'}`);
    // Ten half-star buckets, 0.5 to 5, as mini bars sharing one maximum.
    const buckets = Array.from({ length: 10 }, (_, i) => reviews.filter((review) => Math.round(review.rating * 2) === i + 1).length);
    const most = Math.max(...buckets);
    const bars = el('span', 'rating-bars'); bars.setAttribute('aria-hidden', 'true');
    for (const value of buckets) { const bar = el('span'); bar.style.setProperty('--h', String(most ? value / most : 0)); bars.append(bar); }
    target.replaceChildren(score, stars, bars, count); target.hidden = false;
  }
  // Notes under the metadata saying where the numbers above came from.
  function syncNotes(entry, psnEntry, playstation) {
    const notes = [];
    if (entry) { const synced = el('p', 'helper', 'Your numbers are from your Steam sync on '); synced.append(el('span', 'date', timeLabel(entry.captured_at))); notes.push(synced); }
    if (psnEntry) {
      const synced = el('p', 'helper', 'Your PlayStation numbers are from your public PSN profile, synced on ');
      synced.append(el('span', 'date', timeLabel(psnEntry.captured_at)), '. Hours appear only for PS4 and PS5 games when your privacy settings share them.');
      notes.push(synced);
    }
    if (!entry && !psnEntry) notes.push(el('p', 'helper', playstation
      ? 'Imported from a public PSN profile. PlayStation games are kept separate from Steam games, even with the same name.'
      : 'Imported from Steam.'));
    return notes;
  }
  // Summary, release date and platforms come from IGDB on the single-game
  // endpoint; list responses leave them out. The page works without them.
  async function loadDetails(game, request, { platforms, fields, about }) {
    let details;
    try { details = await api(`/games/${game.id}`); } catch { return; }
    if (request !== requestId) return;
    if (details.platforms?.length) {
      platforms.replaceChildren(...details.platforms.map((p) => el('li', 'platform-badge', p.abbreviation || p.name)));
    }
    if (details.first_release_date) {
      const released = new Date(details.first_release_date).toLocaleDateString(undefined,
        { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' });
      fields.append(el('dt', '', 'Released'), el('dd', 'date', released));
    }
    if (details.summary) about.append(el('p', 'detail-summary', details.summary));
    if (details.summary || details.platforms?.length || details.first_release_date) {
      const credit = el('a', 'text-link detail-credit', 'Game details from IGDB');
      credit.href = 'https://www.igdb.com/'; credit.target = '_blank'; credit.rel = 'noopener noreferrer';
      about.append(credit);
    }
  }
  // The tabbed reviews area: community reviews, and the signed-in player's own.
  // Returns the tab strip, its panels and a loader for both lists.
  function renderReviews(game, request, { createdReview, threadReview, editRequested, ratings }) {
    const writeSection = el('section', 'write-review');
    if (createdReview && state.user) showOwnReview(createdReview, editRequested);
    else if (state.user) writeSection.append(el('p', 'helper', 'Checking for your review…'));
    else writeSection.append(el('h3', '', 'Sign in to review this game'), el('p', 'muted', 'Your rating and review go here. Connect Steam and it shows your hours too.'), steamLink());
    const section = el('section', 'reviews-section');
    if (threadReview) section.append(el('h3', '', 'Review discussion'));
    section.append(el('p', 'helper', threadReview ? 'Comments on this review are public.' : 'Most verified playtime first. Review stats reflect the moment of posting.'));
    if (threadReview) section.append(button('All reviews', 'text-button', () => open(game)));
    const list = el('div', 'reviews-list'); list.append(el('p', 'helper', 'Loading reviews…')); section.append(list);
    const tabs = el('div', 'detail-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Game information');
    const panels = [section, writeSection]; const labels = ['Community reviews', 'Your review'];
    const controls = labels.map((label, index) => {
      const control = button(label, 'detail-tab', () => selectTab(index));
      control.id = `detail-tab-${index}`; control.setAttribute('role', 'tab'); control.setAttribute('aria-controls', `detail-panel-${index}`);
      panels[index].id = `detail-panel-${index}`; panels[index].setAttribute('role', 'tabpanel'); panels[index].setAttribute('aria-labelledby', control.id); panels[index].tabIndex = 0;
      control.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault(); const last = labels.length - 1;
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? last : (index + (event.key === 'ArrowRight' ? 1 : last)) % labels.length;
        selectTab(next); controls[next].focus();
      });
      tabs.append(control); return control;
    });
    function selectTab(selected) { panels.forEach((panel, index) => { panel.hidden = index !== selected; controls[index].setAttribute('aria-selected', String(index === selected)); controls[index].tabIndex = index === selected ? 0 : -1; }); }
    function showOwnReview(own, editing = false) {
      writeSection.replaceChildren();
      if (editing) {
        const form = reviewForm(game, request, own, () => { showOwnReview(own); writeSection.querySelector('button.button').focus(); });
        writeSection.append(form); form.querySelector('input').focus(); return;
      }
      writeSection.append(el('h3', '', 'Your review'), reviewCard(own),
        button('Edit review', 'button secondary', () => showOwnReview(own, true)));
      const removal = el('div', 'review-removal'); const warning = el('div', 'delete-confirmation'); warning.hidden = true;
      const problem = el('p', 'form-error'); problem.setAttribute('role', 'alert'); problem.hidden = true;
      const remove = button('Delete review', 'text-button', () => { warning.hidden = false; remove.hidden = true; keep.focus(); });
      const keep = button('Keep review', 'button secondary', () => { warning.hidden = true; remove.hidden = false; remove.focus(); });
      const confirm = button('Delete permanently', 'button secondary', async () => {
        confirm.disabled = true; keep.disabled = true; problem.hidden = true;
        try {
          await api(`/reviews/${own.id}`, { method: 'DELETE' });
          document.dispatchEvent(new CustomEvent('review-changed'));
          if (request === requestId) { history.replaceState(null, '', `/app#game=${game.id}`); await open(game); }
        } catch (failure) { formError(problem, failure); } finally { confirm.disabled = false; keep.disabled = false; }
      });
      warning.append(el('p', '', 'Permanently delete your review and every comment in its discussion? This cannot be undone.'), keep, confirm, problem);
      removal.append(remove, warning); writeSection.append(removal);
    }
    let offset = 0; let pool = [];
    const error = el('p', 'form-error'); error.setAttribute('role', 'alert'); error.hidden = true; section.append(error);
    const more = button('More reviews', 'button secondary', () => void loadReviews()); more.hidden = true; section.append(more);
    async function loadReviews() {
      more.disabled = true; error.hidden = true;
      try {
        const reviews = await api(`/games/${game.id}/reviews?limit=20&offset=${offset}`);
        if (request !== requestId) return;
        if (!offset) { list.replaceChildren(); pool = []; }
        // Each reviewer's hours are measured against everyone loaded so far;
        // the list comes most verified first, so the longest is always in it.
        pool = pool.concat(reviews.map((review) => review.verified_playtime_minutes));
        for (const review of reviews) list.append(reviewCard(review, false, { context: hoursContext(review.verified_playtime_minutes, pool) }));
        if (state.user && reviews.length && state.liked) void loadLikes(api, state.liked, reviews).catch(report);
        if (!offset && reviews.length < 20) ratingSummary(ratings, reviews);
        if (!offset && !reviews.length) {
          const empty = el('div', 'review-empty'); empty.append(el('strong', '', 'No reviews for this game.'),
            el('span', '', 'Be the first. Your hours are attached automatically.'));
          list.append(empty);
        }
        offset += reviews.length; more.hidden = reviews.length < 20; more.textContent = 'More reviews';
      } catch (failure) {
        if (request === requestId) { if (!offset) list.replaceChildren(); formError(error, failure); more.hidden = false; more.textContent = 'Retry loading reviews'; }
      } finally { more.disabled = false; }
    }
    async function loadOwnReview() {
      writeSection.replaceChildren(el('p', 'helper', 'Checking for your review…'));
      try {
        const own = await api(`/me/games/${game.id}/review`);
        if (request !== requestId) return;
        writeSection.replaceChildren();
        if (own) showOwnReview(own);
        else writeSection.append(reviewForm(game, request));
      } catch (failure) {
        if (request !== requestId) return;
        const message = el('p', 'form-error'); message.setAttribute('role', 'alert'); formError(message, failure);
        writeSection.replaceChildren(message, button('Retry checking your review', 'button secondary', () => void loadOwnReview()));
      }
    }
    return {
      tabs, panels, controls, selectTab,
      load() {
        if (threadReview) list.replaceChildren(reviewCard(threadReview, true));
        return Promise.all([threadReview ? Promise.resolve() : loadReviews(), state.user && !createdReview ? loadOwnReview() : Promise.resolve()]);
      },
    };
  }
  // Earned of total for each PlayStation trophy grade, best first.
  function trophyGrades(trophies) {
    const list = el('dl', 'trophy-grades'); list.setAttribute('aria-label', 'PlayStation trophies by grade');
    for (const grade of TROPHY_GRADES) {
      list.append(statCell(grade[0].toLocaleUpperCase() + grade.slice(1), countPair(trophies.earned[grade], trophies.total[grade])));
    }
    if (trophies.progress != null) {
      list.append(statCell('Progress', `${trophies.progress}%`));
    }
    return list;
  }
  function reviewForm(game, request, existing = null, cancel = null) {
    const form = el('form', 'review-form'); form.append(el('h3', '', existing ? 'Edit your review' : 'Write a review'));
    const rating = createRatingPicker(existing?.rating ?? 0);
    const bodyLabel = el('label', '', 'Your review'); const body = el('textarea'); body.name = 'body'; body.rows = 4; body.maxLength = 10000;
    body.placeholder = 'What would you tell a friend who’s about to play it?'; bodyLabel.append(body);
    body.value = existing?.body || '';
    const disclosure = el('p', 'review-disclosure', existing ? 'Your changes are public. The original posting date and verified play stats will stay the same.' : 'Public review: your display name, rating, text, and available verified playtime and achievement percentage will be visible to everyone. Stats are saved as they are now.');
    const error = el('p', 'form-error'); error.setAttribute('role', 'alert'); error.hidden = true;
    const submit = el('button', 'button primary', existing ? 'Save changes' : 'Publish review'); submit.type = 'submit';
    form.append(rating.element, bodyLabel, disclosure, error, submit);
    const cancelButton = cancel ? button('Cancel', 'text-button', cancel) : null;
    if (cancelButton) form.append(cancelButton);
    form.addEventListener('submit', async (event) => {
      event.preventDefault(); error.hidden = true;
      if (rating.value() < 0.5) { error.textContent = 'Choose at least half a star before publishing.'; error.hidden = false; rating.input.focus(); return; }
      submit.disabled = true; if (cancelButton) cancelButton.disabled = true;
      try {
        const created = await api(existing ? `/reviews/${existing.id}` : `/games/${game.id}/reviews`, { method: existing ? 'PATCH' : 'POST', body: JSON.stringify({ rating: rating.value(), body: body.value.trim() || null }) });
        document.dispatchEvent(new CustomEvent('review-changed'));
        if (request === requestId) await open(game, created);
      } catch (failure) { formError(error, failure); } finally { submit.disabled = false; if (cancelButton) cancelButton.disabled = false; }
    }); return form;
  }
  // A review is a row, not a card: who wrote it on the left; the verified
  // stamp, the stars, the text and the actions on the right. `byline: false`
  // leaves the left column out for lists that name the author themselves.
  function reviewCard(review, expandComments = false, { context = null, byline = true, like = true } = {}) {
    const card = el('article', byline ? 'review-card' : 'review-card no-byline'); const author = review.author_name || `Player ${review.user_id}`;
    if (byline) {
      const by = el('div', 'review-by'); const who = el('div');
      // Name and badge share a line; without a badge the name stands alone as before.
      const earned = badgeFor(review.author_badge); let named = authorLink(author, review.user_id, 'review-author');
      if (earned) { const line = el('span', 'review-name'); line.append(named, earned); named = line; }
      who.append(named, el('span', 'review-date date', timeLabel(review.created_at)));
      by.append(avatarFor(author, review.user_id, 'avatar', review.author_avatar_url), who); card.append(by);
    }
    const main = el('div', 'review-main'); const proof = el('div', 'review-proof');
    const minutes = review.verified_playtime_minutes; const pct = review.verified_achievement_pct;
    const psn = review.verified_source === 'psn';
    if (minutes != null) proof.append(stamp(`${hours(minutes)} h verified`));
    if (minutes != null || pct != null) proof.append(el('span', 'platform-badge', psn ? 'PlayStation' : 'PC'));
    if (pct != null) proof.append(numbered('span', 'review-extra', `${Math.round(pct)}% ${psn ? 'trophies' : 'achievements'}`));
    if (minutes == null && pct == null) proof.append(el('span', 'review-extra', 'No verified play data'));
    const stars = el('span', 'review-rating'); stars.append(starDisplay(review.rating));
    stars.setAttribute('role', 'img'); stars.setAttribute('aria-label', `${review.rating} out of 5 stars`);
    proof.append(stars); main.append(proof);
    if (context) main.append(hoursBar(context));
    if (review.body) main.append(el('p', 'review-body', review.body));
    card.append(main);
    const comments = el('div', 'comments'); comments.hidden = true;
    const toggle = button('Show comments', 'text-button', () => {
      comments.hidden = !comments.hidden; toggle.textContent = comments.hidden ? 'Show comments' : 'Hide comments'; toggle.setAttribute('aria-expanded', String(!comments.hidden));
      if (!comments.hidden && !comments.dataset.loaded) void loadComments();
    }); toggle.setAttribute('aria-expanded', 'false');
    const list = el('div', 'comment-list'); comments.append(list); let offset = 0; let loading = false; let loadVersion = 0;
    const error = el('p', 'form-error'); error.setAttribute('role', 'alert'); error.hidden = true; comments.append(error);
    const more = button('More comments', 'text-button', () => void loadComments()); more.hidden = true; comments.append(more);
    async function loadComments(refresh = false) {
      if (loading && !refresh) return;
      const version = ++loadVersion;
      const pageOffset = refresh ? 0 : offset;
      loading = true; more.disabled = true; error.hidden = true;
      try {
        const rows = await api(`/reviews/${review.id}/comments?limit=20&offset=${pageOffset}`);
        if (!card.isConnected || version !== loadVersion) return;
        if (!pageOffset) list.replaceChildren();
        for (const row of rows) {
          const comment = el('article', 'comment'); comment.append(authorLink(row.author_name || `Player ${row.user_id}`, row.user_id, 'comment-author'),
            el('span', 'review-date', timeLabel(row.created_at)), el('p', '', row.body));
          if (state.user && state.user.id !== row.user_id) comment.append(reportControl(api, 'comment', row.id));
          list.append(comment);
        }
        if (!pageOffset && !rows.length) list.append(el('p', 'helper', 'No comments yet.'));
        offset = pageOffset + rows.length; more.hidden = rows.length < 20; more.textContent = 'More comments'; comments.dataset.loaded = 'true';
      } catch (failure) { if (version === loadVersion) { formError(error, failure); more.hidden = false; more.textContent = 'Retry comments'; } }
      finally { if (version === loadVersion) { loading = false; more.disabled = false; } }
    }
    if (state.user) {
      const form = el('form', 'comment-form'); const label = el('label', '', 'Add a public comment');
      const input = el('textarea'); input.required = true; input.maxLength = 5000; input.rows = 2; label.append(input);
      const submit = el('button', 'button secondary', 'Post comment'); submit.type = 'submit'; form.append(label, submit); comments.append(form);
      form.addEventListener('submit', async (event) => {
        event.preventDefault(); if (!input.value.trim()) { error.hidden = false; error.textContent = 'Write a comment first.'; return; }
        submit.disabled = true; error.hidden = true;
        try {
          const row = await api(`/reviews/${review.id}/comments`, { method: 'POST', body: JSON.stringify({ body: input.value.trim() }) }); if (!card.isConnected) return;
          card.dispatchEvent(new CustomEvent('comment-published', { bubbles: true }));
          input.value = ''; form.querySelector('.comment-posted')?.remove(); form.append(el('p', 'comment-posted', 'Comment posted.'));
          await loadComments(true);
        } catch (failure) { formError(error, failure); } finally { submit.disabled = false; }
      });
    }
    const threadLink = el('a', 'text-button thread-link', 'Open discussion');
    threadLink.href = `/app#review=${review.id}`;
    threadLink.addEventListener('click', (event) => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); history.replaceState(null, '', threadLink.href);
      void openThread(review.id).catch(report);
    });
    const copy = button('Copy link', 'text-button thread-link', async () => {
      copy.textContent = await copyText(`${location.origin}/app#review=${review.id}`) ? 'Link copied' : 'Use the discussion link to share';
    });
    const actions = el('div', 'review-actions');
    if (like) actions.append(likeButton(review, { api, liked: state.liked || new Set(), signedIn: () => Boolean(state.user), report }));
    actions.append(toggle, threadLink, copy);
    if (state.user && state.user.id !== review.user_id) actions.append(reportControl(api, 'review', review.id));
    main.append(actions, comments);
    if (expandComments) queueMicrotask(() => { if (card.isConnected) toggle.click(); });
    return card;
  }
  async function openThread(id) {
    const request = ++requestId;
    const item = await api(`/reviews/${id}`);
    if (request === requestId) await open(item.game, null, item.review);
  }
  return { open, openThread, reviewCard, clear() { requestId += 1; content.replaceChildren(); dialog.close(); } };
}
