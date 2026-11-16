import { $, el, button } from './dom.js';
import { currentSession, requestJson } from './net.js';
import { VISIBILITY, bioRemaining, profileHref, favoriteIds } from './social.js';

// The profile part of the account page: a bio, who can see the profile, and
// the players you blocked. Importing this module does nothing until
// initProfile runs.

const WORDS = { unavailable: 'PlayGraph can’t reach its security service right now. Try again shortly.', fallback: 'That didn’t work. Please try again.' };
let started = false;

export async function initProfile(session) {
  if (started || !session?.user) return;
  started = true;
  const section = $('#profile-section');
  const status = $('#profile-status');
  const bio = $('#profile-bio');
  const handle = $('#profile-handle');
  const visibility = $('#profile-visibility');

  async function call(path, method = 'GET', body) {
    const init = { method, body: body ? JSON.stringify(body) : undefined };
    if (method !== 'GET') {
      // Ask for the current CSRF token each time; the session may have rotated.
      const fresh = await currentSession().catch(() => null);
      if (!fresh) throw new Error('Your session ended. Sign in again to change your profile.');
      init.csrf = fresh.csrf_token;
    }
    return requestJson(path, init, WORDS);
  }

  const count = () => {
    const left = bioRemaining(bio.value);
    $('#profile-bio-count').textContent = left >= 0 ? `${left} characters left` : `${-left} characters over the limit`;
  };
  const explain = () => { $('#profile-visibility-note').textContent = VISIBILITY[visibility.value]?.note || ''; };
  bio.addEventListener('input', count);
  visibility.addEventListener('change', explain);
  $('#profile-view').href = profileHref(session.user.id);

  try {
    const own = await call(`/users/${session.user.id}`);
    bio.value = own.bio || ''; visibility.value = own.profile_visibility || 'public'; handle.value = own.handle || '';
    for (const kind of ['avatar', 'cover']) {
      const preview = $(`#profile-${kind}-preview`); preview.hidden = !own[`${kind}_url`];
      if (own[`${kind}_url`]) preview.src = own[`${kind}_url`];
    }
    void initFavorites(own.favorites || []);
  } catch (error) { status.textContent = error.message; }
  count(); explain(); section.hidden = false;
  for (const kind of ['avatar', 'cover']) {
    const save = $(`#profile-${kind}-upload`); const remove = $(`#profile-${kind}-remove`);
    async function change(clear = false) {
      const note = $('#profile-images-status'); const input = $(`#profile-${kind}-file`);
      const file = input.files?.[0];
      if (!clear && (!file || file.size > 2 * 1024 * 1024 || !['image/jpeg', 'image/png', 'image/webp'].includes(file.type))) {
        note.textContent = 'Choose a JPEG, PNG or WebP image under 2 MB.'; return;
      }
      save.disabled = true; remove.disabled = true; note.textContent = clear ? 'Removing image…' : 'Uploading image…';
      try {
        const fresh = await currentSession(); if (!fresh) throw new Error('Sign in again to change your images.');
        const saved = await requestJson(`/me/profile-images/${kind}`, { method: clear ? 'DELETE' : 'PUT',
          csrf: fresh.csrf_token, ...(clear ? {} : { body: file, headers: { 'Content-Type': 'application/octet-stream' } }) }, WORDS);
        const preview = $(`#profile-${kind}-preview`); preview.hidden = clear;
        if (clear) preview.removeAttribute('src'); else preview.src = saved.url;
        input.value = ''; note.textContent = clear ? 'Image removed.' : 'Image saved. Open your profile to see it.';
      } catch (error) { note.textContent = error.message; }
      finally { save.disabled = false; remove.disabled = false; }
    }
    save.addEventListener('click', () => void change()); remove.addEventListener('click', () => void change(true));
  }
  const editing = new URLSearchParams(location.search).get('edit');
  if (editing === 'profile' || editing === 'favorites') {
    history.replaceState(null, '', '/account');
    const target = editing === 'favorites' ? $('#favorites-form') : section;
    target.scrollIntoView({ block: 'start' }); (editing === 'favorites' ? $('#favorite-1') : bio).focus();
  }

  $('#profile-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (bioRemaining(bio.value) < 0) { status.textContent = 'Your bio is too long. Keep it to 280 characters.'; bio.focus(); return; }
    const save = event.submitter || $('#profile-form button[type="submit"]'); save.disabled = true; status.textContent = '';
    try {
      const saved = await call('/me/profile', 'PATCH', { bio: bio.value, profile_visibility: visibility.value, handle: handle.value });
      bio.value = saved.bio || ''; handle.value = saved.handle || handle.value; count(); status.textContent = 'Profile saved.';
    } catch (error) { status.textContent = error.message; } finally { save.disabled = false; }
  });

  // Four slots, each a choice from your own library. Saved as one ordered list.
  async function initFavorites(pinned) {
    const slots = [...document.querySelectorAll('[data-favorite]')]; const note = $('#favorites-status');
    let games = [];
    try {
      const library = await call('/me/library');
      const seen = new Map(); for (const entry of library) seen.set(entry.game.id, entry.game);
      for (const game of pinned) seen.set(game.id, game);
      games = [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
    } catch (error) { note.textContent = error.message; games = pinned; }
    slots.forEach((select, index) => {
      select.replaceChildren(new Option('Empty', ''), ...games.map((game) => new Option(game.name, String(game.id))));
      select.value = pinned[index] ? String(pinned[index].id) : '';
    });
    if (!games.length) note.textContent = 'Sync a library first, then pin your favorites here.';
    $('#favorites-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const ids = favoriteIds(slots.map((select) => select.value));
      if (ids === null) { note.textContent = 'Each game can be pinned once.'; return; }
      const save = event.submitter || $('#favorites-form button[type="submit"]'); save.disabled = true; note.textContent = '';
      try {
        const saved = await call('/me/favorites', 'PUT', { game_ids: ids });
        slots.forEach((select, index) => { select.value = saved.favorites[index] ? String(saved.favorites[index].id) : ''; });
        note.textContent = 'Favorites saved.';
      } catch (error) { note.textContent = error.message; } finally { save.disabled = false; }
    });
  }

  // Follow requests waiting for an answer, newest first.
  const requests = $('#request-list'); const moreRequests = $('#request-more'); let asked = 0;
  async function loadRequests(append = false) {
    if (!append) { asked = 0; requests.replaceChildren(); }
    moreRequests.disabled = true;
    try {
      const page = await call(`/me/follow-requests?limit=50&offset=${asked}`);
      for (const item of page.items) requests.append(requestRow(item));
      asked += page.items.length;
      if (!asked) requests.append(el('li', 'helper', 'No requests waiting.'));
      moreRequests.hidden = asked >= page.total || !page.items.length;
    } catch (error) { requests.append(el('li', 'helper', error.message)); } finally { moreRequests.disabled = false; }
  }
  function requestRow(item) {
    const row = el('li'); const name = el('a', '', item.user.display_name); name.href = profileHref(item.user.id);
    const answer = (action, done) => async () => {
      accept.disabled = true; decline.disabled = true;
      try { await call(`/me/follow-requests/${item.id}/${action}`, 'POST'); row.replaceChildren(el('span', 'helper', done)); }
      catch (error) { status.textContent = error.message; accept.disabled = false; decline.disabled = false; }
    };
    const accept = button('Accept', 'button secondary', () => {});
    const decline = button('Decline', 'text-button', () => {});
    accept.addEventListener('click', answer('accept', `${item.user.display_name} now follows you.`));
    decline.addEventListener('click', answer('decline', `Declined ${item.user.display_name}.`));
    const controls = el('span', 'request-actions'); controls.append(accept, decline);
    row.append(name, controls); return row;
  }
  moreRequests.addEventListener('click', () => void loadRequests(true));
  void loadRequests();

  // Blocked players, newest first, fifty at a time.
  const list = $('#blocked-list'); const more = $('#blocked-more'); let shown = 0;
  async function loadBlocks(append = false) {
    if (!append) { shown = 0; list.replaceChildren(); }
    more.disabled = true;
    try {
      const page = await call(`/me/blocks?limit=50&offset=${shown}`);
      for (const person of page.items) list.append(blockedRow(person));
      shown += page.items.length;
      if (!shown) list.append(el('li', 'helper', 'You haven’t blocked anyone.'));
      more.hidden = shown >= page.total || !page.items.length;
    } catch (error) { list.append(el('li', 'helper', error.message)); } finally { more.disabled = false; }
  }
  function blockedRow(person) {
    const row = el('li'); const name = el('a', '', person.display_name); name.href = profileHref(person.id);
    const unblock = button('Unblock', 'button secondary', async () => {
      unblock.disabled = true;
      try { await call(`/users/${person.id}/block`, 'DELETE'); row.replaceChildren(el('span', 'helper', `${person.display_name} is unblocked.`)); }
      catch (error) { status.textContent = error.message; unblock.disabled = false; }
    });
    row.append(name, unblock); return row;
  }
  more.addEventListener('click', () => void loadBlocks(true));
  await loadBlocks();
}
