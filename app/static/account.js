import { $ } from './dom.js';
import { initPlayStation } from './psn-account.js';
import { sendRequest, readBody, failureOf, currentSession, AUTH_HEADER } from './net.js';

const status = $('#account-status');
const signIn = $('#clerk-sign-in');
let clerk;
let signInMounted = false;
let linking = false;
let playgraph = null;
// Where a Steam connection ended, when it did not end in the app.
const STEAM_OUTCOMES = {
  'in-use': 'That Steam account already belongs to a PlayGraph account. If it’s yours, sign in with Steam, then connect your sign-in from this page.',
  already: 'Steam is already connected to this account.',
  failed: 'The Steam connection didn’t finish. Please try again.',
};
const steamOutcome = STEAM_OUTCOMES[new URLSearchParams(location.search).get('steam')] || '';
// Clerk draws its own form, so it gets the page's colors and type from the CSS tokens.
const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
if (steamOutcome) history.replaceState(null, '', '/account');

function report(message, retry = false) {
  status.textContent = message;
  $('#retry-account').hidden = !retry;
}

function isSteam(session) {
  return session && session.auth_provider !== 'clerk';
}

function showSteam() {
  linking = false;
  $('#link-context').hidden = true;
  $('#legacy-session').hidden = false;
  $('#ready-session').hidden = true;
  $('#steam-access').hidden = true;
  if (signInMounted) clerk.unmountSignIn(signIn);
  signInMounted = false;
  document.title = 'Your account | PlayGraph';
  report('Your Steam beta account is still signed in.');
}

async function endPlayGraphSession() {
  const session = await currentSession();
  if (!session) return;
  const response = await sendRequest('/auth/logout', { method: 'POST', csrf: session.csrf_token });
  if (!response.ok && response.status !== 401) throw new Error('Sign-out failed. Please try again.');
}

function loadScript(src, publishableKey) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.async = true;
    script.crossOrigin = 'anonymous';
    if (publishableKey) script.dataset.clerkPublishableKey = publishableKey;
    const timeout = setTimeout(() => {
      script.remove();
      reject(new Error('Sign-in is taking too long to load. Please try again.'));
    }, 20000);
    script.onload = () => { clearTimeout(timeout); resolve(); };
    script.onerror = () => {
      clearTimeout(timeout);
      reject(new Error('The sign-in service could not load. Please try again.'));
    };
    document.head.append(script);
  });
}

function preloadScript(src) {
  const link = document.createElement('link');
  link.rel = 'preload'; link.as = 'script'; link.crossOrigin = 'anonymous'; link.href = src;
  document.head.append(link);
}

function renderProviderSession() {
  const signedIn = Boolean(clerk.isSignedIn && clerk.session);
  document.title = signedIn ? 'Your account | PlayGraph' : 'Sign in | PlayGraph';
  $('#ready-session').hidden = !signedIn;
  // Prevent an existing app session being replaced by a Steam login from this page.
  $('#steam-access').hidden = signedIn || linking;
  $('#link-confirmation').hidden = !linking;
  $('#continue-account').textContent = linking ? 'Connect and keep my library' : 'Continue to PlayGraph';
  $('#sign-out-account').textContent = linking ? 'Use a different sign-in / verify again' : 'Sign out';
  $('#confirm-link').checked = false;
  // Only an account that signed up without Steam, already in PlayGraph, can connect it here.
  $('#steam-connect').hidden = !(signedIn && !linking && playgraph?.auth_provider === 'clerk' && !playgraph.has_steam);
  if (signedIn) {
    if (signInMounted) clerk.unmountSignIn(signIn);
    signInMounted = false;
    $('#signed-in-name').textContent = clerk.user?.primaryEmailAddress?.emailAddress || clerk.user?.username || clerk.user?.firstName || 'You’re signed in. Continue when you’re ready.';
  } else if (!signInMounted) {
    clerk.mountSignIn(signIn, { routing: 'hash', withSignUp: true,
      forceRedirectUrl: '/account', signUpForceRedirectUrl: '/account' });
    signInMounted = true;
  }
  report(steamOutcome);
}

