// Reviews as social posts, X / Bluesky style: the action bar under every
// review (reply, repost, like, bookmark, share) and the counts in it.
// The pure helpers at the top have no DOM and are unit tested in node.
import { el, button, cover, heroArt, timeLabel, copyText } from './dom.js';
import { profileHref } from './social.js';
import { integer } from './library.js';

const units = [[1e9, 'B'], [1e6, 'M'], [1e3, 'K']];

/** 999, 1.2K, 12K, 3.4M: one decimal below ten, none above, never rounded up past the real value. */
export function compactCount(value) {
  const n = Math.max(0, Math.floor(Number(value) || 0));
  for (const [size, suffix] of units) {
    if (n < size) continue;
    const scaled = n / size; const digits = scaled < 10 ? 1 : 0;
    const floored = Math.floor(scaled * 10 ** digits) / 10 ** digits;
    return `${floored.toFixed(digits).replace(/\.0$/, '')}${suffix}`;
  }
  return String(n);
}

/** "@rosa" for a review's author, or '' while a player has no handle. */
export const handleText = (handle) => (handle ? `@${handle}` : '');

/**
 * A verified badge in words, for its label and tooltip. `hours` is the tier the
 * server worked out (100, 1000 or 5000); anything else means no badge and ''.
 */
export function badgeLabel(hours) {
  const tier = Number(hours);
  return [100, 1000, 5000].includes(tier) ? `Verified player: ${integer(tier)}+ verified hours on game reviews` : '';
}

/** The count after a like or repost is toggled, never below zero. */
export const nextLikeCount = (count, liked) => Math.max(0, (Number(count) || 0) + (liked ? 1 : -1));

export const actionCountLabel = (label, count, noun) => `${label}, ${compactCount(count)} ${noun}${count >= 1000 ? ` (${integer(count)} total)` : ''}`;

/** A quote with the same limits the server stores: collapsed whitespace, 280 characters, or '' for a plain repost. */
export function quoteText(value) {
  const text = String(value ?? '').replace(/[\u0000-\u001f\u007f-\u009f]/g, '').trim().replace(/\s+/g, ' ');
  return text ? text.slice(0, 280) : '';
}

/** Review ids from feed items or reviews, for one GET /me/likes call (the server takes 100). */
export const likeLookup = (reviews) => [...new Set(reviews.map((review) => review.id))].slice(0, 100).join(',');

// ---- DOM ----


const SVG = 'http://www.w3.org/2000/svg';
const ICONS = {
  reply: 'M4 5h16v11H9l-5 4z',
  repost: 'M7 7h11l-3-3M17 17H6l3 3M18 7v5M6 17v-5',
  like: 'M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z',
  bookmark: 'M7 4h10v16l-5-4-5 4z',
  share: 'M12 4v11M8 8l4-4 4 4M5 14v5h14v-5',
};

export function icon(name) {
  const svg = document.createElementNS(SVG, 'svg'); svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('post-icon');
  const path = document.createElementNS(SVG, 'path'); path.setAttribute('d', ICONS[name]); svg.append(path); return svg;
}

/**
 * The check beside a name, or null when the player has no badge. The tier is
 * in the label as well as the color, so it never depends on telling green from gold.
 */
export function badgeFor(hours) {
  const label = badgeLabel(hours); if (!label) return null;
  const mark = el('span', `verified-badge tier-${Number(hours)}`); mark.setAttribute('role', 'img');
  mark.setAttribute('aria-label', label); mark.title = label;
  const svg = document.createElementNS(SVG, 'svg'); svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
  const disc = document.createElementNS(SVG, 'circle'); disc.setAttribute('cx', '12'); disc.setAttribute('cy', '12'); disc.setAttribute('r', '10');
  const check = document.createElementNS(SVG, 'path'); check.setAttribute('d', 'M7.5 12.5l3 3 6-6.5');
  svg.append(disc, check); mark.append(svg); return mark;
}

