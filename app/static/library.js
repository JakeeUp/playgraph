export const genresFor = (game) => (game.genres || '').split(',').map((g) => g.trim()).filter(Boolean);
const hourFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
const integerFormat = new Intl.NumberFormat();
export const hours = (minutes) => hourFormat.format(Math.max(0, minutes || 0) / 60);
export const integer = (value) => integerFormat.format(value || 0);
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
  return library.reduce((sum, entry) => ({ games: sum.games + 1, minutes: sum.minutes + (entry.playtime_minutes || 0),
    played: sum.played + Number(entry.playtime_minutes > 0), unlocked: sum.unlocked + (entry.achievements_unlocked || 0),
    achievementGames: sum.achievementGames + Number(entry.achievements_unlocked != null && entry.achievements_total > 0),
  }), { games: 0, minutes: 0, played: 0, unlocked: 0, achievementGames: 0 });
}
// Match /me/genres using the library already in memory. Full playtime counts
// toward every tag; software stays out of the game statistics.
export function genreBreakdown(library) {
  const totals = new Map();
  for (const entry of library) {
    if ((entry.game.content_kind || 'game') !== 'game') continue;
    for (const genre of genresFor(entry.game)) {
      if (!totals.has(genre)) totals.set(genre, { genre, total_minutes: 0, game_count: 0 });
      const bucket = totals.get(genre);
      bucket.total_minutes += entry.playtime_minutes || 0;
      bucket.game_count += 1;
    }
  }
  return [...totals.values()].sort((a, b) => b.total_minutes - a.total_minutes);
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

// PlayStation ---------------------------------------------------------------
// Library entries carry a source. Steam and PlayStation numbers are never
// added together: each source has its own shelf, totals and labels.
export const TROPHY_GRADES = ['platinum', 'gold', 'silver', 'bronze'];
/** A game from another store has no Steam app id, so no Steam art or page. */
export const isPlayStationGame = (game) => game?.steam_appid == null;
export function splitBySource(library) {
  const steam = []; const psn = [];
  for (const entry of library) (entry.source === 'psn' ? psn : steam).push(entry);
  return { steam, psn };
}
/** "12.5 h", or a plain note when PlayStation did not share the hours. */
export const playtimeLabel = (entry) => entry?.playtime_minutes == null ? 'Hours not shared' : `${hours(entry.playtime_minutes)} h`;
/** "1 platinum · 3 gold · 10 silver · 40 bronze", earned counts only. */
export function trophyLine(trophies) {
  if (!trophies?.earned) return '';
  return TROPHY_GRADES.map((grade) => `${integer(trophies.earned[grade])} ${grade}`).join(' · ');
}
/** Trophy counts across a PlayStation shelf, plus how many hours were shared. */
export function playStationSummary(entries) {
  const sum = { games: 0, minutes: 0, withHours: 0, earned: 0, total: 0, platinum: 0, gold: 0, silver: 0, bronze: 0 };
  for (const entry of entries) {
    sum.games += 1;
    if (entry.playtime_minutes != null) { sum.minutes += entry.playtime_minutes; sum.withHours += 1; }
    for (const grade of TROPHY_GRADES) {
      const earned = entry.trophies?.earned?.[grade] || 0;
      sum[grade] += earned; sum.earned += earned; sum.total += entry.trophies?.total?.[grade] || 0;
    }
  }
  return sum;
}
/**
 * How a review's verified numbers read, labelled by the store they came from.
 * Reviews written before sources were recorded came from Steam.
 * @returns {{badge: string, text: string} | null} null when nothing was verified
 */
export function reviewVerification(review) {
  const minutes = review?.verified_playtime_minutes; const pct = review?.verified_achievement_pct;
  if (minutes == null && pct == null) return null;
  const psn = review.verified_source === 'psn';
  const parts = [];
  if (minutes != null) parts.push(`${hours(minutes)} h verified`);
  if (pct != null) parts.push(`${Math.round(pct)}% ${psn ? 'trophies' : 'achievements'}`);
  return { badge: psn ? 'PlayStation' : 'PC', text: parts.join(' · ') };
}
