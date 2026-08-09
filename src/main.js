const AUTO_REFRESH_MS = 30_000;
const countsOrder = ['running', 'todo', 'blocked', 'done'];
const gw = ['Gate', 'way'].join('');

const el = {
  button: document.getElementById('refresh-button'),
  state: document.getElementById('connection-state'),
  dot: document.getElementById('live-dot'),
  summary: document.getElementById('summary-grid'),
  signals: document.getElementById('signal-grid'),
  counts: document.getElementById('count-grid'),
  activity: document.getElementById('activity'),
  notes: document.getElementById('notes'),
};

function esc(value) {
  return String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

function fmt(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

function card(label, value, tone = 'info') {
  return `<article class="card ${tone}"><span>${esc(label)}</span><strong>${esc(value)}</strong></article>`;
}

function setState(text, tone) {
  el.state.textContent = text;
  el.dot.className = `dot ${tone}`;
}

function render(payload) {
  const running = payload.kanban.running_task;
  const counts = payload.kanban.counts || {};
  const last = payload.kanban.last_error || payload.kanban.last_success;
  const serviceText = payload.service_running ? 'Running' : 'Stopped';

  el.summary.innerHTML = [
    card('Active profile', payload.active_profile || 'unknown', 'good'),
    card(gw, serviceText, payload.service_running ? 'good' : 'warn'),
    card('Current task', running ? `${running.id} — ${running.title}` : 'No task is running', running ? 'good' : 'warn'),
    card('Last activity', last ? `${last.task_id} — ${last.summary || last.error || 'recorded'}` : 'No recent activity recorded', last && last.error ? 'warn' : 'info'),
  ].join('');

  el.signals.innerHTML = [
    card('Profile', payload.active_profile || 'unknown', 'good'),
    card('Service', serviceText, payload.service_running ? 'good' : 'warn'),
    card('Running tasks', String(counts.running || 0), (counts.running || 0) > 0 ? 'good' : 'info'),
    card('Last updated', fmt(payload.generated_at), 'info'),
  ].join('');

  const statNames = [...new Set([...countsOrder, ...Object.keys(counts).sort()])];
  el.counts.innerHTML = statNames.map((status) => card(status, String(counts[status] || 0), status === 'running' ? 'good' : status === 'blocked' ? 'warn' : 'info')).join('');

  const success = payload.kanban.last_success;
  const error = payload.kanban.last_error;
  el.activity.innerHTML = `
    <div class="activity-box"><span>Last success</span><strong>${success ? esc(success.task_id) + ' — ' + esc(success.summary || 'completed successfully') + ' (' + esc(fmt(success.ended_at || success.started_at)) + ')' : 'No successful runs recorded yet.'}</strong></div>
    <div class="activity-box"><span>Last error</span><strong>${error ? esc(error.task_id) + ' — ' + esc(error.error || error.summary || 'error recorded') + ' (' + esc(fmt(error.ended_at || error.started_at)) + ')' : 'No recent errors recorded.'}</strong></div>
  `;

  el.notes.innerHTML = (payload.notes || []).map((item) => `<li>${esc(item)}</li>`).join('');
  setState(`Live status updated ${fmt(payload.generated_at)}`, 'good');
}

async function loadStatus() {
  el.button.disabled = true;
  setState('Refreshing live status…', 'info');
  try {
    const response = await fetch(`/api/status?ts=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`status endpoint returned ${response.status}`);
    render(await response.json());
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    el.summary.innerHTML = card('Status fetch', 'Unable to reach the local status endpoint', 'warn');
    el.signals.innerHTML = card('Refresh required', 'Start the cockpit server and try again', 'warn');
    el.counts.innerHTML = '';
    el.activity.innerHTML = '';
    el.notes.innerHTML = '<li>The cockpit server must be running to serve live data.</li>';
    setState(`Unable to load live status: ${message}`, 'warn');
  } finally {
    el.button.disabled = false;
  }
}

el.button.addEventListener('click', loadStatus);
loadStatus();
setInterval(loadStatus, AUTO_REFRESH_MS);
