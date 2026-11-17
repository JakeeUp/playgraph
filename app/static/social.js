// Text and link helpers for profiles, follows and reports. No DOM here, so
// they are unit tested in node.

import { plural, integer } from './library.js';

export const BIO_MAX = 280;

// The reasons the server accepts, in the order the report form lists them.
export const REPORT_REASONS = [
  ['spam', 'Spam or advertising'],
  ['harassment', 'Harassment or bullying'],
  ['hate', 'Hate or slurs'],
  ['sexual', 'Sexual content'],
  ['spoilers', 'Unmarked spoilers'],
  ['other', 'Something else'],
];

export const VISIBILITY = {
  public: { label: 'Everyone', note: 'Anyone can see your profile, bio, reviews and who you follow.' },
  followers: { label: 'Followers', note: 'You approve each follower. Everyone else sees only your name and counts. Your reviews stay public on game pages.' },
  private: { label: 'Only me', note: 'Your profile looks like it does not exist to anyone else. Your reviews stay public on game pages.' },
};

/** The player ID in a "#user=12" link, or null. Same bounds as the server. */
export function userFromHash(hash) {
  const match = /^#user=([1-9][0-9]{0,9})$/.exec(hash || '');
  if (!match) return null;
  const id = Number(match[1]);
  return id <= 2147483647 ? id : null;
}

const PROFILE_ROOT = '/app';
export const profileHref = (id) => `${PROFILE_ROOT}#user=${Number(id)}`;

export function viewFromHash(hash) {
  const match = /^#view=(library|software|stats|feed|news|explore|notifications|replay)$/.exec(hash || '');
  return match?.[1] || null;
}

export function notificationText(item) {
  const name = item.actors[0]?.display_name || 'A player';
  const others = item.actor_count - 1;
  const people = others > 0 ? `${name} and ${integer(others)} ${others === 1 ? 'other' : 'others'}` : name;
  const action = {
    like: 'liked your review', repost: 'reposted your review', quote: 'quoted your review',
    comment: 'replied to your review', follow: 'followed you', follow_accepted: 'accepted your follow request',
  }[item.kind];
  return `${people} ${action}`;
}

/** "12 followers · 3 following · 1 review" */
/**
 * The pinned games from four slot values, in order with empty slots dropped.
 * null when a game is chosen twice, so the page can say so before sending.
 */
export function favoriteIds(values) {
  const ids = values.filter(Boolean).map(Number);
  return new Set(ids).size === ids.length ? ids : null;
}

/** The X-style stat row under a profile: [label, value] pairs, hours from minutes. */
export function profileStats(counts) {
  if (!counts) return [];
  return [['Followers', counts.followers], ['Following', counts.following], ['Reviews', counts.reviews],
    ['Likes', counts.likes_received ?? 0], ['Verified hours', Math.floor((counts.verified_minutes ?? 0) / 60)]];
}

/** The line under a trending game: where it is trending, and the verified hours posted this week. */
export function trendLine(genre, hours) {
  const where = genre ? `Trending in ${genre}` : 'Trending';
  const n = Math.max(0, Math.floor(Number(hours) || 0));
  if (!n) return where;
  return `${where} · ${integer(n)} ${n === 1 ? 'hour' : 'hours'} reviewed this week`;
}

/** Why a suggested player is on the list. Shared games beat a like count. */
export function followReason(person) {
  const shared = Math.max(0, Math.floor(Number(person?.shared_games) || 0));
  const likes = Math.max(0, Math.floor(Number(person?.likes_received) || 0));
  if (shared) return `${integer(shared)} ${shared === 1 ? 'game' : 'games'} in common`;
  if (likes) return `${integer(likes)} ${likes === 1 ? 'like' : 'likes'}`;
  return 'Active this week';
}

export function countsLine(counts) {
  if (!counts) return '';
  return [`${integer(counts.followers)} ${plural(counts.followers, 'follower')}`,
    `${integer(counts.following)} following`,
    `${integer(counts.reviews)} ${plural(counts.reviews, 'review')}`].join(' · ');
}

/** Why a profile shows less than usual, or '' when it shows everything. */
export function limitedNote(profile) {
  if (!profile?.limited) return '';
  if (profile.viewer?.blocked) return 'You blocked this player. Their reviews and comments are hidden from you, and yours from them.';
  if (profile.viewer?.requested) return 'This player shares their profile with followers only. Your follow request is waiting for them.';
  return 'This player shares their profile with followers only.';
}

/** Characters left in a bio, counted the way the server cleans it (runs of whitespace are one space). */
export function bioRemaining(text) {
  return BIO_MAX - String(text || '').trim().split(/\s+/).join(' ').length;
}
