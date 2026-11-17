import { $, el, button, cover, loadingState, emptyState, aborted } from './dom.js';
import { hours, integer } from './library.js';
import { barList } from './chart.js';

export const sourceName = (source) => source === 'psn' ? 'PlayStation' : 'Steam';
export const replayHours = (minutes) => minutes == null ? 'Hours not shared' : `${hours(minutes)} h tracked`;
export const monthLabel = (month) => ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][month - 1];
export function cardLines(data, source, name) {
  return [`${name}'s ${data.year} in games`, sourceName(source.source),
    data.partial_year ? 'Partial year. Since tracking began, through the last sync.' : 'Recorded changes during this year.',
    replayHours(source.total_minutes),
    ...(source.source === 'steam' ? [`${source.achievements_earned == null ? 'Unknown' : integer(source.achievements_earned)} achievements earned`]
      : [source.trophies_earned ? Object.entries(source.trophies_earned).map(([grade, count]) => `${grade}: ${count ?? 'unknown'}`).join(' | ') : 'Trophies not shared']),
    `${integer(data.reviews.count)} game reviews | ${integer(data.completed_count)} currently marked completed this year`,
    ...source.top_games.map((row, i) => `${i + 1}. ${row.game.name} | ${hours(row.total_minutes)} h`),
    ...((source.genres || []).length ? [`Top genres: ${source.genres.map((row) => row.genre).join(', ')}`] : []),
    'Software excluded. Steam and PlayStation are never combined.',
    'First synced totals are a baseline, not hours played this year.'];
}