function action(name, label, count = null) {
  const control = document.createElement('button'); control.type = 'button'; control.className = `post-action ${name}`;
  control.setAttribute('aria-label', label); control.title = label; control.append(icon(name));
  const number = document.createElement('span'); number.className = 'post-count num'; control.append(number);
  if (count != null) number.textContent = count > 0 ? compactCount(count) : '';
  return control;
}

/**
 * The heart. Fills at once, then asks the server; on failure it goes back and
 * reports why. `liked` is a Set of review ids the viewer has liked, shared by
 * every heart on the page so two cards for the same review agree.
 */
export function likeButton(review, { api, liked, signedIn, report = () => {} }) {
  const control = action('like', 'Like', review.like_count || 0); control.dataset.likeReview = String(review.id);
  let count = review.like_count || 0; let busy = false;
  const paint = () => {
    const on = liked.has(review.id);
    control.classList.toggle('on', on); control.setAttribute('aria-pressed', String(on));
    control.setAttribute('aria-label', actionCountLabel(on ? 'Unlike' : 'Like', count, count === 1 ? 'like' : 'likes'));
    control.querySelector('.post-count').textContent = count > 0 ? compactCount(count) : '';
  };
  control.addEventListener('likes-changed', paint);
  control.addEventListener('click', async () => {
    if (!signedIn()) { report(new Error('Sign in to like reviews.')); return; }
    if (busy) return; busy = true;
    const want = !liked.has(review.id);
    const before = count; count = nextLikeCount(count, want);
    if (want) liked.add(review.id); else liked.delete(review.id);
    paintAll(review.id, count);
    try {
      const state = await api(`/reviews/${review.id}/like`, { method: want ? 'POST' : 'DELETE' });
      count = state.like_count; if (state.liked) liked.add(review.id); else liked.delete(review.id);
    } catch (error) {
      count = before; if (want) liked.delete(review.id); else liked.add(review.id); report(error);
    } finally { busy = false; paintAll(review.id, count); }
  });
  control.syncCount = (value) => { count = value; paint(); };
  paint(); return control;
}

function paintAll(reviewId, count) {
  for (const control of document.querySelectorAll(`[data-like-review="${reviewId}"]`)) control.syncCount(count);
}

/** Ask once which of these reviews the viewer liked, then light those hearts. */
export async function loadLikes(api, liked, reviews) {
  const ids = likeLookup(reviews); if (!ids) return;
  const data = await api(`/me/likes?review_ids=${ids}`);
  for (const id of data?.liked || []) liked.add(id);
  for (const control of document.querySelectorAll('[data-like-review]')) control.dispatchEvent(new Event('likes-changed'));
}

function paintCount(control, count) {
  const number = control.querySelector('.post-count');
  if (number) number.textContent = count > 0 ? compactCount(count) : '';
}

function paintMarked(selector, reviewId, count) {
  for (const control of document.querySelectorAll(`[${selector}="${reviewId}"]`)) control.syncCount(count);
}

/** Ask once which of these reviews the viewer plain-reposted, then light those buttons. */
export async function loadReposts(api, reposted, reviews) {
  const ids = likeLookup(reviews); if (!ids) return;
  const data = await api(`/me/reposts?review_ids=${ids}`);
  for (const id of data?.reposted || []) reposted.add(id);
  for (const control of document.querySelectorAll('[data-repost-review]')) control.dispatchEvent(new Event('reposts-changed'));
}

/** Ask once which of these reviews the viewer saved. Bookmarks are never counted in public. */
export async function loadBookmarks(api, bookmarked, reviews) {
  const ids = likeLookup(reviews); if (!ids) return;
  const data = await api(`/me/bookmarks?review_ids=${ids}`);
  for (const id of data?.bookmarked || []) bookmarked.add(id);
  for (const control of document.querySelectorAll('[data-bookmark-review]')) control.dispatchEvent(new Event('bookmarks-changed'));
}

