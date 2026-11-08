// Request plumbing shared by every page: the same-origin fetch defaults, the
// CSRF and auth header names, and the one place that words a failed response.
// No DOM here, so the pure parts are unit tested.

export const CSRF_HEADER = 'X-CSRF-Token';
export const AUTH_HEADER = 'X-PlayGraph-Auth';

const GENERIC = 'This request could not be completed. Please try again.';
const SECURITY_DOWN = 'PlayGraph cannot reach its security service. Check that Redis is available, then refresh.';

/**
 * A failed response as a sentence a player can act on. Server detail wins over
 * the canned 503 and fallback text; a 429 always names the wait.
 * `unavailable` and `fallback` let a page word the 503 and unknown cases.
 */
export function failureMessage(status, detail, retryAfter, { unavailable = SECURITY_DOWN, fallback = GENERIC } = {}) {
  if (status === 429) return `Too many requests. Try again in ${retryAfter || '60'} seconds.`;
  if (typeof detail === 'string' && detail) return detail;
  return status === 503 ? unavailable : fallback;
}

/** fetch with the project defaults. `csrf` (a token, even an empty one) adds the CSRF header. */
export function sendRequest(path, { csrf, ...init } = {}) {
  const headers = new Headers(init.headers);
  if (init.body) headers.set('Content-Type', 'application/json');
  if (csrf !== undefined) headers.set(CSRF_HEADER, csrf);
  return fetch(path, { ...init, headers, credentials: 'same-origin', cache: 'no-store' });
}

/** The JSON body, or null for 204 and anything that is not JSON. */
export const readBody = (response) => response.status === 204 ? Promise.resolve(null) : response.json().catch(() => null);

/** An Error for a failed response, carrying its status. */
export function failureOf(response, body, messages) {
  const error = new Error(failureMessage(response.status, body?.detail, response.headers.get('Retry-After'), messages));
  error.status = response.status;
  return error;
}

/** The current session, or null when signed out. */
export async function currentSession() {
  const response = await sendRequest('/auth/session');
  if (response.status === 401) return null;
  if (!response.ok) throw new Error('Unable to check your current account. Please try again.');
  return response.json();
}

/** Send, read and throw on failure. For callers with nothing to do between those steps. */
export async function requestJson(path, init, messages) {
  const response = await sendRequest(path, init);
  const body = await readBody(response);
  if (!response.ok) throw failureOf(response, body, messages);
  return body;
}
