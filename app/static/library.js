export const genresFor = (game) => (game.genres || '').split(',').map((g) => g.trim()).filter(Boolean);
export const hours = (minutes) => new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(Math.max(0, minutes || 0) / 60);
export const integer = (value) => new Intl.NumberFormat().format(value || 0);
export const achievementPercent = (entry) => entry?.achievements_total > 0 && entry.achievements_unlocked != null
  ? Math.min(100, 100 * entry.achievements_unlocked / entry.achievements_total) : null;
export function selectLibrary(library, { query = '', filter = 'all', genre = '', sort = 'playtime', kind = 'all' } = {}) {
  const q = query.trim().toLocaleLowerCase();
  return library.filter((entry) => entry.game.name.toLocaleLowerCase().includes(q)
    && (kind === 'all' || (entry.game.content_kind || 'game') === kind)
    && (filter === 'all' || (filter === 'played' ? entry.playtime_minutes > 0 : entry.playtime_minutes === 0))
    && (!genre || genresFor(entry.game).includes(genre)))
    .sort((a, b) => {
      const delta = sort === 'playtime' ? b.playtime_minutes - a.playtime_minutes
        : sort === 'achievements' ? (achievementPercent(b) ?? -1) - (achievementPercent(a) ?? -1) : 0;
      return delta || a.game.name.localeCompare(b.game.name) || a.game.id - b.game.id;
    });
}
export function summarize(library) {
  return library.reduce((sum, entry) => ({ games: sum.games + 1, minutes: sum.minutes + entry.playtime_minutes,
    played: sum.played + Number(entry.playtime_minutes > 0), unlocked: sum.unlocked + (entry.achievements_unlocked || 0),
    achievementGames: sum.achievementGames + Number(entry.achievements_unlocked != null && entry.achievements_total > 0),
  }), { games: 0, minutes: 0, played: 0, unlocked: 0, achievementGames: 0 });
}
const STEAM_APPS = 'https://shared.fastly.steamstatic.com/store_item_assets/steam/apps';
/**
 * Steam store artwork for one app. The URL is built from the numeric app id
 * alone, so no server or user supplied URL ever reaches an img src, and the
 * one host it names is the one the page's CSP allows.
 * @param {number|string} appid
 * @param {'library_600x900.jpg'|'header.jpg'|'library_hero.jpg'} file
 * @returns {string}
 */
export const steamArt = (appid, file) => `${STEAM_APPS}/${Number(appid)}/${file}`;