async function start() {
  // The settings and the session do not depend on each other, so ask for both
  // at once. A session failure is only reported if sign-in is enabled.
  const sessionRequest = currentSession();
  sessionRequest.catch(() => {});
  // PlayStation linking works for any signed-in account, with or without Clerk.
  sessionRequest.then((session) => { if (session) void initPlayStation(session); }, () => {});
  const response = await sendRequest('/auth/config');
  if (!response.ok) throw new Error('Sign-in settings could not load. Please try again.');
  const config = await response.json();
  if (!config.enabled) {
    report('PlayGraph account sign-in is not enabled on this installation yet. Your Steam beta account is still available.');
    return;
  }
  const session = await sessionRequest;
  playgraph = session;
  if (isSteam(session)) {
    const pending = await sendRequest('/auth/clerk/link');
    if (!pending.ok) throw new Error('Unable to check your account connection.');
    linking = (await pending.json()).pending;
    if (!linking) { showSteam(); return; }
    $('#legacy-session').hidden = true;
    $('#link-context').hidden = false;
    $('#steam-access').hidden = true;
    $('#link-owner').textContent = `You are connecting the Steam library belonging to ${session.user.display_name}.`;
  }
  if (clerk) { renderProviderSession(); return; }
  const api = new URL(config.frontend_api);
  if (api.protocol !== 'https:' || api.origin !== config.frontend_api || !config.publishable_key) {
    throw new Error('Account sign-in is not configured correctly.');
  }
  const clerkScript = `${api.origin}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`;
  // Download the second script while the first loads; they still run in order.
  preloadScript(clerkScript);
  await loadScript(`${api.origin}/npm/@clerk/ui@1/dist/ui.browser.js`);
  await loadScript(clerkScript, config.publishable_key);
  clerk = window.Clerk;
  await clerk.load({
    ui: { ClerkUI: window.__internal_ClerkUICtor }, telemetry: false,
    signInUrl: '/account', signUpUrl: '/account',
    signInForceRedirectUrl: '/account', signUpForceRedirectUrl: '/account',
    appearance: { variables: {
      colorPrimary: token('--accent'), colorPrimaryForeground: token('--on-accent'), colorBackground: token('--surface'),
      colorForeground: token('--text'), colorMutedForeground: token('--text-muted'), colorInput: token('--bg'),
      colorInputForeground: token('--text'), colorBorder: token('--line'), colorDanger: token('--danger'),
      colorShadow: 'transparent', fontFamily: token('--font-sans'), borderRadius: token('--radius'),
    } },
  });
  clerk.addListener(renderProviderSession);
}

$('#retry-account').addEventListener('click', () => location.reload());
async function linkRequest(path, method, token) {
  const session = await currentSession();
  if (!isSteam(session)) throw new Error('Your Steam session ended. Sign in with Steam again before connecting.');
  const response = await sendRequest(path, { method, csrf: session.csrf_token, headers: token ? { 'X-Clerk-Token': token } : {} });
  if (!response.ok) {
    const fallback = 'Connection could not finish. Please try again.';
    throw failureOf(response, await readBody(response), { unavailable: fallback, fallback });
  }
}
$('#start-link').addEventListener('click', async (event) => {
  event.currentTarget.disabled = true;
  try { await linkRequest('/auth/clerk/link', 'POST'); await start(); }
  catch (error) { report(error.message, true); }
  finally { $('#start-link').disabled = false; }
});
$('#cancel-link').addEventListener('click', async (event) => {
  event.currentTarget.disabled = true;
  try { await linkRequest('/auth/clerk/link', 'DELETE'); location.reload(); }
  catch (error) { report(error.message, true); $('#cancel-link').disabled = false; }
});
$('#leave-steam').addEventListener('click', async (event) => {
  event.currentTarget.disabled = true;
  try { await endPlayGraphSession(); location.reload(); }
  catch (error) { report(error.message); $('#leave-steam').disabled = false; }
});
$('#continue-account').addEventListener('click', async (event) => {
  event.currentTarget.disabled = true;
  try {
    if (linking && !$('#confirm-link').checked) throw new Error('Confirm that you want to connect this sign-in to your Steam library.');
    if (!linking && isSteam(await currentSession())) { showSteam(); return; }
    const token = await clerk.session?.getToken({ skipCache: true });
    if (!token) throw new Error('Your sign-in expired. Please sign in again.');
    if (linking) {
      await linkRequest('/auth/clerk/link/complete', 'POST', token);
      location.assign('/app');
      return;
    }
    const response = await sendRequest('/auth/clerk/session', {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, [AUTH_HEADER]: '1' },
    });
    if (!response.ok) {
      throw new Error(response.status === 409
        ? 'Another PlayGraph account is signed in. Sign out before switching accounts.'
        : 'PlayGraph could not verify your sign-in. Please try again.');
    }
    location.assign('/app');
  } catch (error) { report(error.message); }
  finally { $('#continue-account').disabled = false; }
});
$('#connect-steam').addEventListener('click', async (event) => {
  event.currentTarget.disabled = true;
  try {
    const session = await currentSession();
    if (session?.auth_provider !== 'clerk') throw new Error('Continue to PlayGraph first, then connect Steam.');
    const token = await clerk.session?.getToken({ skipCache: true });
    if (!token) throw new Error('Your sign-in expired. Please sign in again.');
    const response = await sendRequest('/auth/steam/connect', {
      method: 'POST', csrf: session.csrf_token, headers: { 'X-Clerk-Token': token },
    });
    const body = await readBody(response);
    if (!response.ok) {
      const fallback = 'Steam connection could not start. Please try again.';
      throw failureOf(response, body, { unavailable: fallback, fallback });
    }
    const target = new URL(body.redirect);
    if (target.origin !== 'https://steamcommunity.com') throw new Error('Steam connection could not start. Please try again.');
    location.assign(target.href);
  } catch (error) { report(error.message); $('#connect-steam').disabled = false; }
});
$('#sign-out-account').addEventListener('click', async (event) => {
  event.currentTarget.disabled = true;
  try {
    if (!linking) await endPlayGraphSession();
    await clerk.signOut({ redirectUrl: '/account' });
  } catch {
    report('Sign-out could not finish. Please try again.');
    $('#sign-out-account').disabled = false;
  }
});

start().catch(() => report('Account sign-in could not load. Check your connection and try again.', true));
