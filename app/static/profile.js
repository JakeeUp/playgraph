import { $, el, button, cover, heroArt, timeLabel, emptyState, numbered, formError, aborted, loadingState } from './dom.js';
import { postCard, loadLikes, loadReposts, loadBookmarks, clampBodies, avatarFor, handleText, compactCount, badgeFor } from './post.js';
import { limitedNote, profileHref, profileStats, REPORT_REASONS } from './social.js';

// A "Report" text button that opens a small form under it: a reason, an
// optional note, send. `type` is review, comment or user. Used on profiles,
// review rows and comments. Nothing here says whether a block exists.
export function reportControl(api, type, id) {
  const wrap = el('span', 'report-control');
  const open = button('Report', 'text-button report-open', () => { open.hidden = true; form.hidden = false; reason.focus(); });
  const form = el('form', 'report-form'); form.hidden = true;
  const reasonLabel = el('label', '', 'Why are you reporting this?');
  const reason = el('select'); reason.required = true;
  reason.append(new Option('Choose a reason', ''), ...REPORT_REASONS.map(([value, label]) => new Option(label, value)));
  const select = el('span', 'select'); select.append(reason); reasonLabel.append(select);
  const noteLabel = el('label', '', 'Anything the moderator should know (optional)');
  const note = el('textarea'); note.rows = 2; note.maxLength = 500; noteLabel.append(note);
  const error = el('p', 'form-error'); error.setAttribute('role', 'alert'); error.hidden = true;
  const send = el('button', 'button secondary', 'Send report'); send.type = 'submit';
  const cancel = button('Cancel', 'text-button', () => { form.hidden = true; open.hidden = false; error.hidden = true; open.focus(); });
  const row = el('div', 'report-actions'); row.append(send, cancel);
  form.append(reasonLabel, noteLabel, error, row);
  form.addEventListener('submit', async (event) => {
    event.preventDefault(); error.hidden = true;
    if (!reason.value) { error.textContent = 'Choose a reason first.'; error.hidden = false; reason.focus(); return; }
    send.disabled = true;
    try {
      await api('/reports', { method: 'POST', body: JSON.stringify({ target_type: type, target_id: id, reason: reason.value, note: note.value.trim() || null }) });
      const done = el('span', 'helper report-sent', 'Thanks. Your report was sent to the moderator.'); done.setAttribute('role', 'status');
      wrap.replaceChildren(done);
    } catch (failure) { formError(error, failure); } finally { send.disabled = false; }
  });
  wrap.append(open, form); return wrap;
}

// A player's name as a link to their profile.
export function authorLink(name, id, className = '') {
  const link = el('a', `author-link ${className}`.trim(), name); link.href = profileHref(id);
  // Already on that profile (say, from a game page opened over it): the hash
  // would not change, so ask for the profile again directly.
  link.addEventListener('click', (event) => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || location.hash !== `#user=${Number(id)}`) return;
    event.preventDefault(); window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  return link;
}