async function saveCard(data, source, name) {
  const canvas = document.createElement('canvas'); canvas.width = 1200; canvas.height = 1040;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Your browser could not create the card.');
  ctx.fillStyle = '#111b27'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#d4eb91'; ctx.font = 'bold 25px sans-serif'; ctx.fillText('PLAYGRAPH / YEAR IN REVIEW', 64, 70);
  const fit = (text, width) => {
    const chars = [...text];
    while (chars.length && ctx.measureText(chars.join('')).width > width) chars.pop();
    return chars.length < [...text].length ? chars.slice(0, -1).join('') + '…' : text;
  };
  let y = 140;
  cardLines(data, source, name).forEach((line, index) => {
    if (index === 3) y += 28;
    ctx.font = index === 0 ? 'bold 44px sans-serif' : index === 3 ? 'bold 60px sans-serif'
      : index > 10 ? '20px sans-serif' : '25px sans-serif';
    ctx.fillStyle = index === 3 ? '#d4eb91' : '#eef2f6';
    ctx.fillText(fit(line, 1072), 64, y); y += index === 3 ? 74 : 47;
  });
  ctx.font = '19px sans-serif'; ctx.fillStyle = '#c5ced9';
  ctx.fillText(`Tracked since ${data.tracked_since?.slice(0, 10) || 'first sync'} | As of ${data.as_of.slice(0, 10)} UTC`, 64, 930);
  ctx.fillText('An image you chose to share. Your replay page stays private.', 64, 975);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
  if (!blob) throw new Error('Your browser could not save the card. Try again.');
  const url = URL.createObjectURL(blob); const link = document.createElement('a');
  link.href = url; link.download = `playgraph-${data.year}-${source.source}.png`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

export function createReplay(state, api, gameDialog, report) {
  let epoch = 0; let selected = null;
  const root = () => $('#replay');
  function clear() { epoch += 1; selected = null; root().replaceChildren(); }
  function render(data) {
    const toolbar = el('div', 'toolbar'); const label = el('label', 'select', 'Year ');
    const picker = el('select'); picker.setAttribute('aria-label', 'Replay year');
    for (const year of data.available_years) picker.append(new Option(String(year), String(year)));
    picker.value = String(data.year); picker.addEventListener('change', () => void load(Number(picker.value)));
    label.append(picker); toolbar.append(label, button('Refresh', 'button secondary', () => void load()));
    const summary = el('section', 'replay-summary');
    summary.append(el('p', 'micro', 'Private to you'), el('h2', '', `${data.year} in games`),
      el('p', 'subtitle', data.partial_year ? 'Your year so far, since tracking began.' : 'Your recorded year in games.'),
      el('p', 'helper', `Tracked since ${data.tracked_since?.slice(0, 10) || 'your first game sync'}. As of ${data.as_of.slice(0, 10)} UTC.`));
    const stats = el('dl', 'stat-strip');
    for (const [title, value] of [['Reviews written', data.reviews.count], ['Average rating', data.reviews.average_rating ?? 'Not rated'],
      ['Currently marked completed this year', data.completed_count]]) {
      const cell = el('div'); cell.append(el('dt', '', title), el('dd', '', String(value))); stats.append(cell);
    }
    summary.append(stats);
    if (data.reviews.highest_rated) summary.append(el('p', '', `Highest rated: ${data.reviews.highest_rated.game.name} (${data.reviews.highest_rated.rating}/5).`));
    root().replaceChildren(toolbar, summary);
    if (!data.has_data) root().append(emptyState({ title: 'No tracked activity for this year',
      text: 'Sync your library to start a baseline. Later syncs record changes, not your lifetime hours.' }));
    for (const source of data.sources) {
      const section = el('section', 'replay-platform'); const heading = el('div', 'section-heading');
      heading.append(el('h2', '', sourceName(source.source)),
        button('Save card', 'button secondary', async () => {
          try { await saveCard(data, source, state.user.display_name); } catch (error) { report(error); }
        }));
      section.append(heading, el('p', 'replay-total num', replayHours(source.total_minutes)));
      if (source.source === 'steam') section.append(el('p', '', source.achievements_earned == null
        ? 'Achievements not reported.' : `${integer(source.achievements_earned)} achievements earned.`));
      else section.append(el('p', '', source.trophies_earned ? Object.entries(source.trophies_earned)
        .map(([grade, count]) => `${grade}: ${count ?? 'not shared'}`).join(' · ') : 'Trophies not shared.'));
      if (source.unknown_playtime_games) section.append(el('p', 'helper', `${source.unknown_playtime_games} games have missing hours. Only measurable changes are counted.`));
      const top = el('div', 'replay-top');
      for (const row of source.top_games) {
        const open = button('', 'replay-game', () => void gameDialog.open(row.game));
        open.append(cover(row.game), el('strong', '', row.game.name), el('span', 'helper', `${hours(row.total_minutes)} h this year`)); top.append(open);
      }
      section.append(el('h3', '', 'Top games'), top);
      if (!source.top_games.length) top.append(el('p', 'helper', 'No measured playtime increases yet.'));
      section.append(el('h3', '', 'By month'), barList(source.months.map((month) => ({
        label: monthLabel(month.month), value: month.total_minutes ?? 0,
        text: month.total_minutes == null ? 'Not available' : `${hours(month.total_minutes)} h`,
        detail: 'Changes land on the later sync date, in UTC.',
      }))));
      section.append(el('h3', '', 'Top genres'), barList(source.genres.map((genre) => ({ label: genre.genre,
        value: genre.total_minutes, text: `${hours(genre.total_minutes)} h`, detail: 'Full credit for each genre. Genre totals overlap.',
      }))));
      root().append(section);
    }
    root().append(el('p', 'replay-note helper', 'Only synced changes count. First observations are baselines, not lifetime hours earned this year. Gaps land on the later sync date. Missing hours stay unknown. Software is excluded. Each genre gets full credit, so genre totals overlap. Completed counts use current statuses, not a permanent diary. Save a card only if you want to share these numbers.'));
  }
  async function load(year = selected) {
    if (!state.user) return;
    const token = ++epoch; const owner = state.user.id;
    root().replaceChildren(loadingState('Loading your year…'));
    try {
      const data = await api(`/me/replay${year == null ? '' : `?year=${year}`}`);
      if (token !== epoch || state.user?.id !== owner || state.view !== 'replay') return;
      selected = data.year; render(data);
    } catch (error) {
      if (token !== epoch || aborted(error) || state.view !== 'replay') return;
      root().replaceChildren(emptyState({ kind: 'error', title: 'Could not load your year', text: 'Try again when your connection is ready.',
        actions: [button('Try again', 'button secondary', () => void load(year))] })); report(error);
    }
  }
  return { load, clear };
}
