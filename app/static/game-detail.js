import { genresFor, hours, integer, achievementPercent } from './library.js';
import { $, el, button, cover, steamLink, timeLabel, formError } from './dom.js';
import { createRatingPicker, starDisplay } from './rating.js';

export function createGameDialog(state, api, report = () => {}) {
  let requestId = 0;
  const dialog = $('#game-dialog'); const content = $('#detail-content');
  $('#close-dialog').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { requestId += 1; content.replaceChildren(); });
  async function open(game, createdReview = null, threadReview = null) {
    const request = ++requestId; content.replaceChildren();
    const entry = state.library.find((row) => row.game.id === game.id);
    const header = el('div', 'detail-header'); header.append(cover(game));
    const software = game.content_kind === 'software';
    const info = el('div', 'detail-info'); info.append(el('p', 'eyebrow', software ? 'SOFTWARE ON PLAYGRAPH' : 'IN THE PLAYGRAPH CATALOG'));
    const title = el('h2', '', game.name); title.id = 'detail-title';
    info.append(title, el('p', 'detail-genres', genresFor(game).join(' · ') || 'Steam game'));
    if (entry) {
      const data = el('div', 'detail-stats'); data.append(el('strong', '', `${hours(entry.playtime_minutes)} h`), el('span', '', software ? 'Recorded Steam usage' : 'Your Steam playtime'));
      if (achievementPercent(entry) != null) data.append(el('strong', '', `${integer(entry.achievements_unlocked)} / ${integer(entry.achievements_total)}`), el('span', '', 'Achievements unlocked'));
      info.append(data, el('p', 'helper', `Library captured ${timeLabel(entry.captured_at)}`));
    }
    const store = el('a', 'text-button', 'View on Steam ↗'); store.href = `https://store.steampowered.com/app/${Number(game.steam_appid)}/`;
    store.target = '_blank'; store.rel = 'noopener noreferrer'; info.append(store); header.append(info); content.append(header);
    const writeSection = el('section', 'write-review');
    if (createdReview) {
      writeSection.append(el('p', 'reviewed-note', '✓ Your review is published.'), reviewCard(createdReview));
    } else if (state.user) writeSection.append(el('p', 'helper', 'Checking your review...'));
    else writeSection.append(el('h3', '', 'Your point of view belongs here.'), el('p', 'muted', 'Connect Steam to rate this game and join the conversation.'), steamLink());
    if (!threadReview) content.append(writeSection);
    const section = el('section', 'reviews-section');
    section.append(el('h3', '', threadReview ? 'Review discussion' : software ? 'Software reviews' : 'Player reviews'),
      el('p', 'helper', threadReview ? 'A public conversation about this review.' : 'Most verified playtime first. Review stats reflect the moment of posting.'));
    if (threadReview) section.append(button('← All reviews for this title', 'text-button', () => open(game)));
    const list = el('div', 'reviews-list'); list.append(el('p', 'helper', 'Loading reviews...')); section.append(list); content.append(section);
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
        if (own) writeSection.append(el('p', 'reviewed-note', '✓ You have reviewed this game.'), reviewCard(own));
        else writeSection.append(reviewForm(game, request));
      } catch (failure) {
        if (request !== requestId) return;
        const message = el('p', 'form-error'); message.setAttribute('role', 'alert'); formError(message, failure);
        writeSection.replaceChildren(message, button('Retry checking your review', 'button secondary', () => void loadOwnReview()));
      }
    }
    if (!dialog.open) dialog.showModal(); dialog.scrollTop = 0; $('#close-dialog').focus();
    if (threadReview) list.replaceChildren(reviewCard(threadReview, true));
    else await Promise.all([loadReviews(), state.user && !createdReview ? loadOwnReview() : Promise.resolve()]);
  }
  function reviewForm(game, request) {
    const form = el('form', 'review-form'); form.append(el('h3', '', 'What did you think?'));
    const rating = createRatingPicker();
    const bodyLabel = el('label', '', 'Your review'); const body = el('textarea'); body.name = 'body'; body.rows = 4; body.maxLength = 10000;
    body.placeholder = 'What stayed with you? What would you tell someone about to play?'; bodyLabel.append(body);
    const disclosure = el('p', 'review-disclosure', 'Public review: your Steam display name, rating, text, and available verified playtime and achievement percentage will be visible to everyone. Stats are saved as they are now.');
    const error = el('p', 'form-error'); error.setAttribute('role', 'alert'); error.hidden = true;
    const submit = el('button', 'button primary', 'Publish review ↗'); submit.type = 'submit';
    form.append(rating.element, bodyLabel, disclosure, error, submit);
    form.addEventListener('submit', async (event) => {
      event.preventDefault(); error.hidden = true;
      if (rating.value() < 0.5) { error.textContent = 'Choose at least half a star before publishing.'; error.hidden = false; rating.input.focus(); return; }
      submit.disabled = true;
      try {
        const created = await api(`/games/${game.id}/reviews`, { method: 'POST', body: JSON.stringify({ rating: rating.value(), body: body.value.trim() || null }) });
        if (request === requestId) await open(game, created);
      } catch (failure) { formError(error, failure); } finally { submit.disabled = false; }
    }); return form;
  }
  function reviewCard(review, expandComments = false) {
    const card = el('article', 'review-card'); const byline = el('div', 'review-byline'); const author = review.author_name || `Player ${review.user_id}`;
    byline.append(el('span', 'avatar', author.slice(0, 1).toLocaleUpperCase()), el('strong', '', author), el('span', 'review-date', timeLabel(review.created_at)));
    const stars = el('span', 'review-rating'); stars.append(starDisplay(review.rating)); stars.setAttribute('aria-label', `${review.rating} out of 5 stars`);
    byline.append(stars); card.append(byline);
    const verified = review.verified_playtime_minutes == null ? 'No verified play data'
      : `${hours(review.verified_playtime_minutes)} h verified on Steam${review.verified_achievement_pct == null ? '' : ` · ${Math.round(review.verified_achievement_pct)}% achievements`}`;
    card.append(el('p', 'verified-label', verified)); if (review.body) card.append(el('p', 'review-body', review.body));
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
    card.append(toggle, threadLink, copy, comments);
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
