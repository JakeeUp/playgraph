import { genresFor, hours, integer, achievementPercent } from './library.js';
import { $, el, button, cover, steamLink, timeLabel, formError } from './dom.js';
import { createRatingPicker, starDisplay } from './rating.js';

export function createGameDialog(state, api, report = () => {}) {
  let requestId = 0;
  const dialog = $('#game-dialog'); const content = $('#detail-content');
  $('#close-dialog').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { requestId += 1; content.replaceChildren(); });
  async function open(game, createdReview = null, threadReview = null, editRequested = false) {
    const request = ++requestId; content.replaceChildren();
    const entry = state.library.find((row) => row.game.id === game.id);
    const profile = el('div', 'game-profile');
    const aside = el('aside', 'game-profile-aside'); aside.append(cover(game));
    const main = el('div', 'game-profile-main');
    const header = el('header', 'detail-header');
    const software = game.content_kind === 'software';
    const info = el('div', 'detail-info'); info.append(el('p', 'detail-kind', software ? 'Software · Steam' : 'Game · Steam'));
    const title = el('h2', '', game.name); title.id = 'detail-title';
    info.append(title, el('p', 'detail-genres', genresFor(game).join(' · ') || 'Genres not available'));
    if (entry) {
      const data = el('div', 'detail-stats'); data.append(el('strong', '', `${hours(entry.playtime_minutes)} h`), el('span', '', software ? 'Recorded Steam usage' : 'Your Steam playtime'));
      if (achievementPercent(entry) != null) data.append(el('strong', '', `${integer(entry.achievements_unlocked)} / ${integer(entry.achievements_total)}`), el('span', '', 'Achievements unlocked'));
      info.append(data, el('p', 'helper', `Library captured ${timeLabel(entry.captured_at)}`));
    }
    const store = el('a', 'text-button', 'View on Steam ↗'); store.href = `https://store.steampowered.com/app/${Number(game.steam_appid)}/`;
    store.target = '_blank'; store.rel = 'noopener noreferrer'; aside.append(store);
    const share = button('Copy game link', 'text-button', async () => {
      try { await navigator.clipboard.writeText(`${location.origin}/app#game=${game.id}`); share.textContent = 'Game link copied'; }
      catch { report(new Error('Could not copy the link. Try again with clipboard access enabled.')); }
    }); aside.append(share); header.append(info); main.append(header); profile.append(aside, main); content.append(profile);
    const writeSection = el('section', 'write-review');
    if (createdReview && state.user) showOwnReview(createdReview, editRequested);
    else if (state.user) writeSection.append(el('p', 'helper', 'Checking your review...'));
    else writeSection.append(el('h3', '', 'Your point of view belongs here.'), el('p', 'muted', 'Connect Steam to rate this game and join the conversation.'), steamLink());
    const section = el('section', 'reviews-section');
    section.append(el('h3', '', threadReview ? 'Review discussion' : software ? 'Software reviews' : 'Player reviews'),
      el('p', 'helper', threadReview ? 'A public conversation about this review.' : 'Most verified playtime first. Review stats reflect the moment of posting.'));
    if (threadReview) section.append(button('← All reviews for this title', 'text-button', () => open(game)));
    const list = el('div', 'reviews-list'); list.append(el('p', 'helper', 'Loading reviews...')); section.append(list);
    const metadata = el('section', 'game-metadata'); metadata.append(el('h3', '', 'Catalog details'));
    const fields = el('dl', 'metadata-table');
    for (const [name, value] of [['Source', 'Steam'], ['Category', software ? 'Software' : 'Game'], ['Genres', genresFor(game).join(', ') || 'Not available'], ['Steam app ID', String(game.steam_appid)], ['Artwork', 'Steam']]) {
      fields.append(el('dt', '', name), el('dd', '', value));
    }
    metadata.append(fields, el('p', 'helper', 'Imported from Steam. Release dates, developers and other platforms are not in this catalog yet.'));
    const tabs = el('div', 'detail-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Game information');
    const panels = [section, writeSection, metadata]; const labels = ['Community reviews', 'Your review', 'Details'];
    const controls = labels.map((label, index) => {
      const control = button(label, 'detail-tab', () => selectTab(index));
      control.id = `detail-tab-${index}`; control.setAttribute('role', 'tab'); control.setAttribute('aria-controls', `detail-panel-${index}`);
      panels[index].id = `detail-panel-${index}`; panels[index].setAttribute('role', 'tabpanel'); panels[index].setAttribute('aria-labelledby', control.id); panels[index].tabIndex = 0;
      control.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3;
        selectTab(next); controls[next].focus();
      });
      tabs.append(control); return control;
    });
    function selectTab(selected) { panels.forEach((panel, index) => { panel.hidden = index !== selected; controls[index].setAttribute('aria-selected', String(index === selected)); controls[index].tabIndex = index === selected ? 0 : -1; }); }
    main.append(tabs, ...panels); selectTab(createdReview ? 1 : 0);
    aside.append(button(state.user ? 'Write / edit review' : 'Write a review', 'button secondary', () => { selectTab(1); controls[1].focus(); }));
    function showOwnReview(own, editing = false) {
      writeSection.replaceChildren();
      if (editing) {
        const form = reviewForm(game, request, own, () => { showOwnReview(own); writeSection.querySelector('button.button').focus(); });
        writeSection.append(form); form.querySelector('input').focus(); return;
      }
      writeSection.append(el('h3', '', 'Your published review'), reviewCard(own),
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
        if (!offset && !reviews.length) list.append(el('p', 'review-empty', 'No reviews yet. Be the first to leave your mark.'));
        offset += reviews.length; more.hidden = reviews.length < 20; more.textContent = 'More reviews';
      } catch (failure) {
        if (request === requestId) { if (!offset) list.replaceChildren(); formError(error, failure); more.hidden = false; more.textContent = 'Retry loading reviews'; }
      } finally { more.disabled = false; }
    }
    async function loadOwnReview() {
      writeSection.replaceChildren(el('p', 'helper', 'Checking your review...'));
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
  function reviewForm(game, request, existing = null, cancel = null) {
    const form = el('form', 'review-form'); form.append(el('h3', '', existing ? 'Edit your review' : 'What did you think?'));
    const rating = createRatingPicker(existing?.rating ?? 0);
    const bodyLabel = el('label', '', 'Your review'); const body = el('textarea'); body.name = 'body'; body.rows = 4; body.maxLength = 10000;
    body.placeholder = 'What stayed with you? What would you tell someone about to play?'; bodyLabel.append(body);
    body.value = existing?.body || '';
    const disclosure = el('p', 'review-disclosure', existing ? 'Your changes are public. The original posting date and verified play stats will stay the same.' : 'Public review: your Steam display name, rating, text, and available verified playtime and achievement percentage will be visible to everyone. Stats are saved as they are now.');
    const error = el('p', 'form-error'); error.setAttribute('role', 'alert'); error.hidden = true;
    const submit = el('button', 'button primary', existing ? 'Save changes' : 'Publish review ↗'); submit.type = 'submit';
    form.append(rating.element, bodyLabel, disclosure, error, submit);
    const cancelButton = cancel ? button('Cancel editing', 'text-button', cancel) : null;
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
    const card = el('article', 'review-card'); const byline = el('div', 'review-byline'); const author = review.author_name || `Player ${review.user_id}`;
    byline.append(el('span', 'avatar', author.slice(0, 1).toLocaleUpperCase()), el('strong', '', author), el('span', 'review-date', timeLabel(review.created_at)));
    const stars = el('span', 'review-rating'); stars.append(starDisplay(review.rating)); stars.setAttribute('aria-label', `${review.rating} out of 5 stars`);
    byline.append(stars); card.append(byline);
    const verified = review.verified_playtime_minutes == null ? 'No verified play data'
      : `${hours(review.verified_playtime_minutes)} h verified on Steam${review.verified_achievement_pct == null ? '' : ` · ${Math.round(review.verified_achievement_pct)}% achievements`}`;
    card.append(el('p', review.verified_playtime_minutes == null ? 'verified-label unverified' : 'verified-label', verified)); if (review.body) card.append(el('p', 'review-body', review.body));
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
        if (!pageOffset && !rows.length) list.append(el('p', 'helper', 'Start the conversation.'));
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
          input.value = ''; form.querySelector('.comment-posted')?.remove(); form.append(el('p', 'comment-posted', `Your comment was posted: ${row.body}`));
          await loadComments(true);
        } catch (failure) { formError(error, failure); } finally { submit.disabled = false; }
      });
    }
    const threadLink = el('a', 'text-button thread-link', 'Open discussion ↗');
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
    card.append(actions, comments);
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
