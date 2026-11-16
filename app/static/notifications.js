import { $, el, button, timeLabel, loadingState } from './dom.js';
import { notificationText, profileHref } from './social.js';

export function createNotifications(state, api, gameDialog, report) {
  let timer = null; let epoch = 0; let loading = false; let latest = 0; let offset = 0;
  const badge = (count) => {
    $('#notification-count').textContent = count > 99 ? '99+' : String(count);
    $('#notification-count').hidden = !count;
    $('#notification-bell').setAttribute('aria-label', count ? `Notifications, ${count} unread` : 'Notifications');
  };
  function row(item) {
    const entry = el('article', `notification-row${item.unread_count ? ' unread' : ''}`);
    const label = notificationText(item);
    let link;
    if (item.review_id) {
      link = button(label, 'notification-link', () => void gameDialog.openThread(item.review_id).catch(report));
    } else {
      link = el('a', 'notification-link', label); link.href = profileHref(item.actors[0].id);
    }
    const time = el('time', 'helper', timeLabel(item.created_at)); time.dateTime = item.created_at;
    if (item.unread_count) entry.append(el('span', 'sr-only', 'Unread. '));
    entry.append(link, time); return entry;
  }
  async function load(more = false) {
    if (loading || !state.user) return;
    const token = epoch; loading = true;
    if (!more && !$('#notification-items').children.length) $('#notification-items').replaceChildren(loadingState('Loading notifications…'));
    try {
      const data = await api(`/me/notifications?limit=20&offset=${more ? offset : 0}`);
      if (token !== epoch) return;
      if (!more) { $('#notification-items').replaceChildren(); offset = 0; }
      $('#notification-items').append(...data.items.map(row));
      if (!data.total) $('#notification-items').append(el('p', 'empty-message', 'No notifications yet. Your next likes, replies and follows will show up here.'));
      offset += data.items.length; latest = data.latest_id; badge(data.unread_count);
      $('#notifications-more').hidden = offset >= data.total;
      $('#notifications-read').disabled = !data.unread_count;
      $('#notification-status').textContent = '';
    } catch (error) {
      if (token === epoch) { $('#notification-status').textContent = 'Could not load notifications. Try Refresh.'; report(error); }
    } finally { if (token === epoch) loading = false; }
  }
  async function poll(token) {
    try {
      if (!document.hidden && state.user) {
        const data = await api('/me/notifications/unread');
        if (token !== epoch) return;
        badge(data.unread_count);
        // Preserve the page position while someone is reading older notifications.
        if (state.view === 'notifications' && offset <= 20) await load();
      }
    } catch { /* A transient polling failure keeps the last count. Refresh can be retried. */ }
    if (token === epoch && state.user) timer = setTimeout(() => void poll(token), 30000);
  }
  function clear() {
    epoch += 1; clearTimeout(timer); loading = false; latest = 0; offset = 0; badge(0);
    $('#notification-items').replaceChildren(); $('#notification-status').textContent = '';
  }
  function start() { clear(); void poll(epoch); }
  $('#notifications-refresh').addEventListener('click', () => void load());
  $('#notifications-more').addEventListener('click', () => void load(true));
  $('#notifications-read').addEventListener('click', async () => {
    const control = $('#notifications-read'); control.disabled = true;
    try { await api(`/me/notifications/read?through_id=${latest}`, { method: 'POST' }); await load(); }
    catch (error) { report(error); control.disabled = false; }
  });
  window.addEventListener('pagehide', clear);
  return { load, start, clear };
}
