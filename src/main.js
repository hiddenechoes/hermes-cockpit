const statusItems = [
  {
    label: 'Task queue',
    value: 'Ready for the next run',
    tone: 'good',
  },
  {
    label: 'GitHub sync',
    value: 'Waiting for push',
    tone: 'warn',
  },
  {
    label: 'Cockpit shell',
    value: 'Static HTML/CSS/JS',
    tone: 'info',
  },
];

const shortcuts = [
  'Open task board',
  'Check repo status',
  'Review workflow logs',
];

function formatTimestamp(date = new Date()) {
  return new Intl.DateTimeFormat('en-US', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

function renderStatus() {
  const list = document.getElementById('status-list');
  const shortcutList = document.getElementById('action-list');
  const updated = document.getElementById('last-updated');

  if (!list || !shortcutList || !updated) {
    return;
  }

  list.innerHTML = statusItems
    .map(
      (item) => `
        <li class="status-item status-${item.tone}">
          <span class="status-label">${item.label}</span>
          <strong>${item.value}</strong>
        </li>
      `,
    )
    .join('');

  shortcutList.innerHTML = shortcuts
    .map(
      (item) => `
        <button type="button" class="shortcut-card">${item}</button>
      `,
    )
    .join('');

  updated.textContent = formatTimestamp();
}

renderStatus();