// The profile page: name, bio, counts, follow and block controls, recent
// reviews, and who they follow. It is a view of the app like Reviews or
// Explore, opened from any author name through a #user=ID link.
export function createProfile(state, api, gameDialog, report) {
  const root = $('#profile');
  let requestId = 0;

  async function open(id) {
    const request = ++requestId;
    root.setAttribute('aria-busy', 'true');
    root.replaceChildren(loadingState('Loading profile…'));
    let data;
    try { data = await api(`/users/${id}`); } catch (failure) {
      if (request !== requestId || aborted(failure)) return;
      root.removeAttribute('aria-busy');
      // A missing player, a private profile and someone who blocked you all
      // look the same here, as they do on the server.
      root.replaceChildren(failure.status === 404
        ? emptyState({ title: 'Player not found', text: 'This profile doesn’t exist or isn’t shared with you.' })
        : emptyState({ kind: 'error', title: 'Couldn’t load this profile', text: failure.message,
          actions: [button('Try again', 'button secondary', () => void open(id))] }));
      if (failure.status !== 404) report(failure);
      return;
    }
    if (request !== requestId) return;
    root.removeAttribute('aria-busy');
    render(data);
  }

  function render(data) {
    document.title = `${data.display_name} | PlayGraph`;
    const head = el('header', 'profile-head');
    const who = el('div', 'profile-who');
    const name = el('h1', '', data.display_name); name.id = 'profile-name';
    const earned = badgeFor(data.badge); if (earned) name.append(' ', earned);
    who.append(name);
    if (data.handle) who.append(el('p', 'profile-handle', handleText(data.handle)));
    if (data.joined) { const joined = el('p', 'helper', 'Joined '); joined.append(el('span', 'date', timeLabel(data.joined))); who.append(joined); }
    if (data.bio) who.append(el('p', 'profile-bio', data.bio));
    const note = limitedNote(data); if (note) who.append(el('p', 'profile-note', note));
    if (data.viewer.follows_you) who.append(el('p', 'helper', 'Follows you'));
    if (data.counts) who.append(statRow(data.counts));
    const banner = el('div', 'profile-banner'); banner.setAttribute('aria-hidden', 'true');
    if (data.cover_url) {
      const image = el('img', 'profile-cover'); image.src = data.cover_url; image.alt = ''; image.referrerPolicy = 'no-referrer';
      image.addEventListener('error', () => { image.remove(); if (data.banner_game) banner.append(heroArt(data.banner_game)); }, { once: true });
      banner.append(image);
    } else if (data.banner_game) banner.append(heroArt(data.banner_game));
    const top = el('div', 'profile-top'); top.append(avatarFor(data.display_name, data.id, 'avatar profile-avatar', data.avatar_url), actions(data));
    head.append(banner, top, who);
    root.replaceChildren(head);
    if (data.room) root.append(gameRoom(data));
    if (data.recent_reviews) root.append(data.viewer.is_self ? ownPosts(data) : recentReviews(data));
    // Who follows whom is shown to signed-in players only, as the server requires.
    if (data.counts && !data.limited && state.user) root.append(people(data));
  }

  function gameRoom(data) {
    const section = el('section', 'profile-section game-room');
    const head = el('div', 'section-heading'); head.append(el('h2', '', 'Game room'));
    if (data.viewer.is_self) {
      const edit = el('a', 'text-link', 'Customize'); edit.href = '/account'; edit.search = '?edit=favorites'; head.append(edit);
    }
    section.append(head, el('p', 'helper', 'Your favorites, plus up to 12 recently updated games on each shelf.'));
    section.append(favorites(data));
    for (const [status, title] of [['playing', 'Playing now'], ['completed', 'Completed'], ['want_to_play', 'Want to play']]) {
      const shelf = el('div', 'room-shelf'); shelf.append(el('h3', '', title));
      const games = data.room[status] || [];
      const row = el('div', 'room-games');
      for (const game of games) {
        const open = button('', 'room-game', () => gameDialog.open(game)); open.setAttribute('aria-label', `Open ${game.name}`);
        open.append(cover(game), el('span', '', game.name)); row.append(open);
      }
      if (!games.length) row.append(el('p', 'helper', data.viewer.is_self ? 'Set a game’s status on its game page to fill this shelf.' : 'No games on this shelf yet.'));
      shelf.append(row); section.append(shelf);
    }
    return section;
  }

  // Follow, block and report, or a link to edit your own profile.
  function actions(data) {
    const row = el('div', 'profile-actions');
    const problem = el('p', 'form-error'); problem.setAttribute('role', 'alert'); problem.hidden = true;
    if (data.viewer.is_self) {
      const edit = el('a', 'button secondary', 'Edit profile'); edit.href = '/account'; edit.search = '?edit=profile'; row.append(edit); return row;
    }
    if (!state.user) {
      const signIn = el('a', 'button primary', 'Sign in to follow'); signIn.href = '/account'; row.append(signIn); return row;
    }
    const act = (control, path, method) => async () => {
      control.disabled = true; problem.hidden = true;
      try { await api(path, { method }); document.dispatchEvent(new CustomEvent('review-changed')); await open(data.id); }
      catch (failure) { formError(problem, failure); control.disabled = false; }
    };
    if (data.viewer.blocked) {
      const unblock = button('Unblock', 'button secondary', () => {});
      unblock.addEventListener('click', act(unblock, `/users/${data.id}/block`, 'DELETE'));
      row.append(unblock, problem); return row;
    }
    // Following, Requested (waiting for a followers-only player to accept) or Follow.
    const { following, requested } = data.viewer; const engaged = following || requested;
    const label = following ? 'Following' : requested ? 'Requested' : data.profile_visibility === 'followers' ? 'Ask to follow' : 'Follow';
    const follow = button(label, engaged ? 'button secondary' : 'button primary', () => {});
    follow.setAttribute('aria-pressed', String(engaged));
    if (following) follow.setAttribute('aria-label', `Unfollow ${data.display_name}`);
    if (requested) follow.setAttribute('aria-label', `Cancel your follow request to ${data.display_name}`);
    follow.addEventListener('click', act(follow, `/users/${data.id}/follow`, engaged ? 'DELETE' : 'POST'));
    // Blocking asks once, and says what it does.
    const confirmBox = el('div', 'block-confirmation'); confirmBox.hidden = true;
    const blockButton = button('Block', 'text-button', () => { confirmBox.hidden = false; blockButton.hidden = true; keep.focus(); });
    const keep = button('Cancel', 'button secondary', () => { confirmBox.hidden = true; blockButton.hidden = false; blockButton.focus(); });
    const confirm = button(`Block ${data.display_name}`, 'button secondary', () => {});
    confirm.addEventListener('click', act(confirm, `/users/${data.id}/block`, 'POST'));
    confirmBox.append(el('p', '', 'You’ll stop seeing each other’s reviews and comments, and any follows between you are removed. They aren’t told.'), keep, confirm);
    row.append(follow, blockButton, reportControl(api, 'user', data.id), confirmBox, problem);
    return row;
  }

  function cardFor(item) {
    return postCard(item, { gameDialog, api, state, report, authorLink, quoteReport: (id) => reportControl(api, 'repost', id) });
  }

  function fillPosts(list, items, emptyTitle, emptyText) {
    list.replaceChildren();
    for (const item of items) list.append(cardFor(item));
    clampBodies(list);
    if (state.user && items.length) {
      const reviews = items.map((item) => item.review);
      void loadLikes(api, state.liked, reviews).catch(report);
      void loadReposts(api, state.reposted, reviews).catch(report);
      void loadBookmarks(api, state.bookmarked, reviews).catch(report);
    }
    if (!items.length) list.append(emptyState({ title: emptyTitle, text: emptyText }));
  }

  function recentReviews(data) {
    const section = el('section', 'profile-section');
    const heading = el('div', 'section-heading'); heading.append(el('h2', '', 'Recent reviews')); section.append(heading);
    const list = el('div', 'feed-items');
    fillPosts(list, data.recent_reviews, 'No reviews yet', 'Nothing posted so far.');
    section.append(list); return section;
  }

  // Reviews and Bookmarks. Bookmarks exist only on your own profile.
  function ownPosts(data) {
    const section = el('section', 'profile-section');
    const tabs = el('div', 'filter-chips'); tabs.setAttribute('role', 'group'); tabs.setAttribute('aria-label', 'Your posts');
    const list = el('div', 'feed-items');
    const reviewsChip = button('Reviews', 'chip selected', () => show('reviews'));
    const savedChip = button('Bookmarks', 'chip', () => show('bookmarks'));
    reviewsChip.setAttribute('aria-pressed', 'true'); savedChip.setAttribute('aria-pressed', 'false');
    tabs.append(reviewsChip, savedChip);
    let loadId = 0;
    async function show(kind) {
      const current = ++loadId;
      const saved = kind === 'bookmarks';
      reviewsChip.classList.toggle('selected', !saved); savedChip.classList.toggle('selected', saved);
      reviewsChip.setAttribute('aria-pressed', String(!saved)); savedChip.setAttribute('aria-pressed', String(saved));
      if (!saved) { fillPosts(list, data.recent_reviews, 'No reviews yet', 'Open a game you’ve played and rate it.'); return; }
      list.replaceChildren(loadingState('Loading bookmarks…'));
      try {
        const page = await api('/me/bookmarks?limit=20&offset=0');
        if (current !== loadId) return;
        fillPosts(list, page.items, 'No bookmarks yet', 'Save a review with the bookmark on its post. Only you can see these.');
      } catch (failure) {
        if (current === loadId) list.replaceChildren(emptyState({ kind: 'error', title: 'Couldn’t load bookmarks', text: failure.message }));
      }
    }
    show('reviews');
    section.append(tabs, list); return section;
  }

  // Followers, following, reviews, likes and verified hours, X style: bold number, quiet label.
  function statRow(counts) {
    const row = el('ul', 'profile-stats'); row.setAttribute('aria-label', 'Profile stats');
    for (const [label, value] of profileStats(counts)) {
      const item = el('li'); item.append(numbered('strong', 'num', compactCount(value)), ' ', el('span', '', label)); row.append(item);
    }
    return row;
  }

  // Four pinned games. The first wears the crown.
  function favorites(data) {
    const section = el('section', 'profile-section');
    const heading = el('div', 'section-heading'); heading.append(el('h2', '', 'Favorite games'));
    if (data.viewer.is_self) { const edit = el('a', 'text-link', 'Edit'); edit.href = '/account'; edit.search = '?edit=favorites'; heading.append(edit); }
    section.append(heading);
    const row = el('ol', 'favorite-row');
    data.favorites.forEach((game, index) => {
      const item = el('li', index === 0 ? 'favorite crowned' : 'favorite');
      const open = button('', 'favorite-open', () => gameDialog.open(game));
      open.setAttribute('aria-label', `${index === 0 ? 'Top favorite' : `Favorite ${index + 1}`}: ${game.name}`);
      open.append(cover(game));
      if (index === 0) { const crown = el('span', 'crown', 'Top pick'); crown.setAttribute('aria-hidden', 'true'); open.append(crown); }
      item.append(open, el('span', 'favorite-name', game.name)); row.append(item);
    });
    if (!data.favorites.length) row.append(el('li', 'helper', 'Pin up to four games from your account page.'));
    section.append(row); return section;
  }

  // Followers and following, loaded when asked for, twenty at a time.
  function people(data) {
    const section = el('section', 'profile-section');
    const heading = el('div', 'section-heading'); heading.append(el('h2', '', 'People')); section.append(heading);
    const tabs = el('div', 'filter-chips'); tabs.setAttribute('role', 'group'); tabs.setAttribute('aria-label', 'Show');
    const list = el('ul', 'people-list'); const more = button('Show more', 'button secondary', () => void load(true)); more.hidden = true;
    const problem = el('p', 'form-error'); problem.setAttribute('role', 'alert'); problem.hidden = true;
    let kind = null; let shown = 0; let loadId = 0;
    const chips = ['followers', 'following'].map((name) => {
      const chip = button(name === 'followers' ? 'Followers' : 'Following', 'chip', () => { kind = name; void load(false); });
      chip.setAttribute('aria-pressed', 'false'); chip.dataset.kind = name; tabs.append(chip); return chip;
    });
    async function load(append) {
      const current = ++loadId; if (!append) { shown = 0; list.replaceChildren(); }
      chips.forEach((chip) => { const on = chip.dataset.kind === kind; chip.classList.toggle('selected', on); chip.setAttribute('aria-pressed', String(on)); });
      more.disabled = true; problem.hidden = true;
      try {
        const page = await api(`/users/${data.id}/${kind}?limit=20&offset=${shown}`);
        if (current !== loadId) return;
        for (const person of page.items) {
          const item = el('li'); item.append(avatarFor(person.display_name, person.id, 'avatar', person.avatar_url), authorLink(person.display_name, person.id));
          const earned = badgeFor(person.badge); if (earned) item.append(earned);
          if (person.handle) item.append(el('span', 'post-handle', handleText(person.handle)));
          list.append(item);
        }
        shown += page.items.length;
        if (!shown) list.append(el('li', 'helper', kind === 'followers' ? 'No followers yet.' : 'Not following anyone yet.'));
        more.hidden = shown >= page.total || !page.items.length;
      } catch (failure) { if (current === loadId) formError(problem, failure); } finally { more.disabled = false; }
    }
    section.append(tabs, list, problem, more); return section;
  }

  return { open, clear() { requestId += 1; root.replaceChildren(); } };
}
