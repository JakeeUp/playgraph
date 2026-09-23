import { $ } from './dom.js';

const status = $('#account-status');
const signIn = $('#clerk-sign-in');
let clerk;
let signInMounted = false;
let linking = false;

function report(message, retry = false) {
  status.textContent = message;
  $('#retry-account').hidden = !retry;
}

async function currentSession() {
  const response = await fetch('/auth/session', { credentials: 'same-origin', cache: 'no-store' });
  if (response.status === 401) return null;
  if (!response.ok) throw new Error('Unable to check your current account. Please try again.');
  return response.json();
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
  report('Your Steam beta account is still signed in.');
}

async function endPlayGraphSession() {
  const session = await currentSession();
  if (!session) return;
  const response = await fetch('/auth/logout', {
    method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { 'X-CSRF-Token': session.csrf_token },
  });
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

function renderProviderSession() {
  const signedIn = Boolean(clerk.isSignedIn && clerk.session);
  $('#ready-session').hidden = !signedIn;
  // Prevent an existing app session being replaced by a Steam login from this page.
  $('#steam-access').hidden = signedIn || linking;
  $('#link-confirmation').hidden = !linking;
  $('#continue-account').textContent = linking ? 'Connect and keep my library' : 'Continue to PlayGraph';
  $('#sign-out-account').textContent = linking ? 'Use a different sign-in / verify again' : 'Sign out';
  $('#confirm-link').checked = false;
  if (signedIn) {
    if (signInMounted) clerk.unmountSignIn(signIn);
    signInMounted = false;
    $('#signed-in-name').textContent = clerk.user?.primaryEmailAddress?.emailAddress || clerk.user?.username || clerk.user?.firstName || 'You’re signed in. Continue when you’re ready.';
  } else if (!signInMounted) {
    clerk.mountSignIn(signIn, { routing: 'hash', withSignUp: true,
      forceRedirectUrl: '/account', signUpForceRedirectUrl: '/account' });
    signInMounted = true;
  }
  report('');
}

async function start() {
  const response = await fetch('/auth/config', { credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok) throw new Error('Sign-in settings could not load. Please try again.');
  const config = await response.json();
  if (!config.enabled) {
    report('PlayGraph account sign-in is not enabled on this installation yet. Your Steam beta account is still available.');
    return;
  }
  const session = await currentSession();
  if (isSteam(session)) {
    const pending = await fetch('/auth/clerk/link', { credentials: 'same-origin', cache: 'no-store' });
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
  await loadScript(`${api.origin}/npm/@clerk/ui@1/dist/ui.browser.js`);
  await loadScript(`${api.origin}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`, config.publishable_key);
  clerk = window.Clerk;
  await clerk.load({
    ui: { ClerkUI: window.__internal_ClerkUICtor }, telemetry: false,
    signInUrl: '/account', signUpUrl: '/account',
    signInForceRedirectUrl: '/account', signUpForceRedirectUrl: '/account',
    appearance: { variables: { colorPrimary: '#f0a23c', colorBackground: '#1c252d', borderRadius: '4px' } },
  });
  clerk.addListener(renderProviderSession);
}

$('#retry-account').addEventListener('click', () => location.reload());
async function linkRequest(path, method, token) {
  const session = await currentSession();
  if (!isSteam(session)) throw new Error('Your Steam session ended. Sign in with Steam again before connecting.');
  const response = await fetch(path, { method, credentials: 'same-origin', cache: 'no-store',
    headers: { 'X-CSRF-Token': session.csrf_token, ...(token ? { 'X-Clerk-Token': token } : {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === 'string' ? body.detail : 'Connection could not finish. Please try again.');
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
    const response = await fetch('/auth/clerk/session', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { Authorization: `Bearer ${token}`, 'X-PlayGraph-Auth': '1' },
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
