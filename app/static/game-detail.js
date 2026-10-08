import { genresFor, hours, integer, achievementPercent, steamArt, isPlayStationGame, playtimeLabel, reviewVerification, storeArt, TROPHY_GRADES } from './library.js';
import { $, el, button, cover, steamLink, timeLabel, formError, numbered } from './dom.js';
import { createRatingPicker, starDisplay } from './rating.js';

export function createGameDialog(state, api, report = () => {}) {
  let requestId = 0; let pageTitle = document.title;
  const dialog = $('#game-dialog'); const content = $('#detail-content');
  $('#close-dialog').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { requestId += 1; dialog.style.removeProperty('--tint'); content.replaceChildren(); document.title = pageTitle; });
  async function open(game, createdReview = null, threadReview = null, editRequested = false) {
    const request = ++requestId; content.replaceChildren(); dialog.style.removeProperty('--tint');
    if (!dialog.open) pageTitle = document.title;
    document.title = `${game.name} | PlayGraph`;
    const entry = state.library.find((row) => row.game.id === game.id);
    // PlayStation numbers live on their own entry and are shown on their own
    // lines; they are never added to Steam hours or achievements.
    const psnEntry = (state.psnLibrary || []).find((row) => row.game.id === game.id);
    const playstation = isPlayStationGame(game);
    const profile = el('div', 'game-profile');
    const aside = el('aside', 'game-profile-aside'); aside.append(cover(game));
    const main = el('div', 'game-profile-main');
    const header = el('header', 'detail-header');
    const software = game.content_kind === 'software';
    const info = el('div', 'detail-info');
    const title = el('h2', '', game.name); title.id = 'detail-title';
    info.append(el('p', 'detail-kicker', [software ? 'Software' : 'Game', playstation ? 'PlayStation' : 'Steam'].join(' · ')), title);
    // Your numbers sit under the art: hours and achievements when the game is
    // in your library, otherwise a plain note.
    const stats = el('dl', 'detail-stats');
    if (entry) {
      const pct = achievementPercent(entry);
      for (const [label, value] of [[software ? 'Time used' : 'Played', `${hours(entry.playtime_minutes)} h`],
        ['Achievements', pct == null ? 'None' : `${integer(entry.achievements_unlocked)} / ${integer(entry.achievements_total)}`]]) {
        const cell = el('div'); cell.append(el('dt', '', label), numbered('dd', entry.playtime_minutes > 0 && label !== 'Achievements' ? 'played' : '', value)); stats.append(cell);
      }
    }
    if (psnEntry) {
      // The server sums the grades into achievements_unlocked/total for PSN rows.
      const trophyCount = psnEntry.trophies
        ? `${integer(psnEntry.achievements_unlocked)} / ${integer(psnEntry.achievements_total)}` : 'None';
      for (const [label, value, played] of [['PlayStation hours', playtimeLabel(psnEntry), psnEntry.playtime_minutes > 0],
        ['Trophies', trophyCount, false]]) {
        const cell = el('div'); cell.append(el('dt', '', label), numbered('dd', played ? 'played' : '', value)); stats.append(cell);
      }
    }
    if (!entry && !psnEntry) {
      const cell = el('div'); cell.append(el('dt', '', 'Your library'), el('dd', '', !state.user ? 'Sign in to see your hours'
        : playstation ? 'Not in your PlayStation library' : 'Not in your Steam library')); stats.append(cell);
    }
    aside.append(stats);
    if (psnEntry?.trophies) aside.append(trophyGrades(psnEntry.trophies));
    // Reference sheet: label column, value column, hairline rows.
    const fields = el('dl', 'metadata-table');
    const platforms = el('ul', 'platform-badges'); platforms.append(el('li', 'platform-badge', playstation ? 'PlayStation' : 'PC'));
    const rows = [['Genres', genresFor(game).join(', ') || 'Not listed'], ['Category', software ? 'Software' : 'Game'],
      ['Platforms', platforms], ['Source', playstation ? 'Public PSN profile' : 'Steam']];
    if (!playstation) rows.push(['Steam app ID', String(game.steam_appid)]);
    for (const [name, value] of rows) {
      const dd = el('dd', name === 'Steam app ID' ? 'num' : ''); dd.append(value); fields.append(el('dt', '', name), dd);
    }
    info.append(fields);
    const about = el('div', 'detail-about'); info.append(about);
    if (entry) { const synced = el('p', 'helper', 'Your numbers are from your Steam sync on '); synced.append(el('span', 'num', timeLabel(entry.captured_at))); info.append(synced); }
    if (psnEntry) {
      const synced = el('p', 'helper', 'Your PlayStation numbers are from your public PSN profile, synced on ');
      synced.append(el('span', 'num', timeLabel(psnEntry.captured_at)), '. Hours appear only for PS4 and PS5 games when your privacy settings share them.');
      info.append(synced);
    }
    if (!entry && !psnEntry) info.append(el('p', 'helper', playstation
      ? 'Imported from a public PSN profile. PlayStation games are kept separate from Steam games, even with the same name.'
      : 'Imported from Steam.'));
    // Summary, release date and platforms come from IGDB on the single-game
    // endpoint; list responses leave them out. The page works without them.
    void (async () => {
      let details;
      try { details = await api(`/games/${game.id}`); } catch { return; }
      if (request !== requestId) return;
      if (details.platforms?.length) {
        platforms.replaceChildren(...details.platforms.map((p) => el('li', 'platform-badge', p.abbreviation || p.name)));
      }
      if (details.first_release_date) {
        const released = new Date(details.first_release_date).toLocaleDateString(undefined,
          { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' });
        fields.append(el('dt', '', 'Released'), el('dd', 'num', released));
      }
      if (details.summary) about.append(el('p', 'detail-summary', details.summary));
      if (details.summary || details.platforms?.length || details.first_release_date) {
        const credit = el('a', 'text-button detail-credit', 'Game details from IGDB ↗');
        credit.href = 'https://www.igdb.com/'; credit.target = '_blank'; credit.rel = 'noopener noreferrer';
        about.append(credit);
      }
    })();
    header.append(info); main.append(header); profile.append(aside, main);
    content.append(heroBand(game), profile); tint(game, request);
    const writeSection = el('section', 'write-review');
    if (createdReview && state.user) showOwnReview(createdReview, editRequested);
    else if (state.user) writeSection.append(el('p', 'helper', 'Checking for your review…'));
    else writeSection.append(el('h3', '', 'Sign in to review this game'), el('p', 'muted', 'Your rating and review go here. Connect Steam and it shows your hours too.'), steamLink());
    const section = el('section', 'reviews-section');
    if (threadReview) section.append(el('h3', '', 'Review discussion'));
    section.append(el('p', 'helper', threadReview ? 'Comments on this review are public.' : 'Most verified playtime first. Review stats reflect the moment of posting.'));
    if (threadReview) section.append(button('← All reviews', 'text-button', () => open(game)));
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
    main.append(tabs, ...panels); selectTab(createdReview ? 1 : 0);
    const actions = el('div', 'detail-actions');
    actions.append(button(state.user ? 'Your review' : 'Write a review', 'button primary', () => { selectTab(1); controls[1].focus(); }));
    let store = null;
    if (!playstation) {
      store = el('a', 'text-button', 'View on Steam ↗'); store.href = `https://store.steampowered.com/app/${Number(game.steam_appid)}/`;
      store.target = '_blank'; store.rel = 'noopener noreferrer';
    }
    const share = button('Copy game link', 'text-button', async () => {
      try { await navigator.clipboard.writeText(`${location.origin}/app#game=${game.id}`); share.textContent = 'Link copied'; }
      catch { report(new Error('Could not copy the link. Try again with clipboard access enabled.')); }
    });
    const links = el('div', 'detail-links'); if (store) links.append(store); links.append(share);
    aside.append(actions, links);
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
    let offset = 0;
    const error = el('p', 'form-error'); error.setAttribute('role', 'alert'); error.hidden = true; section.append(error);
    const more = button('More reviews', 'button secondary', () => void loadReviews()); more.hidden = true; section.append(more);
    async function loadReviews() {
      more.disabled = true; error.hidden = true;
      try {
        const reviews = await api(`/games/${game.id}/reviews?limit=20&offset=${offset}`);
        if (request !== requestId) return;
        if (!offset) list.replaceChildren();
        for (const review of reviews) list.append(reviewCard(review));
        if (!offset && !reviews.length) list.append(el('p', 'review-empty', 'No reviews yet.'));
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
    if (!dialog.open) dialog.showModal(); dialog.scrollTop = 0; $('#close-dialog').focus();
    if (threadReview) list.replaceChildren(reviewCard(threadReview, true));
    await Promise.all([threadReview ? Promise.resolve() : loadReviews(), state.user && !createdReview ? loadOwnReview() : Promise.resolve()]);
  }
  // Earned of total for each PlayStation trophy grade, best first.
  function trophyGrades(trophies) {
    const list = el('dl', 'trophy-grades'); list.setAttribute('aria-label', 'PlayStation trophies by grade');
    for (const grade of TROPHY_GRADES) {
      const cell = el('div'); cell.append(el('dt', '', grade[0].toLocaleUpperCase() + grade.slice(1)),
        numbered('dd', '', `${integer(trophies.earned[grade])} / ${integer(trophies.total[grade])}`));
      list.append(cell);
    }
    if (trophies.progress != null) {
      const cell = el('div'); cell.append(el('dt', '', 'Progress'), numbered('dd', '', `${trophies.progress}%`)); list.append(cell);
    }
    return list;
  }
  // Per-game color: average the cover (Steam's CDN allows CORS) on an 8x12 canvas,
  // darken it so it never goes bright, and expose it as --tint. Failure leaves CSS fallbacks.
  function tint(game, request) {
    // Steam's and IGDB's CDNs allow CORS; Sony's doesn't, so its art can't be read.
    const source = isPlayStationGame(game) ? game.cover_url : steamArt(game.steam_appid, 'library_600x900.jpg');
    if (!source) return;
    const img = new Image(); img.crossOrigin = 'anonymous';
    img.onload = () => {
      if (request !== requestId) return;
      try {
        const canvas = document.createElement('canvas'); canvas.width = 8; canvas.height = 12;
        const ctx = canvas.getContext('2d'); ctx.drawImage(img, 0, 0, 8, 12);
        // Weight each pixel by its saturation, so the cover's real color wins
        // over the greys and blacks most box art is mostly made of.
        const px = ctx.getImageData(0, 0, 8, 12).data; const sum = [0, 0, 0]; let total = 0;
        for (let i = 0; i < px.length; i += 4) {
          const weight = Math.max(px[i], px[i + 1], px[i + 2]) - Math.min(px[i], px[i + 1], px[i + 2]) + 8;
          for (let c = 0; c < 3; c++) sum[c] += px[i + c] * weight; total += weight;
        }
        const [r, g, b] = sum.map((v) => Math.round(v / total * 0.55));
        dialog.style.setProperty('--tint', `rgb(${r},${g},${b})`);
      } catch { /* tainted canvas: keep the CSS fallback */ }
    };
    img.src = source;
  }
  // Backdrop band behind the cover and title. Wide hero art first, then the
  // store header blurred hard (it is too small to show sharp at this width),
  // then nothing: the CSS gradient underneath carries it. Games without Steam
  // art use IGDB artwork, or their cover blurred.
  function heroBand(game) {
    const band = el('div', 'detail-hero'); band.setAttribute('aria-hidden', 'true');
    const img = el('img'); img.alt = ''; img.decoding = 'async'; img.referrerPolicy = 'no-referrer';
    if (isPlayStationGame(game)) {
      const art = storeArt(game);
      if (!art.hero) return band;
      band.classList.toggle('soft', art.soft); img.addEventListener('error', () => img.remove());
      img.src = art.hero; band.append(img); return band;
    }
    let step = 0; const files = ['library_hero.jpg', 'header.jpg'];
    const load = () => { img.src = steamArt(game.steam_appid, files[step]); };
    img.addEventListener('error', () => {
      step += 1;
      if (step < files.length) { band.classList.add('soft'); load(); } else img.remove();
    });
    load(); band.append(img); return band;
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
  function reviewCard(review, expandComments = false) {
    // Avatar in the first column; everything else in the second. One meta
    // line carries who, the stars, the platform, verified hours and the date.
    const card = el('article', 'review-card'); const author = review.author_name || `Player ${review.user_id}`;
    const main = el('div', 'review-main'); const byline = el('div', 'review-byline');
    const stars = el('span', 'review-rating'); stars.append(starDisplay(review.rating));
    stars.setAttribute('role', 'img'); stars.setAttribute('aria-label', `${review.rating} out of 5 stars`);
    byline.append(el('strong', 'review-author', author), stars);
    const verified = reviewVerification(review);
    if (!verified) byline.append(el('span', 'verified-label unverified', 'No verified play data'));
    else byline.append(el('span', 'platform-badge', verified.badge), numbered('span', 'verified-label', verified.text));
    byline.append(el('span', 'review-date', timeLabel(review.created_at)));
    main.append(byline); if (review.body) main.append(el('p', 'review-body', review.body));
    card.append(el('span', 'avatar', author.slice(0, 1).toLocaleUpperCase()), main);
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
          const comment = el('article', 'comment'); comment.append(el('strong', '', row.author_name || `Player ${row.user_id}`),
            el('span', 'review-date', timeLabel(row.created_at)), el('p', '', row.body)); list.append(comment);
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
      try { await navigator.clipboard.writeText(`${location.origin}/app#review=${review.id}`); copy.textContent = 'Link copied'; }
      catch { copy.textContent = 'Use the discussion link to share'; }
    });
    const actions = el('div', 'review-actions'); actions.append(toggle, threadLink, copy);
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