function closeMenus() {
  for (const menu of document.querySelectorAll('.post-menu')) menu.hidden = true;
}
if (typeof document !== 'undefined' && document.documentElement && !document.documentElement.dataset.postMenus) {
  document.documentElement.dataset.postMenus = '1';
  document.addEventListener('click', closeMenus);
}

/**
 * Repost opens a small menu: plain repost (one per review) or a quote.
 * The count covers both. `reposted` is only the plain ones, shared by every card.
 */
function repostControl(review, { api, reposted, signedIn, report }) {
  const wrap = el('span', 'post-repost');
  const control = action('repost', 'Repost', review.repost_count || 0);
  control.dataset.repostReview = String(review.id);
  control.setAttribute('aria-haspopup', 'menu'); control.setAttribute('aria-expanded', 'false');
  let count = review.repost_count || 0; let busy = false;
  const menu = el('div', 'post-menu'); menu.hidden = true; menu.setAttribute('role', 'menu');
  const plain = button('', 'post-menu-item', () => {}); plain.setAttribute('role', 'menuitem');
  const quoteOpen = button('Quote', 'post-menu-item', () => { composer.hidden = false; field.focus(); });
  quoteOpen.setAttribute('role', 'menuitem');
  const composer = el('form', 'quote-form'); composer.hidden = true;
  const field = el('textarea'); field.maxLength = 280; field.rows = 3; field.required = true;
  field.setAttribute('aria-label', 'Quote this review');
  const left = el('span', 'helper'); const submit = el('button', 'button secondary', 'Post quote'); submit.type = 'submit';
  composer.append(field, left, submit);
  menu.append(plain, quoteOpen, composer);
  const paint = () => {
    const on = reposted.has(review.id);
    control.classList.toggle('on', on); control.setAttribute('aria-pressed', String(on));
    const noun = count === 1 ? 'repost' : 'reposts';
    control.setAttribute('aria-label', actionCountLabel(on ? 'Undo repost' : 'Repost', count, noun));
    paintCount(control, count); plain.textContent = on ? 'Undo repost' : 'Repost';
  };
  const showLeft = () => { const n = 280 - quoteText(field.value).length; left.textContent = `${n} characters left`; };
  field.addEventListener('input', showLeft); showLeft();
  control.addEventListener('reposts-changed', paint);
  control.addEventListener('click', (event) => {
    event.stopPropagation();
    if (!signedIn()) { report(new Error('Sign in to repost reviews.')); return; }
    const open = menu.hidden; closeMenus(); menu.hidden = !open; composer.hidden = true;
    control.setAttribute('aria-expanded', String(!menu.hidden));
  });
  menu.addEventListener('click', (event) => event.stopPropagation());
  plain.addEventListener('click', async () => {
    if (busy) return; busy = true; menu.hidden = true;
    const want = !reposted.has(review.id); const before = count;
    count = nextLikeCount(count, want);
    if (want) reposted.add(review.id); else reposted.delete(review.id);
    paintMarked('data-repost-review', review.id, count);
    try {
      const state = await api(`/reviews/${review.id}/repost`, { method: want ? 'POST' : 'DELETE', body: want ? '{}' : undefined });
      count = state.repost_count;
      if (state.reposted) reposted.add(review.id); else reposted.delete(review.id);
    } catch (error) {
      count = before; if (want) reposted.delete(review.id); else reposted.add(review.id); report(error);
    } finally { busy = false; paintMarked('data-repost-review', review.id, count); }
  });
  composer.addEventListener('submit', async (event) => {
    event.preventDefault();
    const text = quoteText(field.value);
    if (!text) { field.focus(); return; }
    if (busy) return; busy = true; submit.disabled = true;
    const before = count; count = nextLikeCount(count, true); paintMarked('data-repost-review', review.id, count);
    try {
      const state = await api(`/reviews/${review.id}/repost`, { method: 'POST', body: JSON.stringify({ quote: text }) });
      count = state.repost_count; field.value = ''; showLeft(); menu.hidden = true;
    } catch (error) {
      count = before; report(error);
    } finally { busy = false; submit.disabled = false; paintMarked('data-repost-review', review.id, count); }
  });
  control.syncCount = (value) => { count = value; paint(); };
  paint(); wrap.append(control, menu); return wrap;
}

