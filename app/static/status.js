// Play statuses: want to play, playing, completed, paused, dropped. Set by
// hand on the game page and never changed by a sync. No DOM here, so the
// filtering is unit tested in node.

export const STATUSES = [
  { value: 'want_to_play', label: 'Want to play' },
  { value: 'playing', label: 'Playing' },
  { value: 'completed', label: 'Completed' },
  { value: 'paused', label: 'Paused' },
  { value: 'dropped', label: 'Dropped' },
];

const LABELS = new Map(STATUSES.map(({ value, label }) => [value, label]));
export const statusLabel = (value) => LABELS.get(value) || '';

/** GET /me/statuses rows as a Map of game id to row. */
export function indexStatuses(rows) {
  return new Map((rows || []).map((row) => [row.game.id, row]));
}

/** The map after one game's status changed. `row` null means cleared. Returns a new map. */
export function withStatus(statuses, gameId, row) {
  const next = new Map(statuses);
  if (row) next.set(gameId, row); else next.delete(gameId);
  return next;
}

/**
 * Library entries narrowed to one status. `status` '' keeps everything and
 * 'none' keeps games with no status. A real status also brings in games with
 * that status that aren't on this shelf (a backlog of games you don't own),
 * marked `unowned`, when `kind` matches. Entries keep their order; extras go last.
 */
export function filterByStatus(entries, statuses, status, kind = 'game') {
  if (!status) return entries;
  const owned = new Set(entries.map((entry) => entry.game.id));
  const kept = entries.filter((entry) => status === 'none'
    ? !statuses.has(entry.game.id) : statuses.get(entry.game.id)?.status === status);
  if (status === 'none') return kept;
  const extras = [...statuses.values()]
    .filter((row) => row.status === status && !owned.has(row.game.id) && (row.game.content_kind || 'game') === kind)
    .map((row) => ({ game: row.game, unowned: true }));
  return kept.concat(extras);
}

/** How many games carry each status, for the filter menu. */
export function statusCounts(statuses) {
  const counts = Object.fromEntries(STATUSES.map(({ value }) => [value, 0]));
  for (const row of statuses.values()) if (row.status in counts) counts[row.status] += 1;
  return counts;
}
