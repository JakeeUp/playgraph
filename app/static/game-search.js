import { $, el, button, cover, loadingState, aborted, formError, steamLink } from './dom.js';

export function searchResultMeta(row) {
  const released = row.first_release_date ? new Date(row.first_release_date).getUTCFullYear() : null;
  const year = Number.isFinite(released) ? released : null;
  return [year, (row.platforms || []).map((p) => p.abbreviation || p.name).join(', ')].filter(Boolean).join(' · ') || 'Release details not listed';
}

export function createGameSearch(state, api, gameDialog) {
  const root = $('#game-search'); const results = $('#game-search-results');
  const form = $('#game-search-form'); const input = $('#game-search-query');
  const submit = $('#game-search-submit'); let version = 0;
  const toggle = $('#game-search-open'); toggle.setAttribute('aria-controls', form.id); toggle.setAttribute('aria-expanded', 'false');
  toggle.addEventListener('click', () => { form.hidden = !form.hidden; toggle.setAttribute('aria-expanded', String(!form.hidden)); if (!form.hidden) input.focus(); });
  form.addEventListener('submit', async (event) => {
    event.preventDefault(); const q = input.value.trim(); if (q.length < 2) return;
    const request = ++version; submit.disabled = true; results.setAttribute('aria-busy', 'true');
    results.replaceChildren(loadingState('Searching games…'));
    try {
      const platform = $('#game-search-platform').value;
      const data = await api(`/games/search?${new URLSearchParams({ q, platform })}`);
      if (request !== version) return;
      results.replaceChildren();
      if (data.local.length) addGroup('Already on PlayGraph', data.local, true);
      const localIds = new Set(data.local.map((row) => row.id));
      const external = data.external.filter((row) => !localIds.has(row.game_id));
      if (external.length) addGroup('From IGDB', external, false);
      if (!data.local.length && !external.length) results.append(el('p', 'helper', 'No matching games. Try another title or platform.'));
      if (!state.user) results.append(el('p', 'helper', 'Sign in to search IGDB and add missing games.'), steamLink('Sign in'));
    } catch (error) {
      if (request !== version || aborted(error)) return;
      const problem = el('p', 'form-error'); problem.setAttribute('role', 'alert'); results.replaceChildren(problem); formError(problem, error);
    } finally { if (request === version) { submit.disabled = false; results.removeAttribute('aria-busy'); } }
  });
  function addGroup(title, rows, local) {
    const group = el('section'); group.append(el('h3', '', title));
    for (const row of rows) {
      const item = el('div', 'game-search-result'); item.append(cover(row));
      const text = el('div'); text.append(el('h4', '', row.name), el('p', 'helper', searchResultMeta(row)));
      const problem = el('p', 'form-error'); problem.setAttribute('role', 'alert'); problem.hidden = true;
      const action = button(local || row.game_id ? 'Open game' : 'Add to PlayGraph', 'button secondary', async () => {
        const request = version; action.disabled = true; problem.hidden = true;
        try {
          const game = local ? row : row.game_id ? await api(`/games/${row.game_id}`)
            : await api('/games/import', { method: 'POST', body: JSON.stringify({ igdb_id: row.igdb_id }) });
          if (request !== version) return;
          if (!local) row.game_id = game.id;
          action.textContent = 'Open game'; await gameDialog.open(game);
        } catch (error) { if (request === version && !aborted(error)) formError(problem, error); }
        finally { action.disabled = false; }
      });
      text.append(problem); item.append(text, action); group.append(item);
    }
    results.append(group);
  }
  function clear() { version += 1; input.value = ''; form.hidden = true; toggle.setAttribute('aria-expanded', 'false'); results.replaceChildren(); submit.disabled = false; results.removeAttribute('aria-busy'); }
  return { clear, show(visible) { if (!visible && !root.hidden) clear(); root.hidden = !visible; } };
}