/** A private save. No count: nobody else should be able to tell. */
function bookmarkControl(review, { api, bookmarked, signedIn, report }) {
  const control = action('bookmark', 'Bookmark'); control.dataset.bookmarkReview = String(review.id);
  let busy = false;
  const paint = () => {
    const on = bookmarked.has(review.id);
    control.classList.toggle('on', on); control.setAttribute('aria-pressed', String(on));
    control.setAttribute('aria-label', on ? 'Remove bookmark' : 'Bookmark');
  };
  control.addEventListener('bookmarks-changed', paint);
  control.addEventListener('click', async () => {
    if (!signedIn()) { report(new Error('Sign in to bookmark reviews.')); return; }
    if (busy) return; busy = true;
    const want = !bookmarked.has(review.id);
    if (want) bookmarked.add(review.id); else bookmarked.delete(review.id);
    paint();
    try {
      const state = await api(`/reviews/${review.id}/bookmark`, { method: want ? 'POST' : 'DELETE' });
      if (state.bookmarked) bookmarked.add(review.id); else bookmarked.delete(review.id);
    } catch (error) {
      if (want) bookmarked.delete(review.id); else bookmarked.add(review.id); report(error);
    } finally { busy = false; paint(); }
  });
  paint(); return control;
}

/**
 * The full bar: reply (with the comment count), repost, like, bookmark, share.
 */
export function actionBar(review, { api, liked, reposted, bookmarked, signedIn, report, onReply, replies = 0, onShare }) {
  const bar = document.createElement('div'); bar.className = 'post-actions';
  const reply = action('reply', `Reply, ${replies} ${replies === 1 ? 'comment' : 'comments'}`, replies);
  reply.addEventListener('click', onReply); reply.setAttribute('aria-expanded', 'false');
  const share = action('share', 'Copy link to this review'); share.addEventListener('click', onShare);
  bar.append(reply,
    repostControl(review, { api, reposted: reposted || new Set(), signedIn, report }),
    likeButton(review, { api, liked, signedIn, report }),
    bookmarkControl(review, { api, bookmarked: bookmarked || new Set(), signedIn, report }),
    share);
  bar.setReplies = (count) => {
    reply.querySelector('.post-count').textContent = count > 0 ? compactCount(count) : '';
    reply.setAttribute('aria-label', actionCountLabel('Reply', count, count === 1 ? 'comment' : 'comments'));
  };
  return bar;
}

/** A round initial with a color picked from the player's ID, the same everywhere. */
export function avatarFor(name, id, className = 'avatar', url = null) {
  const face = el('span', className, (name || '?').slice(0, 1).toLocaleUpperCase());
  face.setAttribute('aria-hidden', 'true');
  if (url) {
    const image = el('img'); image.alt = ''; image.src = url; image.referrerPolicy = 'no-referrer';
    image.addEventListener('error', () => image.remove(), { once: true }); face.append(image);
  }
  face.style.setProperty('--hue', String((Number(id) * 47) % 360)); return face;
}

/**
 * A review as a post, X / Bluesky style: avatar on the left; name, @handle and
 * time; "reviewed <game>"; the stars, verified stamp and text; the game's art
 * like an attached image; then reply, repost, like, bookmark and share.
 * `authorLink` is passed in so this module never imports the profile page.
 */
function personHead(name, id, handle, when, authorLink, badge) {
  const head = el('header', 'post-head');
  head.append(authorLink(name, id, 'post-name'));
  const mark = badgeFor(badge); if (mark) head.append(mark);
  if (handle) head.append(el('span', 'post-handle', handleText(handle)));
  const dot = el('span', 'post-dot', '·'); dot.setAttribute('aria-hidden', 'true');
  const time = el('time', 'post-time', timeLabel(when)); time.dateTime = when;
  head.append(dot, time);
  return head;
}

