// Explore: this week's trends and players to follow. The catalog grid under
// it is the rest of the page and stays in app.js.
import { $, el, button, cover, loadingState } from './dom.js';
import { avatarFor, handleText, badgeFor } from './post.js';
import { profileHref, trendLine, followReason } from './social.js';

export function trendGameRow(entry, openGame) {
  const game = entry.game;
  const row = button('', 'rail-hit', () => openGame(game));
  row.append(cover(game));
  const text = el('span', 'rail-copy');
  text.append(el('span', 'rail-title', game.name));
  text.append(el('span', 'rail-meta', trendLine(entry.genre, entry.hours)));
  row.append(text);
  return row;
}

export function suggestionRow(person, { state, api, report }) {
  const row = el('div', 'suggest-row');
  const link = el('a', 'suggest-link');
  link.href = profileHref(person.id);
  link.append(avatarFor(person.display_name, person.id, 'avatar rail-avatar', person.avatar_url));
  const text = el('span', 'rail-copy');
  const title = el('span', 'rail-title', person.display_name || `Player ${person.id}`);
  const earned = badgeFor(person.badge); if (earned) title.append(' ', earned);
  text.append(title);
  const handle = handleText(person.handle);
  const reason = followReason(person);
  text.append(el('span', 'rail-meta', handle ? `${handle} · ${reason}` : reason));
  link.append(text);
  row.append(link, followControl(person, { state, api, report }));
  return row;
}

function followControl(person, { state, api, report }) {
  if (!state.user) {
    const signIn = el('a', 'button secondary', 'Follow');
    signIn.href = '/account';
    return signIn;
  }
  const control = button('Follow', 'button primary', async () => {
    control.disabled = true;
    try {
      const result = await api(`/users/${person.id}/follow`, { method: 'POST' });
      const requested = result?.requested === true || result?.status === 'pending';
      control.textContent = requested ? 'Requested' : 'Following';
      control.classList.remove('primary');
      control.classList.add('secondary');
    } catch (error) {
      control.disabled = false;
      report(error);
    }
  });
  return control;
}

export function createExplore(state, api, gameDialog, report) {
  let requestId = 0;
  const games = $('#trend-games');
  const reviews = $('#trend-reviews');
  const people = $('#trend-people');

  function reviewRow(item) {
    const { review, game } = item;
    const row = button('', 'rail-hit trend-review', () => gameDialog.open(game));
    row.append(avatarFor(review.author_name, review.user_id, 'avatar rail-avatar', review.author_avatar_url));
    const text = el('span', 'rail-copy');
    const title = el('span', 'rail-title', review.author_name || `Player ${review.user_id}`);
    const earned = badgeFor(review.author_badge); if (earned) title.append(' ', earned);
    text.append(title);
    const gameName = game?.name ? ` on ${game.name}` : '';
    const body = (review.body || '').replace(/\s+/g, ' ').trim();
    text.append(el('span', 'rail-meta', body ? `${body.slice(0, 90)}${gameName}` : `Reviewed${gameName}`));
    row.append(text);
    return row;
  }

  async function load() {
    const request = ++requestId;
    for (const node of [games, reviews, people]) node.replaceChildren(loadingState());
    try {
      const [trends, suggested] = await Promise.all([
        api('/explore'),
        api('/explore/suggestions'),
      ]);
      if (request !== requestId || state.view !== 'explore') return;
      const gameRows = (trends.games || []).slice(0, 8).map((entry) => trendGameRow(entry, (game) => gameDialog.open(game)));
      games.replaceChildren(...(gameRows.length ? gameRows : [el('p', 'helper', 'Nothing trending this week.')]));
      const reviewRows = (trends.reviews || []).slice(0, 8).map(reviewRow);
      reviews.replaceChildren(...(reviewRows.length ? reviewRows : [el('p', 'helper', 'No reviews trending this week.')]));
      const peopleRows = (suggested.items || []).slice(0, 8).map((person) => suggestionRow(person, { state, api, report }));
      people.replaceChildren(...(peopleRows.length ? peopleRows : [el('p', 'helper', 'No one to suggest yet.')]));
    } catch (error) {
      if (request !== requestId) return;
      const retry = button('Try again', 'button secondary', () => void load());
      games.replaceChildren(el('p', 'helper', 'Couldn’t load trends.'), retry);
      reviews.replaceChildren();
      people.replaceChildren();
      report(error);
    }
  }

  return {
    load,
    clear() { requestId += 1; games.replaceChildren(); reviews.replaceChildren(); people.replaceChildren(); },
  };
}
