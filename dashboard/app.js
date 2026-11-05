'use strict';

const el = id => document.getElementById(id);
const labels = {
  install_if_still_free: ['Installation möglich', 'good'],
  download_only: ['Nur Download', 'warn'],
  wol_candidate: ['WOL-Kandidat', 'warn'],
  review: ['Prüfen', 'warn'],
  unknown: ['Offen', 'neutral'],
};

function badge(text, color) {
  const span = document.createElement('span');
  span.className = 'badge ' + color;
  span.textContent = text;
  return span;
}

function render(state) {
  el('count-clients').textContent = state.clients.length;
  el('count-free').textContent = state.counts.install_if_still_free || 0;
  el('count-busy').textContent = state.counts.download_only || 0;
  el('count-review').textContent = (state.counts.review || 0) + (state.counts.wol_candidate || 0) + (state.counts.unknown || 0);
  el('stamp').textContent = state.timestamp ? new Intl.DateTimeFormat('de-DE', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/Berlin'
  }).format(new Date(state.timestamp)) : 'Kein Bericht';
  el('notice').textContent = state.refresh_error || (state.refreshing ? 'Wartungsvorschau läuft …' :
    state.window ? 'Wartungsfenster offen · Vorschau ohne Paketänderungen' :
    'Wartungsfenster Mo–Fr, 18–20 Uhr · Vorschau ohne Paketänderungen');
  el('refresh').disabled = state.refreshing;
  const rows = state.clients.map(client => {
    const row = document.createElement('tr');
    const title = document.createElement('th');
    title.scope = 'row';
    title.textContent = client.host;
    row.append(title);
    const cells = [
      badge(...(labels[client.action] || labels.unknown)),
      String(client.updates),
      client.removals ? client.removals + (client.cleanup ? ' vorgesehen' : ' zurückgestellt') : '—',
      client.patch === 'patched' ? badge('Testpatch', 'good') : client.patch === 'stock' ? 'Debian' : '—',
      client.apt_enabled ? badge('Aktiv', 'warn') : '—',
    ];
    for (const value of cells) {
      const cell = document.createElement('td');
      cell.append(value);
      row.append(cell);
    }
    return row;
  });
  el('client-rows').replaceChildren(...rows);
  if (!el('host').options.length) {
    for (const client of state.clients) {
      el('host').add(new Option(client.host, client.host, false, client.host === 'LAB-08B'));
    }
  }
}

async function request(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Die Anfrage konnte nicht ausgeführt werden.');
  return data;
}

async function load() {
  try {
    const state = await request('/api/state');
    render(state);
    if (state.refreshing) setTimeout(load, 2200);
  } catch {
    el('notice').textContent = 'Der lokale Wartungsbericht konnte nicht geladen werden.';
    el('refresh').disabled = false;
  }
}

el('refresh').addEventListener('click', async () => {
  el('refresh').disabled = true;
  try {
    await request('/api/refresh', {});
    await load();
  } catch (error) {
    el('notice').textContent = error.message;
    el('refresh').disabled = false;
  }
});

el('plan-form').addEventListener('submit', async event => {
  event.preventDefault();
  el('plan-button').disabled = true;
  el('tasks').hidden = true;
  el('plan-status').textContent = 'Ansible-Aufgaben werden gelesen …';
  try {
    const plan = await request('/api/plan', {host: el('host').value});
    el('tasks').replaceChildren(...plan.tasks.map(task => {
      const item = document.createElement('li');
      item.textContent = task;
      return item;
    }));
    el('tasks').hidden = false;
    el('plan-status').textContent = plan.host + ' · ' + plan.tasks.length + ' Aufgaben';
  } catch (error) {
    el('plan-status').textContent = error.message;
  } finally {
    el('plan-button').disabled = false;
  }
});

load();