export function postCard(item, { gameDialog, api, state, report, authorLink, quoteReport }) {
  const { review, game } = item; const openGame = () => gameDialog.open(game);
  const quote = item.repost?.quote ? item.repost : null;
  const name = quote ? (quote.author_name || `Player ${quote.user_id}`) : (review.author_name || `Player ${review.user_id}`);
  const personId = quote ? quote.user_id : review.user_id;
  const card = el('article', quote ? 'post is-quote' : 'post');
  if (!quote && item.repost) {
    const banner = el('p', 'post-repost-banner');
    banner.append(icon('repost'), ' ', authorLink(item.repost.author_name || `Player ${item.repost.user_id}`, item.repost.user_id));
    const reposterBadge = badgeFor(item.repost.author_badge); if (reposterBadge) banner.append(reposterBadge);
    banner.append(' reposted');
    card.append(banner);
  } else if (item.reason) card.append(el('p', 'post-reason', item.reason));
  const avatar = el('a', 'post-avatar'); avatar.href = profileHref(personId); avatar.tabIndex = -1; avatar.setAttribute('aria-hidden', 'true');
  avatar.append(avatarFor(name, personId, 'avatar', quote ? quote.author_avatar_url : review.author_avatar_url));
  const main = el('div', 'post-main');
  const head = personHead(name, personId, quote ? quote.author_handle : review.author_handle, quote ? quote.created_at : review.created_at,
    authorLink, quote ? quote.author_badge : review.author_badge);
  const context = el('p', 'post-context'); context.append('reviewed ', button(game.name, 'title-link', openGame));
  const body = gameDialog.reviewCard(review, false, { byline: false, like: false });
  const media = button('', 'post-media', openGame); media.setAttribute('aria-label', `Open ${game.name}`);
  const caption = el('span', 'post-media-caption'); caption.append(cover(game), el('span', 'post-media-title', game.name));
  media.append(heroArt(game), caption);
  // The review card's own comment toggle still runs the thread; the reply button drives it.
  const toggle = body.querySelector('.review-actions [aria-expanded]');
  let replies = item.comment_count || 0;
  const bar = actionBar(review, { api, liked: state.liked, reposted: state.reposted, bookmarked: state.bookmarked,
    signedIn: () => Boolean(state.user), report, replies,
    onReply: (event) => { toggle?.click(); event.currentTarget.setAttribute('aria-expanded', toggle?.getAttribute('aria-expanded') || 'false'); },
    onShare: async (event) => {
      const control = event.currentTarget; const copied = await copyText(`${location.origin}/app#review=${review.id}`);
      control.title = copied ? 'Link copied' : 'Could not copy the link'; control.classList.toggle('done', copied);
      if (!copied) report(new Error('Could not copy the link. Try again with clipboard access enabled.'));
    } });
  const reportControl = body.querySelector('.report-control'); if (reportControl) bar.append(reportControl);
  card.addEventListener('comment-published', () => { replies += 1; bar.setReplies(replies); });
  if (quote) {
    const words = el('p', 'post-quote', quote.quote);
    const embed = el('div', 'post-embed');
    const originalName = review.author_name || `Player ${review.user_id}`;
    embed.append(personHead(originalName, review.user_id, review.author_handle, review.created_at, authorLink, review.author_badge), context, body, media);
    main.append(head, words, embed, bar);
    if (quoteReport && state.user && state.user.id !== quote.user_id) bar.append(quoteReport(quote.id));
  } else {
    main.append(head, context, body, media, bar);
  }
  card.append(avatar, main); return card;
}

/** Long reviews stop at five lines with a control to read the rest. */
export function clampBodies(root) {
  requestAnimationFrame(() => {
    for (const body of root.querySelectorAll('.post .review-body:not([data-clamped])')) {
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
