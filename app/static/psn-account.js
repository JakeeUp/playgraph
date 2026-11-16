import { $, timeLabel, copyText } from './dom.js';
import { currentSession, requestJson, failureMessage } from './net.js';
import { createJobPoller } from './jobs.js';

// The PlayStation section of the account page. Players prove they own a PSN
// Online ID by putting a one-time code in their public About Me; PlayGraph
// never asks for a PSN password, cookie or token. Importing this module does
// nothing until initPlayStation runs, so its text helpers can be unit tested.


const PSN_WORDS = { unavailable: 'PlayStation is temporarily unavailable. Try again later.', fallback: 'That didn’t work. Please try again.' };

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
export const failureText = (status, detail, retryAfter) => failureMessage(status, detail, retryAfter, PSN_WORDS);

let started = false;

export async function initPlayStation(session) {
  if (started || !session?.psn_enabled) return;
  started = true;
  const section = $('#psn-section');
  const status = $('#psn-status');
  const say = (message) => { status.textContent = message; };

  async function call(path, method = 'GET', body) {
    const init = { method, body: body ? JSON.stringify(body) : undefined };
    if (method !== 'GET') {
      // Ask for the current CSRF token each time; the session may have rotated.
      const fresh = await currentSession().catch(() => null);
      if (!fresh) throw new Error('Your session ended. Sign in again, then link PlayStation.');
      init.csrf = fresh.csrf_token;
    }
    return requestJson(path, init, PSN_WORDS);
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

  const poller = createJobPoller({
    fetchJob: (jobId) => call(`/me/psn/sync/status/${encodeURIComponent(jobId)}`),
    onActive: (job) => {
      say(job.status === 'in_progress' ? 'Syncing with PlayStation. Requests are paced, so this can take a minute or two.'
        : 'PlayStation sync is queued.');
      $('#psn-sync').disabled = true;
    },
    onSettled: async (job) => {
      $('#psn-sync').disabled = false;
      await refresh();
      say(job.status === 'complete' ? 'PlayStation sync finished.'
        : job.status === 'failed' ? 'The PlayStation sync failed. Try again later.' : '');
    },
    onError: (error) => { $('#psn-sync').disabled = false; say(error.message); },
  });
  const poll = (jobId) => poller.poll(jobId);

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
    say(await copyText($('#psn-code').textContent) ? 'Code copied.' : 'Copy didn’t work. Select the code and copy it yourself.');
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
      // 409: a sync is already running. Its job id comes from the server.
      if (error.status === 409) void refresh().then((state) => state.job_id && poll(state.job_id));
      else say(error.message);
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
