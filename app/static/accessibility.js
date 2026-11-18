const STORAGE_KEY = 'playgraph:accessibility';
export const accessibility = { shortcuts: true };

function savedPreferences() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    return { motion: saved?.motion === true, contrast: saved?.contrast === true, shortcuts: saved?.shortcuts !== false };
  } catch { return { motion: false, contrast: false, shortcuts: true }; }
}

export function initAccessibility() {
  const controls = Object.fromEntries(['motion', 'contrast', 'shortcuts'].map((name) => [name, document.getElementById(`a11y-${name}`)]));
  if (Object.values(controls).some((control) => !control)) return;
  const deviceMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let preferences = savedPreferences();
  function apply() {
    const motion = deviceMotion.matches || preferences.motion;
    document.documentElement.toggleAttribute('data-reduced-motion', motion);
    document.documentElement.toggleAttribute('data-high-contrast', preferences.contrast);
    controls.motion.checked = motion; controls.motion.disabled = deviceMotion.matches;
    controls.contrast.checked = preferences.contrast; controls.shortcuts.checked = preferences.shortcuts;
    accessibility.shortcuts = preferences.shortcuts;
    document.getElementById('a11y-motion-help').textContent = deviceMotion.matches
      ? 'Your device already requests reduced motion.' : 'Stop moving cover walls, loading animations and transitions.';
    const hint = document.querySelector('.search kbd'); if (hint) hint.hidden = !preferences.shortcuts;
    const search = document.getElementById('search');
    if (search) {
      if (preferences.shortcuts) search.setAttribute('aria-keyshortcuts', '/');
      else search.removeAttribute('aria-keyshortcuts');
    }
  }
  for (const [name, control] of Object.entries(controls)) control.addEventListener('change', () => {
    preferences[name] = control.checked;
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences)); } catch { /* The current page still uses the preference. */ }
    apply();
  });
  const settings = document.getElementById('accessibility-settings');
  const opener = document.getElementById('accessibility-open');
  opener?.addEventListener('click', () => {
    settings.open = true; const summary = settings.querySelector('summary');
    summary.focus(); summary.scrollIntoView({ block: 'center', behavior: 'instant' });
  });
  settings?.addEventListener('toggle', () => opener?.setAttribute('aria-expanded', String(settings.open)));
  const header = document.querySelector('.topbar');
  if (header && globalThis.ResizeObserver) new ResizeObserver(() => {
    document.documentElement.style.setProperty('--sticky-header-size', `${header.getBoundingClientRect().height}px`);
  }).observe(header);
  deviceMotion.addEventListener('change', apply);
  globalThis.addEventListener?.('storage', (event) => {
    if (event.key === STORAGE_KEY || event.key === null) { preferences = savedPreferences(); apply(); }
  });
  apply();
}

// A new view opens at the top of the page, the way a new page would, and its
// heading takes focus so screen readers announce it. Scrolling the heading
// itself into view pushed the cover wall half off screen on every tab switch.
export function focusHeading(heading) {
  if (!heading || heading.closest('[hidden]')) return;
  heading.tabIndex = -1;
  heading.focus({ preventScroll: true });
  globalThis.scrollTo?.({ top: 0, behavior: 'instant' });
}

if (globalThis.document?.getElementById?.('a11y-motion')) initAccessibility();
