/* PlayGraph demo: the app's API, answered in the browser from sample data.

   Loaded as a plain script before the app, it takes over window.fetch for the
   app's own API paths and nothing else. No request leaves the browser. What a
   visitor posts lives in this tab (sessionStorage) and Reset clears it. */
(() => {
  'use strict';
  const DATA = window.PLAYGRAPH_DEMO;
  const KEY = 'playgraph-demo-state-v1';
  const ME = DATA.users[0];
  const MINUTE = 60000;
  const API = /^\/(auth\/|games(\/|$)|me\/|feed$|reviews\/|news$|users\/|reports$|reposts\/|explore(\/|$))/;

  const users = new Map(DATA.users.map((user) => [user.id, user]));
  const games = new Map(DATA.games.map((game) => [game.id, { header_image_url: null, first_release_date: null,
    cover_url: null, hero_url: null, ...game }]));
  const ago = (minutes) => new Date(Date.now() - minutes * MINUTE).toISOString();

  function fresh() {
    return {
      signedIn: true, syncs: {}, nextReview: 1000, nextComment: 1000,
      reviews: DATA.reviews.map(({ minutes_ago, ...review }) => ({ ...review, created_at: ago(minutes_ago) })),
      comments: DATA.comments.map(({ minutes_ago, ...comment }) => ({ ...comment, created_at: ago(minutes_ago) })),
      capturedAt: ago(180),
      liked: [], reposted: [], bookmarked: [], quotes: null, plainReposts: null, nextRepost: 10,
      following: (DATA.follows || []).filter(([a]) => a === ME.id).map(([, b]) => b),
      favorites: [...(DATA.favorites || [])], handle: ME.handle, bio: ME.bio,
    };
  }
  let state;
  try { state = JSON.parse(sessionStorage.getItem(KEY)) || fresh(); } catch { state = fresh(); }
  const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify(state)); } catch { /* private mode: keep it in memory */ } };

  function notificationRows() {
    if (!state.notifications) {
      const review = state.reviews.find((row) => row.user_id === ME.id);
      const people = DATA.users.filter((person) => person.id !== ME.id);
      state.notifications = [];
      const add = (kind, actor, target = null, comment = null) => state.notifications.push({
        id: state.notifications.length + 1, kind, actor_id: actor.id, review_id: target,
        comment_id: comment, created_at: ago(8 + state.notifications.length), read_at: null,
      });
      if (review) {
        for (const person of people.slice(0, 4)) add('like', person, review.id);
        add('repost', people[0], review.id); add('quote', people[1], review.id);
        const reply = state.comments.find((row) => row.review_id === review.id && row.user_id !== ME.id);
        if (reply) add('comment', users.get(reply.user_id), review.id, reply.id);
      }
      add('follow', people[0]); add('follow_accepted', people[1]); save();
    }
    return state.notifications.filter((row) => ageHours(row.created_at) < 90 * 24 &&
      (!row.review_id || state.reviews.some((review) => review.id === row.review_id)));
  }
  function notificationInbox(params) {
    const rows = notificationRows(); const groups = new Map();
    for (const row of [...rows].sort((a, b) => b.id - a.id)) {
      const key = [row.kind, row.review_id, row.comment_id, row.created_at.slice(0, 10)].join(':');
      if (!groups.has(key)) groups.set(key, { id: row.id, kind: row.kind, review_id: row.review_id,
        comment_id: row.comment_id, created_at: row.created_at, unread_count: 0, actors: [] });
      const group = groups.get(key);
      if (!row.read_at) group.unread_count += 1;
      if (!group.actors.some((actor) => actor.id === row.actor_id)) group.actors.push({
        id: row.actor_id, display_name: author(row.actor_id), handle: handleOf(row.actor_id),
      });
    }
    const items = [...groups.values()].map((group) => ({ ...group, actor_count: group.actors.length, actors: group.actors.slice(0, 3) }));
    return { items: page(items, params), total: items.length, latest_id: Math.max(0, ...rows.map((row) => row.id)),
      unread_count: rows.filter((row) => !row.read_at).length };
  }

  // ---- shapes the real API returns ----
  const game = (id) => games.get(id);
  const author = (id) => users.get(id)?.display_name || `Player ${id}`;
  const handleOf = (id) => (id === ME.id ? (state.handle ?? ME.handle) : users.get(id)?.handle) || null;
  const imageOf = (id, kind) => id === ME.id ? state.images?.[kind] || null : null;
  const verifiedMinutes = (id) => state.reviews.filter((review) => review.user_id === id &&
    game(review.game_id)?.content_kind === 'game').reduce((sum, review) => sum + (review.verified_playtime_minutes || 0), 0);
  const badgeOf = (id) => users.get(id)?.has_steam
    ? [5000, 1000, 100].find((hours) => verifiedMinutes(id) >= hours * 60) ?? null : null;
  const liked = () => (state.liked ||= []);
  const likeCount = (review) => (review.like_count || 0) + (liked().includes(review.id) ? 1 : 0);
  // A few shares from the made-up players, so Following and For You have something to show
  // before the visitor reposts anything. Two plain reposts of one review make it "popular".
  function ensureShare() {
    state.reposted ||= []; state.bookmarked ||= [];
    if (state.quotes) return;
    const others = state.reviews.filter((review) => review.user_id !== ME.id && game(review.game_id)?.content_kind === 'game');
    const quoted = others.find((review) => review.user_id === 3) || others[0];
    const shared = others.find((review) => review.user_id === 2) || others[1] || quoted;
    state.quotes = quoted ? [{ id: 1, user_id: 2, review_id: quoted.id, quote: 'The hours on this one are the whole argument.', created_at: ago(35) }] : [];
    state.plainReposts = shared ? [
      { id: 2, user_id: 3, review_id: shared.id, created_at: ago(20) },
      { id: 3, user_id: 5, review_id: shared.id, created_at: ago(18) },
    ] : [];
    save();
  }
  const shareRows = () => {
    ensureShare();
    return [...state.plainReposts, ...state.quotes,
      ...state.reposted.map((id) => ({ id: 100000 + id, user_id: ME.id, review_id: id, quote: null, created_at: ago(1) }))];
  };
  const repostCount = (id) => shareRows().filter((row) => row.review_id === id).length;
  const repostOut = (row) => ({ id: row.id, user_id: row.user_id, author_name: author(row.user_id),
    author_handle: handleOf(row.user_id), author_badge: badgeOf(row.user_id), author_avatar_url: imageOf(row.user_id, 'avatar'), quote: row.quote || null, created_at: row.created_at });
  const reviewOut = (review) => ({ ...review, author_name: author(review.user_id), author_handle: handleOf(review.user_id),
    author_badge: badgeOf(review.user_id),
    author_avatar_url: imageOf(review.user_id, 'avatar'),
    like_count: likeCount(review), repost_count: repostCount(review.id),
    verified_source: review.verified_playtime_minutes == null ? null : 'steam' });
  const commentOut = (comment) => ({ ...comment, author_name: author(comment.user_id) });
  const library = () => DATA.library.map((entry) => ({ game: game(entry.game_id), source: 'steam',
    playtime_minutes: entry.playtime_minutes, achievements_unlocked: entry.achievements_unlocked,
    achievements_total: entry.achievements_total, trophies: null, captured_at: state.capturedAt }));
  const owned = (gameId) => DATA.library.find((entry) => entry.game_id === gameId);
  const newestFirst = (a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id;
  const commentCount = (reviewId) => state.comments.filter((comment) => comment.review_id === reviewId).length;
  const feedItem = (review, reason, repost = null) => {
    const item = { review: reviewOut(review), game: game(review.game_id), comment_count: commentCount(review.id), reason };
    if (repost) item.repost = repostOut(repost);
    return item;
  };
  function cleanedQuote(value) {
    if (value == null || value === '') return null;
    if (typeof value !== 'string') return false;
    return value.trim().replace(/\s+/g, ' ').slice(0, 280) || null;
  }
  function repostState(id, quoteId) {
    const body = { review_id: id, reposted: (state.reposted || []).includes(id), repost_count: repostCount(id) };
    if (quoteId) body.quote_id = quoteId;
    return body;
  }

  function respond(status, body) {
    const headers = { 'Content-Type': 'application/json' };
    return new Response(status === 204 ? null : JSON.stringify(body), { status, headers });
  }
  const missing = (what) => respond(404, { detail: `${what} not found` });
  const page = (rows, params, size = 20) => {
    const offset = Number(params.get('offset')) || 0;
    return rows.slice(offset, offset + (Number(params.get('limit')) || size));
  };
  const matches = (gameId, query) => !query || game(gameId).name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
  function needSession() {
    return state.signedIn ? null : respond(401, { detail: 'Not authenticated' });
  }
  function readBody(init) {
    try { return JSON.parse(init.body || '{}'); } catch { return {}; }
  }
  function validRating(value) {
    return typeof value === 'number' && value >= 0.5 && value <= 5 && Number.isInteger(value * 2);
  }
  function cleanText(value, limit) {
    if (value == null) return '';
    if (typeof value !== 'string' || value.length > limit || value.includes('\u0000')) return null;
    return value.trim();
  }

  // Same window and half-life as the server: 7 days, half every 48 hours.
  const ageHours = (iso) => Math.max(0, (Date.now() - Date.parse(iso)) / 3600000);
  const trendWeight = (iso) => {
    const hours = ageHours(iso);
    return hours > 24 * 7 ? 0 : 0.5 ** (hours / 48);
  };
  function exploreTrends() {
    const gameScore = new Map();
    const reviewScore = new Map();
    const minutes = new Map();
    const add = (gameId, reviewId, weight) => {
      if (weight <= 0 || game(gameId)?.content_kind !== 'game') return;
      gameScore.set(gameId, (gameScore.get(gameId) || 0) + weight);
      reviewScore.set(reviewId, (reviewScore.get(reviewId) || 0) + weight);
    };
    for (const review of state.reviews) {
      const weight = trendWeight(review.created_at);
      add(review.game_id, review.id, weight);
      if (weight > 0 && review.verified_playtime_minutes) {
        minutes.set(review.game_id, (minutes.get(review.game_id) || 0) + review.verified_playtime_minutes);
      }
      add(review.game_id, review.id, weight * (review.like_count || 0));
    }
    for (const comment of state.comments) {
      const review = state.reviews.find((row) => row.id === comment.review_id);
      if (review) add(review.game_id, review.id, trendWeight(comment.created_at));
    }
    ensureShare();
    for (const row of shareRows()) {
      const review = state.reviews.find((item) => item.id === row.review_id);
      if (review) add(review.game_id, review.id, trendWeight(row.created_at));
    }
    const gamesOut = [...gameScore.keys()].sort((a, b) => (gameScore.get(b) - gameScore.get(a)) || ((minutes.get(b) || 0) - (minutes.get(a) || 0)) || a - b)
      .slice(0, 8).map((id) => {
        const item = game(id);
        return { game: item, genre: (item.genres || '').split(',')[0].trim(), hours: Math.floor((minutes.get(id) || 0) / 60), score: gameScore.get(id) };
      });
    const reviewsOut = [...reviewScore.keys()].sort((a, b) => (reviewScore.get(b) - reviewScore.get(a)) || b - a)
      .slice(0, 8).map((id) => feedItem(state.reviews.find((row) => row.id === id), 'Trending this week'));
    return { games: gamesOut, reviews: reviewsOut };
  }
  function explorePeople() {
    const owned = new Set((DATA.library || []).map((entry) => entry.game_id));
    const followed = new Set(state.signedIn ? followsOf() : []);
    const likes = new Map();
    const played = new Map();
    const active = new Set();
    for (const review of state.reviews) {
      if (trendWeight(review.created_at) <= 0 || game(review.game_id)?.content_kind !== 'game') continue;
      active.add(review.user_id);
      likes.set(review.user_id, (likes.get(review.user_id) || 0) + (review.like_count || 0));
      if (!played.has(review.user_id)) played.set(review.user_id, new Set());
      played.get(review.user_id).add(review.game_id);
    }
    return [...active].filter((id) => id !== ME.id && !followed.has(id)).map((id) => {
      const shared = [...(played.get(id) || [])].filter((gameId) => state.signedIn && owned.has(gameId)).length;
      return { id, display_name: author(id), handle: handleOf(id), badge: badgeOf(id), avatar_url: imageOf(id, 'avatar'), shared_games: shared,
        likes_received: likes.get(id) || 0, profile_visibility: 'public' };
    }).sort((a, b) => b.shared_games - a.shared_games || b.likes_received - a.likes_received || a.id - b.id).slice(0, 8);
  }

  // ---- the For you feed: owned games first, then shared genres, then newest ----
  function personalFeed(query) {
    const minutes = new Map();
    for (const entry of DATA.library) {
      const item = game(entry.game_id);
      if (item.content_kind !== 'game' || !entry.playtime_minutes) continue;
      for (const genre of (item.genres || '').split(',')) minutes.set(genre, (minutes.get(genre) || 0) + entry.playtime_minutes);
    }
    const favorite = new Set([...minutes].sort((a, b) => b[1] - a[1]).slice(0, 5).map(([genre]) => genre));
    return state.reviews
      .filter((review) => review.user_id !== ME.id && game(review.game_id).content_kind === 'game' && matches(review.game_id, query))
      .map((review) => {
        const shared = (game(review.game_id).genres || '').split(',').filter((genre) => favorite.has(genre));
        const mine = owned(review.game_id);
        const score = (mine ? 4 : 0) + Math.min(3, shared.length);
        const reason = mine ? 'In your game library' : shared.length ? `Because you play ${shared[0]} games` : 'New in the community';
        return { review, score, reason };
      })
      .sort((a, b) => b.score - a.score || newestFirst(a.review, b.review));
  }

  // Quotes always, and a plain repost once a review has been shared twice.
  function popularShares(query) {
    ensureShare();
    const seen = new Set();
    return shareRows().filter((row) => {
      if (row.user_id === ME.id) return false;
      const review = state.reviews.find((item) => item.id === row.review_id);
      if (!review || review.user_id === ME.id || game(review.game_id).content_kind !== 'game' || !matches(review.game_id, query)) return false;
      if (!row.quote && repostCount(review.id) < 2) return false;
      const key = row.quote ? `q${row.id}` : `p${review.id}`;
      if (seen.has(key)) return false;
      seen.add(key); return true;
    });
  }

  // Play statuses: a few to start with, two on games the demo player doesn't
  // own. Changes are kept in this tab like reviews are.
  const STATUS_VALUES = ['want_to_play', 'playing', 'completed', 'paused', 'dropped'];
  function startingStatuses() {
    const mine = DATA.library.map((entry) => entry.game_id);
    const other = [...games.values()].filter((item) => item.content_kind === 'game' && !mine.includes(item.id)).map((item) => item.id);
    return [[mine[0], 'completed'], [mine[1], 'playing'], [mine[2], 'paused'], [other[0], 'want_to_play'], [other[1], 'want_to_play']]
      .filter(([id]) => id != null)
      .map(([id, status], index) => ({ game_id: id, status, updated_at: ago(60 * 24 * (index + 1)),
        completed_at: status === 'completed' ? ago(60 * 24 * 9) : null }));
  }
  function statusRows() {
    if (!state.statuses) { state.statuses = startingStatuses(); save(); }
    return state.statuses;
  }
  const statusOut = ({ game_id, ...row }) => ({ game: game(game_id), ...row });
  function setStatus(gameId, init) {
    const denied = needSession(); if (denied) return denied;
    if (!game(gameId)) return missing('Game');
    const { status } = readBody(init);
    if (!STATUS_VALUES.includes(status)) return respond(422, { detail: 'Choose a status.' });
    const rows = statusRows(); let row = rows.find((item) => item.game_id === gameId);
    const now = new Date().toISOString();
    if (!row) { row = { game_id: gameId, completed_at: null }; rows.push(row); }
    row.completed_at = status !== 'completed' ? null : row.status === 'completed' ? row.completed_at : now;
    row.status = status; row.updated_at = now; save();
    return respond(200, statusOut(row));
  }

  // ---- profiles, follows, likes and favorites ----
  const followsOf = () => (state.following ||= []);
  const baseFollows = DATA.follows || [];
  const followerIds = (id) => [...new Set([...baseFollows.filter(([a, b]) => b === id && a !== ME.id).map(([a]) => a),
    ...(followsOf().includes(id) ? [ME.id] : [])])];
  const followingIds = (id) => (id === ME.id ? followsOf() : baseFollows.filter(([a]) => a === id).map(([, b]) => b));
  const card = (id) => ({ id, display_name: author(id), handle: handleOf(id), badge: badgeOf(id), avatar_url: imageOf(id, 'avatar') });
  function profileOut(id) {
    const user = users.get(id); if (!user) return null;
    const mine = state.reviews.filter((review) => review.user_id === id).sort(newestFirst);
    const top = [...mine].filter((r) => r.verified_playtime_minutes != null).sort((a, b) => b.verified_playtime_minutes - a.verified_playtime_minutes)[0];
    const pinned = id === ME.id ? (state.favorites || []) : [];
    const counts = { followers: followerIds(id).length, following: followingIds(id).length, reviews: mine.length,
      likes_received: mine.reduce((sum, review) => sum + likeCount(review), 0),
      verified_minutes: verifiedMinutes(id) };
    const statusCounts = Object.fromEntries(STATUS_VALUES.map((value) => [value, id === ME.id ? statusRows().filter((r) => r.status === value).length : 0]));
    return { id, display_name: user.display_name, handle: handleOf(id), badge: badgeOf(id), limited: false, profile_visibility: 'public',
      avatar_url: imageOf(id, 'avatar'), cover_url: imageOf(id, 'cover'),
      room: Object.fromEntries(['playing', 'completed', 'want_to_play'].map((status) => [status,
        id === ME.id ? statusRows().filter((row) => row.status === status).slice(0, 12).map((row) => game(row.game_id)).filter(Boolean) : []])),
      viewer: { signed_in: state.signedIn, is_self: id === ME.id, following: followsOf().includes(id), requested: false,
        follows_you: followingIds(id).includes(ME.id), blocked: false },
      counts, bio: id === ME.id ? (state.bio ?? user.bio) : user.bio, joined: ago(60 * 24 * (120 + id * 9)),
      recent_reviews: mine.slice(0, 10).map((review) => feedItem(review, '')), status_counts: statusCounts,
      favorites: pinned.map(game).filter(Boolean),
      banner_game: top ? game(top.game_id) : pinned.length ? game(pinned[0]) : null };
  }
  function likeState(id) {
    const review = state.reviews.find((row) => row.id === id);
    return { review_id: id, liked: liked().includes(id), like_count: review ? likeCount(review) : 0 };
  }

  const routes = [
    ['GET', /^\/me\/(follow-requests|blocks)$/, () => needSession() || respond(200, { items: [], total: 0 })],
    ['PUT', /^\/me\/profile-images\/(avatar|cover)$/, async ([kind], _, init) => {
      const denied = needSession(); if (denied) return denied;
      const file = init.body;
      if (!(file instanceof Blob) || file.size > 2 * 1024 * 1024 || !['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) return respond(422, { detail: 'Choose a JPEG, PNG or WebP under 2 MB.' });
      let image;
      try { image = await createImageBitmap(file); } catch { return respond(422, { detail: 'Choose a valid image.' }); }
      try {
        if (image.width * image.height > 16000000) return respond(422, { detail: 'Choose an image under 16 million pixels.' });
        const canvas = document.createElement('canvas'); canvas.width = kind === 'avatar' ? 256 : 1440; canvas.height = kind === 'avatar' ? 256 : 480;
        const scale = Math.max(canvas.width / image.width, canvas.height / image.height);
        canvas.getContext('2d').drawImage(image, (canvas.width - image.width * scale) / 2, (canvas.height - image.height * scale) / 2, image.width * scale, image.height * scale);
        const url = canvas.toDataURL('image/webp', 0.82);
        const before = state.images; state.images = { ...before, [kind]: url };
        try { sessionStorage.setItem(KEY, JSON.stringify(state)); }
        catch { state.images = before; return respond(413, { detail: 'This demo tab is full. Reset it or use a smaller image.' }); }
        return respond(200, { url });
      } finally { image.close(); }
    }],
    ['DELETE', /^\/me\/profile-images\/(avatar|cover)$/, ([kind]) => {
      const denied = needSession(); if (denied) return denied;
      if (state.images) delete state.images[kind]; save(); return respond(204);
    }],
    ['GET', /^\/me\/notifications$/, (_, params) => needSession() || respond(200, notificationInbox(params))],
    ['GET', /^\/me\/notifications\/unread$/, () => needSession() || respond(200, {
      unread_count: notificationRows().filter((row) => !row.read_at).length,
    })],
    ['POST', /^\/me\/notifications\/read$/, (_, params) => {
      const denied = needSession(); if (denied) return denied;
      const through = Number(params.get('through_id'));
      if (!params.has('through_id') || !Number.isInteger(through) || through < 0 || through > 2147483647) return respond(422, { detail: 'Choose a notification snapshot.' });
      const rows = notificationRows();
      for (const row of rows) if (row.id <= through && !row.read_at) row.read_at = new Date().toISOString();
      save(); return respond(200, { unread_count: rows.filter((row) => !row.read_at).length });
    }],
    ['GET', /^\/auth\/session$/, () => needSession() || respond(200, {
      user: { ...ME, handle: handleOf(ME.id), avatar_url: imageOf(ME.id, 'avatar') }, auth_provider: 'clerk', has_steam: true, psn_enabled: false, has_psn: false,
      csrf_token: 'demo', expires_at: Math.floor(Date.now() / 1000) + 12 * 3600 })],
    ['POST', /^\/auth\/logout$/, () => { state.signedIn = false; save(); return respond(204); }],

    ['GET', /^\/games$/, (_, params) => {
      const kind = params.get('kind') || 'game';
      const rows = [...games.values()].filter((item) => item.content_kind === kind && matches(item.id, params.get('q')));
      const sort = params.get('sort') || 'name';
      if (sort === 'reviewed') {
        const counts = new Map();
        for (const review of state.reviews) counts.set(review.game_id, (counts.get(review.game_id) || 0) + 1);
        rows.sort((a, b) => (counts.get(b.id) || 0) - (counts.get(a.id) || 0) || a.name.localeCompare(b.name) || a.id - b.id);
      } else if (sort === 'newest') {
        rows.sort((a, b) => String(b.first_release_date || '').localeCompare(String(a.first_release_date || '')) || a.name.localeCompare(b.name) || a.id - b.id);
      } else {
        rows.sort((a, b) => a.name.localeCompare(b.name) || a.id - b.id);
      }
      return respond(200, { total: rows.length, games: page(rows, params, 48) });
    }],
    ['GET', /^\/games\/(\d+)$/, ([id]) => game(+id) ? respond(200, { ...game(+id), summary: null, platforms: [] }) : missing('Game')],
    ['GET', /^\/games\/(\d+)\/reviews$/, ([id], params) => {
      const rows = state.reviews.filter((review) => review.game_id === +id).sort((a, b) =>
        (b.verified_playtime_minutes ?? -1) - (a.verified_playtime_minutes ?? -1) || newestFirst(a, b));
      return respond(200, page(rows, params).map(reviewOut));
    }],
    ['POST', /^\/games\/(\d+)\/reviews$/, ([id], _, init) => {
      const denied = needSession(); if (denied) return denied;
      if (!game(+id)) return missing('Game');
      if (state.reviews.some((review) => review.game_id === +id && review.user_id === ME.id)) {
        return respond(409, { detail: 'You already reviewed this game. Edit your review instead.' });
      }
      const { rating, body } = readBody(init); const text = cleanText(body, 10000);
      if (!validRating(rating) || text === null) return respond(422, { detail: 'Choose half a star to five stars, and keep the text under 10,000 characters.' });
      const mine = owned(+id);
      const review = { id: state.nextReview++, user_id: ME.id, game_id: +id, rating, body: text || null, created_at: new Date().toISOString(),
        verified_playtime_minutes: mine ? mine.playtime_minutes : null,
        verified_achievement_pct: mine && mine.achievements_total ? Math.round(1000 * mine.achievements_unlocked / mine.achievements_total) / 10 : null };
      state.reviews.push(review); save();
      return respond(201, reviewOut(review));
    }],
    ['GET', /^\/me\/games\/(\d+)\/review$/, ([id]) => {
      const denied = needSession(); if (denied) return denied;
      const review = state.reviews.find((row) => row.game_id === +id && row.user_id === ME.id);
      return respond(200, review ? reviewOut(review) : null);
    }],
    ['GET', /^\/reviews\/(\d+)$/, ([id]) => {
      const review = state.reviews.find((row) => row.id === +id);
      return review ? respond(200, { review: reviewOut(review), game: game(review.game_id) }) : missing('Review');
    }],
    ['PATCH', /^\/reviews\/(\d+)$/, ([id], _, init) => {
      const denied = needSession(); if (denied) return denied;
      const review = state.reviews.find((row) => row.id === +id && row.user_id === ME.id);
      if (!review) return missing('Review');
      const { rating, body } = readBody(init); const text = cleanText(body, 10000);
      if (!validRating(rating) || text === null) return respond(422, { detail: 'Choose half a star to five stars, and keep the text under 10,000 characters.' });
      Object.assign(review, { rating, body: text || null }); save();
      return respond(200, reviewOut(review));
    }],
    ['DELETE', /^\/reviews\/(\d+)$/, ([id]) => {
      const denied = needSession(); if (denied) return denied;
      if (!state.reviews.some((row) => row.id === +id && row.user_id === ME.id)) return missing('Review');
      state.reviews = state.reviews.filter((row) => row.id !== +id);
      state.comments = state.comments.filter((row) => row.review_id !== +id);
      state.reposted = (state.reposted || []).filter((other) => other !== +id);
      state.bookmarked = (state.bookmarked || []).filter((other) => other !== +id);
      if (state.quotes) state.quotes = state.quotes.filter((row) => row.review_id !== +id);
      if (state.plainReposts) state.plainReposts = state.plainReposts.filter((row) => row.review_id !== +id);
      save();
      return respond(204);
    }],
    ['GET', /^\/reviews\/(\d+)\/comments$/, ([id], params) => {
      const rows = state.comments.filter((comment) => comment.review_id === +id)
        .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id);
      return respond(200, page(rows, params).map(commentOut));
    }],
    ['POST', /^\/reviews\/(\d+)\/comments$/, ([id], _, init) => {
      const denied = needSession(); if (denied) return denied;
      if (!state.reviews.some((row) => row.id === +id)) return missing('Review');
      const text = cleanText(readBody(init).body, 5000);
      if (!text) return respond(422, { detail: 'Write a comment first.' });
      const comment = { id: state.nextComment++, review_id: +id, user_id: ME.id, body: text, created_at: new Date().toISOString() };
      state.comments.push(comment); save();
      return respond(201, commentOut(comment));
    }],

    ['GET', /^\/feed$/, (_, params) => {
      const rows = state.reviews.filter((review) => game(review.game_id).content_kind === 'game' && matches(review.game_id, params.get('q')))
        .sort(newestFirst);
      const before = Math.max(0, ...state.reviews.map((review) => review.id));
      return respond(200, { items: page(rows, params).map((review) => feedItem(review, 'New in the community')),
        total: rows.length, before, mode: 'latest' });
    }],
    ['GET', /^\/me\/feed$/, (_, params) => {
      const denied = needSession(); if (denied) return denied;
      const rows = personalFeed(params.get('q') || '');
      const before = Math.max(0, ...state.reviews.map((review) => review.id));
      const query = params.get('q') || '';
      const shares = popularShares(query).map((row) => {
        const review = state.reviews.find((item) => item.id === row.review_id);
        return { review, score: 4, repost: row,
          reason: row.quote ? `${author(row.user_id)} quoted` : `${author(row.user_id)} reposted` };
      });
      const merged = [...rows, ...shares].sort((a, b) => b.score - a.score || newestFirst(a.review, b.review));
      return respond(200, { items: page(merged, params).map(({ review, reason, repost }) => feedItem(review, reason, repost)),
        total: merged.length, before, mode: 'for_you', candidate_limit: 500, library_before: 0 });
    }],
    ['GET', /^\/me\/following-feed$/, (_, params) => {
      const denied = needSession(); if (denied) return denied;
      ensureShare();
      const followed = new Set(followsOf());
      const query = params.get('q') || '';
      const events = [];
      for (const review of state.reviews) {
        if (!followed.has(review.user_id) || game(review.game_id).content_kind !== 'game' || !matches(review.game_id, query)) continue;
        events.push({ at: review.created_at, id: review.id, review, repost: null, reason: 'From someone you follow' });
      }
      for (const row of shareRows()) {
        if (!followed.has(row.user_id)) continue;
        const review = state.reviews.find((item) => item.id === row.review_id);
        if (!review || !matches(review.game_id, query)) continue;
        events.push({ at: row.created_at, id: row.id, review, repost: row,
          reason: row.quote ? `${author(row.user_id)} quoted` : `${author(row.user_id)} reposted` });
      }
      events.sort((a, b) => b.at.localeCompare(a.at) || b.id - a.id);
      const before = Math.max(0, ...state.reviews.map((review) => review.id));
      const repostBefore = Math.max(0, ...shareRows().map((row) => row.id));
      return respond(200, { items: page(events, params).map((event) => feedItem(event.review, event.reason, event.repost)),
        total: events.length, before, repost_before: repostBefore, mode: 'following' });
    }],
    ['GET', /^\/me\/library$/, () => needSession() || respond(200, library())],
    // A few sample play statuses, including two games the demo player doesn't own.
    ['GET', /^\/users\/by-handle\/([^/]+)$/, ([handle]) => {
      const wanted = decodeURIComponent(handle).replace(/^@/, '').toLowerCase();
      const found = DATA.users.find((user) => handleOf(user.id) === wanted);
      return found ? respond(200, profileOut(found.id)) : missing('User');
    }],
    ['GET', /^\/users\/(\d+)$/, ([id]) => { const out = profileOut(+id); return out ? respond(200, out) : missing('User'); }],
    ['GET', /^\/users\/(\d+)\/(followers|following)$/, ([id, kind], params) => {
      const denied = needSession(); if (denied) return denied;
      const ids = kind === 'followers' ? followerIds(+id) : followingIds(+id);
      return respond(200, { items: page(ids.map(card), params), total: ids.length });
    }],
    ['POST', /^\/users\/(\d+)\/follow$/, ([id]) => {
      const denied = needSession(); if (denied) return denied;
      if (+id === ME.id || !users.has(+id)) return missing('User');
      if (!followsOf().includes(+id)) followsOf().push(+id); save();
      return respond(200, { following: true, requested: false });
    }],
    ['DELETE', /^\/users\/(\d+)\/follow$/, ([id]) => {
      const denied = needSession(); if (denied) return denied;
      state.following = followsOf().filter((other) => other !== +id); save(); return respond(204);
    }],
    ['PATCH', /^\/me\/profile$/, (_, __, init) => {
      const denied = needSession(); if (denied) return denied;
      const body = readBody(init);
      if (body.handle != null) {
        const handle = String(body.handle).trim().replace(/^@/, '').toLowerCase();
        if (!/^(?=[a-z0-9_]*[a-z])[a-z0-9_]{3,15}$/.test(handle)) return respond(422, { detail: 'Use 3 to 15 letters, numbers or underscores, with at least one letter.' });
        if (DATA.users.some((user) => user.id !== ME.id && user.handle === handle)) return respond(409, { detail: 'That handle is taken. Try another.' });
        state.handle = handle;
      }
      if (body.bio !== undefined) { const text = cleanText(body.bio, 280); if (text === null) return respond(422, { detail: 'Keep your bio to 280 characters.' }); state.bio = text || null; }
      save(); return respond(200, profileOut(ME.id));
    }],
    ['PUT', /^\/me\/favorites$/, (_, __, init) => {
      const denied = needSession(); if (denied) return denied;
      const ids = readBody(init).game_ids;
      if (!Array.isArray(ids) || ids.length > 4 || new Set(ids).size !== ids.length) return respond(422, { detail: 'Pin up to four different games.' });
      if (!ids.every((id) => games.has(id))) return missing('Game');
      state.favorites = ids; save(); return respond(200, { favorites: ids.map(game) });
    }],
    ['GET', /^\/me\/likes$/, (_, params) => {
      const denied = needSession(); if (denied) return denied;
      const asked = new Set((params.get('review_ids') || '').split(',').filter(Boolean).map(Number));
      return respond(200, { liked: liked().filter((id) => asked.has(id)).sort((a, b) => a - b) });
    }],
    ['POST', /^\/reviews\/(\d+)\/like$/, ([id]) => {
      const denied = needSession(); if (denied) return denied;
      if (!state.reviews.some((row) => row.id === +id)) return missing('Review');
      if (!liked().includes(+id)) liked().push(+id); save(); return respond(200, likeState(+id));
    }],
    ['DELETE', /^\/reviews\/(\d+)\/like$/, ([id]) => {
      const denied = needSession(); if (denied) return denied;
      state.liked = liked().filter((other) => other !== +id); save(); return respond(200, likeState(+id));
    }],
    ['GET', /^\/me\/reposts$/, (_, params) => {
      const denied = needSession(); if (denied) return denied;
      ensureShare();
      const asked = new Set((params.get('review_ids') || '').split(',').filter(Boolean).map(Number));
      return respond(200, { reposted: state.reposted.filter((id) => asked.has(id)).sort((a, b) => a - b) });
    }],
    ['POST', /^\/reviews\/(\d+)\/repost$/, ([id], _, init) => {
      const denied = needSession(); if (denied) return denied;
      ensureShare();
      const review = state.reviews.find((row) => row.id === +id);
      if (!review) return missing('Review');
      if (review.user_id === ME.id) return respond(422, { detail: "You can't repost your own review." });
      const body = readBody(init);
      const text = cleanedQuote(body.quote);
      if (text === false) return respond(422, { detail: 'Keep a quote to 280 characters.' });
      if (text) {
        const row = { id: state.nextRepost++, user_id: ME.id, review_id: review.id, quote: text, created_at: new Date().toISOString() };
        state.quotes.push(row); save();
        return respond(200, repostState(review.id, row.id));
      }
      if (!state.reposted.includes(review.id)) state.reposted.push(review.id);
      save(); return respond(200, repostState(review.id));
    }],
    ['DELETE', /^\/reviews\/(\d+)\/repost$/, ([id]) => {
      const denied = needSession(); if (denied) return denied;
      ensureShare();
      state.reposted = state.reposted.filter((other) => other !== +id); save();
      return respond(200, repostState(+id));
    }],
    ['DELETE', /^\/reposts\/(\d+)$/, ([id]) => {
      const denied = needSession(); if (denied) return denied;
      ensureShare();
      const row = state.quotes.find((item) => item.id === +id && item.user_id === ME.id);
      if (!row) return missing('Repost');
      state.quotes = state.quotes.filter((item) => item !== row); save();
      return respond(200, repostState(row.review_id));
    }],
    ['GET', /^\/me\/bookmarks$/, (_, params) => {
      const denied = needSession(); if (denied) return denied;
      ensureShare();
      if (params.has('review_ids')) {
        const asked = new Set((params.get('review_ids') || '').split(',').filter(Boolean).map(Number));
        return respond(200, { bookmarked: state.bookmarked.filter((id) => asked.has(id)).sort((a, b) => a - b) });
      }
      const rows = state.bookmarked.map((id) => state.reviews.find((review) => review.id === id)).filter(Boolean);
      return respond(200, { items: page(rows, params).map((review) => feedItem(review, 'Saved by you')), total: rows.length });
    }],
    ['POST', /^\/reviews\/(\d+)\/bookmark$/, ([id]) => {
      const denied = needSession(); if (denied) return denied;
      ensureShare();
      if (!state.reviews.some((row) => row.id === +id)) return missing('Review');
      if (!state.bookmarked.includes(+id)) state.bookmarked.unshift(+id);
      save(); return respond(200, { review_id: +id, bookmarked: true });
    }],
    ['DELETE', /^\/reviews\/(\d+)\/bookmark$/, ([id]) => {
      const denied = needSession(); if (denied) return denied;
      ensureShare();
      state.bookmarked = state.bookmarked.filter((other) => other !== +id); save();
      return respond(200, { review_id: +id, bookmarked: false });
    }],
    ['POST', /^\/reports$/, () => needSession() || respond(201, { status: 'open' })],
    ['GET', /^\/me\/statuses$/, (_, params) => needSession() || respond(200, [...statusRows()]
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
      .filter((row) => !params.get('status') || row.status === params.get('status')).map(statusOut))],
    ['PUT', /^\/me\/games\/(\d+)\/status$/, ([id], _, init) => setStatus(Number(id), init)],
    ['DELETE', /^\/me\/games\/(\d+)\/status$/, ([id]) => {
      const denied = needSession(); if (denied) return denied;
      state.statuses = statusRows().filter((row) => row.game_id !== Number(id)); save();
      return respond(204);
    }],
    // Syncing: queued, a few seconds "in progress", then done.
    ['POST', /^\/me\/sync$/, () => {
      const denied = needSession(); if (denied) return denied;
      const job = `demo-sync-${Date.now()}`; state.syncs[job] = 0; save();
      return respond(200, { job_id: job, status: 'queued' });
    }],
    ['GET', /^\/me\/sync\/status\/(.+)$/, ([job]) => {
      const id = decodeURIComponent(job);
      if (!(id in state.syncs)) return respond(200, { job_id: id, status: 'not_found', result: null });
      state.syncs[id] += 1;
      if (state.syncs[id] < 2) { save(); return respond(200, { job_id: id, status: 'in_progress', result: null }); }
      delete state.syncs[id]; state.capturedAt = new Date().toISOString(); save();
      return respond(200, { job_id: id, status: 'complete', result: { games_synced: DATA.library.length } });
    }],
    ['GET', /^\/explore$/, () => respond(200, exploreTrends())],
    ['GET', /^\/explore\/suggestions$/, () => respond(200, { items: explorePeople() })],
    ['GET', /^\/news$/, () => respond(200, {
      next: null,
      items: DATA.news.map((item) => ({ title: item.title, summary: item.summary, source: 'PlayGraph demo',
        url: `https://github.com/JakeeUp/playgraph#${item.anchor}`, published_at: ago(item.minutes_ago),
        image: `https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/${item.image_appid}/header.jpg` })),
    })],
  ];

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    if (url.origin !== location.origin || !API.test(url.pathname)) return realFetch(input, init);
    const method = (init.method || 'GET').toUpperCase();
    // A short pause, so loading states behave the way they do against a server.
    await new Promise((resolve) => setTimeout(resolve, 80 + Math.random() * 120));
    for (const [verb, pattern, handle] of routes) {
      const match = pattern.exec(url.pathname);
      if (match && verb === method) return handle(match.slice(1), url.searchParams, init);
    }
    return respond(404, { detail: 'Not Found' });
  };

  // Controls on the demo banner and the demo account page.
  window.playgraphDemo = {
    reset() { try { sessionStorage.removeItem(KEY); } catch { /* nothing saved */ } state = fresh(); },
    signIn() { state.signedIn = true; save(); },
    signOut() { state.signedIn = false; save(); },
  };
  document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('profile-section')) {
      import('./profile-account.js').then(async ({ initProfile }) => {
        const response = await window.fetch('/auth/session');
        if (response.ok) await initProfile(await response.json());
      }).catch(() => { document.getElementById('profile-status').textContent = 'Could not load profile settings. Refresh to try again.'; });
    }
    document.getElementById('demo-reset')?.addEventListener('click', () => { window.playgraphDemo.reset(); location.reload(); });
    document.getElementById('demo-sign-in')?.addEventListener('click', () => { window.playgraphDemo.signIn(); location.assign('./'); });
    document.getElementById('demo-sign-out')?.addEventListener('click', (event) => {
      event.preventDefault(); window.playgraphDemo.signOut(); location.assign('./');
    });
  });
})();
