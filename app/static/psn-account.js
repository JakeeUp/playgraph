import { $, timeLabel } from './dom.js';

// The PlayStation section of the account page. Players prove they own a PSN
// Online ID by putting a one-time code in their public About Me; PlayGraph
// never asks for a PSN password, cookie or token. Importing this module does
// nothing until initPlayStation runs, so its text helpers can be unit tested.

const POLL_MS = 5000;
const ACTIVE = new Set(['queued', 'in_progress', 'deferred']);

/** What the last sync could see, in words. `sync` is the server's last outcome. */
export function privacyNote(sync) {
  if (!sync) return 'Your first PlayStation sync will bring in your trophies.';
  if (sync.problem === 'account_not_found') return 'PlayStation couldn’t find this account at the last sync.';
  if (sync.trophies_visible === false && sync.playtime_visible === false) {
    return 'Your PSN privacy settings hide both trophies and play time, so nothing new could be imported.';
  }
  if (sync.trophies_visible === false) return 'Your PSN privacy settings hide your trophies, so only PS4 and PS5 play time was imported.';
  if (sync.playtime_visible === false) {
    return 'Your PSN privacy settings hide play time, so only trophies were imported. Hours stay blank, never zero.';
  }
  return 'Trophies and PS4 and PS5 play time were imported from your public profile.';
}

/** A failed response as a sentence a player can act on. */
export function failureText(status, detail, retryAfter) {
  if (status === 429) return `Too many attempts. Try again in ${retryAfter || '60'} seconds.`;
  if (typeof detail === 'string' && detail) return detail;
  if (status === 503) return 'PlayStation is temporarily unavailable. Try again later.';
  return 'That didn’t work. Please try again.';
}

let started = false;

export async function initPlayStation(session) {
  if (started || !session?.psn_enabled) return;
  started = true;
  const section = $('#psn-section');
  const status = $('#psn-status');
  let pollTimer = null;
  const say = (message) => { status.textContent = message; };

  async function call(path, method = 'GET', body) {
    const headers = {};
    if (method !== 'GET') {
      // Ask for the current CSRF token each time; the session may have rotated.
      const current = await fetch('/auth/session', { credentials: 'same-origin', cache: 'no-store' });
      if (!current.ok) throw new Error('Your session ended. Sign in again, then link PlayStation.');
      headers['X-CSRF-Token'] = (await current.json()).csrf_token;
    }
    if (body) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, { method, headers, credentials: 'same-origin', cache: 'no-store',
      body: body ? JSON.stringify(body) : undefined });
    const data = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) {
      const error = new Error(failureText(response.status, data?.detail, response.headers.get('Retry-After')));
      error.status = response.status; throw error;
    }
    return data;
  }

  function show(part) {
    for (const id of ['#psn-start', '#psn-pending', '#psn-linked']) $(id).hidden = id !== part;
  }

  function render(state) {
    if (state.linked) {
      show('#psn-linked');
      $('#psn-linked-id').textContent = state.online_id || 'your PSN account';
      $('#psn-last-sync').textContent = state.last_synced_at ? `Last synced ${timeLabel(state.last_synced_at)}.` : 'Not synced yet.';
      $('#psn-privacy').textContent = privacyNote(state.sync);
    } else if (state.pending) {
      show('#psn-pending');
      $('#psn-code').textContent = state.pending.code;
      $('#psn-pending-id').textContent = state.pending.online_id;
    } else {
      show('#psn-start');
    }
  }

  async function refresh() {
    const state = await call('/auth/psn');
    render(state);
    return state;
  }

  async function poll(jobId) {
    clearTimeout(pollTimer);
    try {
      const job = await call(`/me/psn/sync/status/${encodeURIComponent(jobId)}`);
      if (ACTIVE.has(job.status)) {
        say(job.status === 'in_progress' ? 'Syncing with PlayStation. Requests are paced, so this can take a minute or two.'
          : 'PlayStation sync is queued.');
        $('#psn-sync').disabled = true;
        pollTimer = setTimeout(() => void poll(jobId), POLL_MS);
        return;
      }
      $('#psn-sync').disabled = false;
      await refresh();
      say(job.status === 'complete' ? 'PlayStation sync finished.'
        : job.status === 'failed' ? 'The PlayStation sync failed. Try again later.' : '');
    } catch (error) { $('#psn-sync').disabled = false; say(error.message); }
  }

  $('#psn-start').addEventListener('submit', async (event) => {
    event.preventDefault();
    const submit = event.currentTarget.querySelector('button[type="submit"]');
    submit.disabled = true; say('Looking up that Online ID…');
    try {
      const pending = await call('/auth/psn/link', 'POST', { online_id: $('#psn-online-id').value.trim() });
      render({ linked: false, pending });
      say('Your code is ready. Add it to your About Me, then check.');
      $('#psn-copy').focus();
    } catch (error) { say(error.message); $('#psn-online-id').focus(); }
    finally { submit.disabled = false; }
  });

  $('#psn-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#psn-code').textContent); say('Code copied.'); }
    catch { say('Copy didn’t work. Select the code and copy it yourself.'); }
  });

  $('#psn-check').addEventListener('click', async (event) => {
    const control = event.currentTarget; control.disabled = true; say('Checking your PSN profile…');
    try {
      const linked = await call('/auth/psn/link/check', 'POST');
      say(`Linked ${linked.online_id}. You can remove the code from your About Me now.`);
      await refresh();
      $('#psn-sync').focus();
      if (linked.job_id) void poll(linked.job_id);
    } catch (error) { say(error.message); }
    finally { control.disabled = false; }
  });

  $('#psn-cancel').addEventListener('click', async () => {
    try { await call('/auth/psn/link', 'DELETE'); say(''); render({ linked: false, pending: null }); $('#psn-online-id').focus(); }
    catch (error) { say(error.message); }
  });

  $('#psn-sync').addEventListener('click', async (event) => {
    const control = event.currentTarget; control.disabled = true;
    try { const job = await call('/me/psn/sync', 'POST'); void poll(job.job_id); }
    catch (error) {
      control.disabled = false;
      if (error.status === 409) void poll(`sync-psn-user-${session.user.id}`); else say(error.message);
    }
  });

  try {
    const state = await refresh();
    section.hidden = false;
    if (state.linked && state.job_id) void poll(state.job_id);
  } catch (error) {
    // A disabled feature answers 404: leave the section hidden.
    if (error.status !== 404) { section.hidden = false; say(error.message); }
  }
}
