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
  const API = /^\/(auth\/|games(\/|$)|me\/|feed$|reviews\/|news$)/;

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
    };
  }
  let state;
  try { state = JSON.parse(sessionStorage.getItem(KEY)) || fresh(); } catch { state = fresh(); }
  const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify(state)); } catch { /* private mode: keep it in memory */ } };

  // ---- shapes the real API returns ----
  const game = (id) => games.get(id);
  const author = (id) => users.get(id)?.display_name || `Player ${id}`;
  const reviewOut = (review) => ({ ...review, author_name: author(review.user_id),
    verified_source: review.verified_playtime_minutes == null ? null : 'steam' });
  const commentOut = (comment) => ({ ...comment, author_name: author(comment.user_id) });
  const library = () => DATA.library.map((entry) => ({ game: game(entry.game_id), source: 'steam',
    playtime_minutes: entry.playtime_minutes, achievements_unlocked: entry.achievements_unlocked,
    achievements_total: entry.achievements_total, trophies: null, captured_at: state.capturedAt }));
  const owned = (gameId) => DATA.library.find((entry) => entry.game_id === gameId);
  const newestFirst = (a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id;
  const commentCount = (reviewId) => state.comments.filter((comment) => comment.review_id === reviewId).length;
  const feedItem = (review, reason) => ({ review: reviewOut(review), game: game(review.game_id),
    comment_count: commentCount(review.id), reason });

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

  const routes = [
    ['GET', /^\/auth\/session$/, () => needSession() || respond(200, {
      user: ME, auth_provider: 'clerk', has_steam: true, psn_enabled: false, has_psn: false,
      csrf_token: 'demo', expires_at: Math.floor(Date.now() / 1000) + 12 * 3600 })],
    ['POST', /^\/auth\/logout$/, () => { state.signedIn = false; save(); return respond(204); }],

    ['GET', /^\/games$/, (_, params) => {
      const kind = params.get('kind') || 'game';
      const rows = [...games.values()].filter((item) => item.content_kind === kind && matches(item.id, params.get('q')))
        .sort((a, b) => a.name.localeCompare(b.name) || a.id - b.id);
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
      state.comments = state.comments.filter((row) => row.review_id !== +id); save();
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
      return respond(200, { items: page(rows, params).map(({ review, reason }) => feedItem(review, reason)),
        total: rows.length, before, mode: 'for_you', candidate_limit: 500, library_before: 0 });
    }],
    ['GET', /^\/me\/library$/, () => needSession() || respond(200, library())],
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
    document.getElementById('demo-reset')?.addEventListener('click', () => { window.playgraphDemo.reset(); location.reload(); });
    document.getElementById('demo-sign-in')?.addEventListener('click', () => { window.playgraphDemo.signIn(); location.assign('./'); });
    document.getElementById('demo-sign-out')?.addEventListener('click', (event) => {
      event.preventDefault(); window.playgraphDemo.signOut(); location.assign('./');
    });
  });
})();
