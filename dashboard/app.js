'use strict';

const el = id => document.getElementById(id);
const pages = [...document.querySelectorAll('[data-page]')];
const previous = window.location.pathname === '/' && window.location.hash
  ? el(window.location.hash.slice(1)) : null;
if (previous?.dataset.page) window.history.replaceState(null, '', previous.dataset.page);
const currentPage = window.location.pathname;
const views = [...document.querySelectorAll('nav a')];
for (const page of pages) page.hidden = page.dataset.page !== currentPage;
for (const view of views) {
  if (view.getAttribute('href') === currentPage) {
    view.setAttribute('aria-current', 'page');
    document.title = view.textContent + ' · CISCO-LAB';
  }
}
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

if (['/', '/schuelerclients', '/installationsfolge'].includes(currentPage)) load();

let pilotPlan = null;
let pilotBusy = false;
let selectedRun = null;
let reviewRun = null;
let pilotPolling = null;
const pilotStatus = {
  running: 'Läuft', succeeded: 'Erfolgreich', failed: 'Fehlgeschlagen', interrupted: 'Unterbrochen',
};

function pilotButtons() {
  el('pilot-action').hidden = !pilotPlan;
  el('pilot-start').disabled = !pilotPlan || pilotBusy;
  el('pilot-plan').disabled = !el('pilot-job').options.length || pilotBusy;
}

async function pilotLog(id) {
  selectedRun = id;
  try {
    const data = await request('/api/pilot/log/' + id);
    if (selectedRun !== id) return;
    el('pilot-output').textContent = data.output || 'Ansible startet …';
    el('pilot-output').hidden = false;
  } catch (error) {
    el('pilot-status').textContent = error.message;
  }
}

async function loadPilots() {
  clearTimeout(pilotPolling);
  try {
    const data = await request('/api/pilot');
    if (!el('pilot-job').options.length) {
      for (const host of data.hosts) el('pilot-host').add(new Option(host, host));
      for (const job of data.jobs) el('pilot-job').add(new Option(job.label, job.id));
      el('pilot-status').textContent = 'Wähle Testclient und Auftrag.';
    }
    const jobs = new Map(data.jobs.map(job => [job.id, job]));
    el('pilot-empty').hidden = data.runs.length > 0;
    pilotBusy = data.runs.some(run => run.status === 'running');
    pilotButtons();
    el('pilot-runs').replaceChildren(...data.runs.map(run => {
      const row = document.createElement('tr');
      const stamp = new Date(run.started).toLocaleString('de-DE', {timeZone: 'Europe/Berlin'});
      const job = jobs.get(run.job);
      for (const value of [stamp, run.host, job.label, pilotStatus[run.status]]) {
        const cell = row.insertCell();
        cell.textContent = value;
      }
      const review = row.insertCell();
      if (run.review) {
        const accepted = run.review.decision === 'accepted';
        review.append(badge(accepted ? 'Abgenommen' : 'Abgelehnt', accepted ? 'good' : 'warn'));
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = 'Details';
        const note = document.createElement('p');
        note.textContent = [run.review.reviewer, new Date(run.review.reviewed).toLocaleString('de-DE'),
          run.review.note, 'Stand ' + run.revision.slice(0, 12)].filter(Boolean).join(' · ');
        details.append(summary, note);
        review.append(details);
      } else if (run.status === 'succeeded' && !run.source_changed) {
        const reviewButton = document.createElement('button');
        reviewButton.type = 'button';
        reviewButton.className = 'button';
        reviewButton.textContent = 'Praxistest erfassen';
        reviewButton.addEventListener('click', () => openReview(run, job));
        review.append(reviewButton);
      } else {
        review.textContent = run.source_changed ? 'Quellen geändert' : '—';
      }
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'button';
      button.textContent = 'Anzeigen';
      button.addEventListener('click', () => pilotLog(run.id));
      row.insertCell().append(button);
      return row;
    }));
    if (selectedRun) await pilotLog(selectedRun);
    if (pilotBusy) pilotPolling = setTimeout(loadPilots, 2400);
  } catch (error) {
    el('pilot-status').textContent = error.message;
  }
}

function clearPlan() {
  pilotPlan = null;
  el('pilot-tasks').hidden = true;
  pilotButtons();
}

el('pilot-form').addEventListener('change', clearPlan);
el('pilot-form').addEventListener('submit', async event => {
  event.preventDefault();
  clearPlan();
  const host = el('pilot-host').value;
  const job = el('pilot-job').value;
  el('pilot-plan').disabled = true;
  el('pilot-status').textContent = 'Aufgabenliste wird gelesen …';
  try {
    const plan = await request('/api/pilot/plan', {host, job});
    if (host !== el('pilot-host').value || job !== el('pilot-job').value) return;
    pilotPlan = plan;
    el('pilot-tasks').replaceChildren(...plan.tasks.map(task => {
      const item = document.createElement('li');
      item.textContent = task;
      return item;
    }));
    el('pilot-tasks').hidden = false;
    el('pilot-status').textContent = host + ' · ' + plan.tasks.length + ' Aufgaben · Vorschau, noch kein Deploy';
  } catch (error) {
    el('pilot-status').textContent = error.message;
  } finally {
    pilotButtons();
  }
});

el('pilot-start').addEventListener('click', () => {
  if (!pilotPlan || pilotBusy) return;
  const name = el('pilot-job').selectedOptions[0].text;
  const images = pilotPlan.images;
  const extra = images ? ' Desktop: ' + images.desktop + '. Sperre und Anmeldung: ' + images.lock + '.' : '';
  el('pilot-confirm-target').textContent = name + ' auf ' + pilotPlan.host + ' anwenden?' + extra;
  el('pilot-confirm').returnValue = 'cancel';
  el('pilot-confirm').showModal();
});

el('pilot-confirm').addEventListener('close', async () => {
  if (el('pilot-confirm').returnValue !== 'start' || !pilotPlan || pilotBusy) return;
  const {host, job, revision, token} = pilotPlan;
  pilotBusy = true;
  clearPlan();
  try {
    const run = await request('/api/pilot/start', {host, job, revision, token});
    selectedRun = run.id;
    el('pilot-status').textContent = 'Deploy angenommen. Verlauf und Ausgabe stehen im Laufprotokoll.';
  } catch (error) {
    el('pilot-status').textContent = error.message;
  } finally {
    pilotBusy = false;
    pilotButtons();
    await loadPilots();
  }
});

function openReview(run, job) {
  reviewRun = run;
  el('pilot-review-target').textContent = run.host + ' · ' + job.label + ' · Stand ' + run.revision.slice(0, 12);
  const fieldset = el('pilot-review-checks');
  fieldset.querySelectorAll('label').forEach(label => label.remove());
  for (const [id, text] of Object.entries(job.checks)) {
    const label = document.createElement('label');
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.value = id;
    label.append(check, ' ' + text);
    fieldset.append(label);
  }
  el('pilot-review-note').value = '';
  el('pilot-review-error').textContent = '';
  el('pilot-review').showModal();
}

el('pilot-review-cancel').addEventListener('click', () => el('pilot-review').close());
el('pilot-review-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.submitter;
  if (!reviewRun || !button) return;
  const checks = [...el('pilot-review-checks').querySelectorAll('input:checked')].map(input => input.value);
  button.disabled = true;
  try {
    await request('/api/pilot/review', {
      run_id: reviewRun.id, decision: button.value, checks, note: el('pilot-review-note').value,
    });
    el('pilot-review').close();
    await loadPilots();
  } catch (error) {
    el('pilot-review-error').textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

if (currentPage === '/pilotdeploys') loadPilots();

let roomData = null;
let roomPlan = null;
let roomPolling = null;
const roomStatus = {running: 'Läuft', finished: 'Beendet', interrupted: 'Unterbrochen'};

async function loadRoom() {
  clearTimeout(roomPolling);
  try {
    roomData = await request('/api/room');
    if (!el('room-action').options.length) {
      for (const [value, label] of Object.entries(roomData.actions)) {
        el('room-action').add(new Option(label, value));
      }
      const scopes = el('room-scope');
      for (const host of roomData.scopes.room) {
        if (![...scopes.options].some(option => option.text === 'Nur ' + host)) {
          scopes.add(new Option('Nur ' + host, host));
        }
      }
    }
    const busy = roomData.runs.some(run => run.status === 'running');
    el('room-prepare').disabled = busy;
    el('room-runs').replaceChildren(...roomData.runs.map((run, index) => {
      const details = document.createElement('details');
      details.open = index === 0;
      const title = document.createElement('summary');
      title.textContent = [roomData.actions[run.action], roomStatus[run.status],
        new Date(run.started).toLocaleString('de-DE')].join(' · ');
      const list = document.createElement('ul');
      for (const host of roomData.scopes[run.scope]) {
        const item = document.createElement('li');
        item.textContent = host + ' · ' + (run.results[host] || 'Kein Ergebnis');
        list.append(item);
      }
      details.append(title, list);
      return details;
    }));
    if (busy) roomPolling = setTimeout(loadRoom, 2400);
  } catch (error) {
    el('room-status').textContent = error.message;
  }
}

el('room-form').addEventListener('submit', event => {
  event.preventDefault();
  roomPlan = {action: el('room-action').value, scope: el('room-scope').value, confirmed: true};
  const hosts = roomData.scopes[roomPlan.scope];
  el('room-confirm-target').textContent = roomData.actions[roomPlan.action] + ' · ' + hosts.join(', ');
  el('room-confirm').returnValue = 'cancel';
  el('room-confirm').showModal();
});

el('room-confirm').addEventListener('close', async () => {
  if (el('room-confirm').returnValue !== 'start' || !roomPlan) return;
  el('room-prepare').disabled = true;
  try {
    await request('/api/room/start', roomPlan);
    el('room-status').textContent = 'Aktion gestartet. Ergebnisse erscheinen pro Rechner.';
  } catch (error) {
    el('room-status').textContent = error.message;
  } finally {
    roomPlan = null;
    await loadRoom();
  }
});

if (currentPage === '/sammelaktionen') loadRoom();

let backupPolling = null;
async function loadBackups() {
  clearTimeout(backupPolling);
  try {
    const data = await request('/api/backup');
    const busy = data.runs.some(run => run.status === 'running');
    el('backup-start').disabled = busy;
    el('backup-runs').replaceChildren(...data.runs.map(run => {
      const item = document.createElement('li');
      const status = {running: 'Läuft', finished: 'Gesichert', failed: 'Fehlgeschlagen', interrupted: 'Unterbrochen'};
      item.textContent = [new Date(run.started).toLocaleString('de-DE'), status[run.status], run.file,
        run.data_copy && 'Auf data gesichert', run.copied && 'SCP übertragen', run.error].filter(Boolean).join(' · ');
      return item;
    }));
    if (busy) backupPolling = setTimeout(loadBackups, 2400);
  } catch (error) {
    el('backup-status').textContent = error.message;
  }
}

el('backup-form').addEventListener('submit', async event => {
  event.preventDefault();
  el('backup-start').disabled = true;
  try {
    await request('/api/backup/start', {transfer: true,
      confirmed: el('backup-confirm').checked});
    el('backup-status').textContent = 'Sicherung gestartet.';
  } catch (error) {
    el('backup-status').textContent = error.message;
  } finally {
    await loadBackups();
  }
});

if (currentPage === '/backup') loadBackups();

function renderImages(data) {
  el('appearance-folder').textContent = data.folder;
  for (const key of ['desktop', 'lock']) {
    const select = el('appearance-' + key);
    const choices = new Set(data.images);
    if (data.selected[key]) choices.add(data.selected[key]);
    select.replaceChildren(new Option('LAB-Standardbild', ''),
      ...[...choices].map(name => new Option(name, name)));
    select.value = data.selected[key];
  }
  el('appearance-save').disabled = false;
}

async function loadImages() {
  const refresh = el('appearance-refresh');
  refresh.disabled = true;
  el('appearance-status').textContent = 'Bilderordner wird gelesen …';
  try {
    const data = await request('/api/appearance');
    renderImages(data);
    const count = data.images.length === 1 ? '1 Bild verfügbar' : data.images.length + ' Bilder verfügbar';
    const skipped = data.excluded ? ' · ' + data.excluded + ' nicht angezeigt (leer oder über ' + data.limit_mib + ' MiB)' : '';
    el('appearance-status').textContent = count + skipped + '.';
  } catch (error) {
    el('appearance-status').textContent = error.message;
  } finally {
    refresh.disabled = false;
  }
}

el('appearance-refresh').addEventListener('click', loadImages);
el('appearance-form').addEventListener('submit', async event => {
  event.preventDefault();
  el('appearance-save').disabled = true;
  try {
    renderImages(await request('/api/appearance/select', {
      desktop: el('appearance-desktop').value, lock: el('appearance-lock').value,
    }));
    clearPlan();
    el('appearance-status').textContent = 'Auswahl gespeichert. Auf Clients wurde noch nichts geändert.';
    await loadAppearanceRelease();
  } catch (error) {
    el('appearance-status').textContent = error.message;
  } finally {
    el('appearance-save').disabled = false;
  }
});

let releaseData = null;
let releasePlan = null;
let releasePolling = null;
const releaseStatus = {running: 'Läuft', finished: 'Beendet', failed: 'Fehlgeschlagen', interrupted: 'Unterbrochen'};

async function loadAppearanceRelease() {
  clearTimeout(releasePolling);
  try {
    releaseData = await request('/api/appearance/rollout');
    const {approval, targets, runs} = releaseData;
    const busy = runs.some(run => run.status === 'running');
    el('appearance-release-start').disabled = !approval || busy;
    el('appearance-release-status').textContent = busy ? 'Raumverteilung läuft …' : approval
      ? 'Abgenommen auf ' + approval.host + ' · Stand ' + approval.revision.slice(0, 12)
      : 'Für die aktuelle Bildauswahl liegt noch keine Pilotabnahme vor.';
    el('appearance-release-runs').replaceChildren(...runs.map((run, index) => {
      const details = document.createElement('details');
      details.open = index === 0;
      const summary = document.createElement('summary');
      const assigned = run.targets || targets;
      const finished = Object.keys(run.results).length + ' von ' + assigned.length + ' ausgewertet';
      summary.textContent = [new Date(run.started).toLocaleString('de-DE'), releaseStatus[run.status], finished, run.error].filter(Boolean).join(' · ');
      const list = document.createElement('ul');
      for (const host of assigned) {
        const item = document.createElement('li');
        const pending = run.status !== 'running' ? 'Kein Ergebnis'
          : run.active?.includes(host) ? 'In Arbeit' : 'Wartet';
        item.textContent = host + ' · ' + (run.results[host] || pending);
        if (run.results[host]) {
          const log = document.createElement('button');
          log.type = 'button';
          log.className = 'button log-button';
          log.textContent = 'Protokoll';
          log.addEventListener('click', async () => {
            const output = el('appearance-release-output');
            try {
              const data = await request('/api/appearance/rollout/log/' + run.id + '/' + host);
              output.textContent = host + '\n' + data.output;
            } catch (error) {
              output.textContent = host + ' · ' + error.message;
            }
            output.hidden = false;
          });
          item.append(' ', log);
        }
        list.append(item);
      }
      details.append(summary);
      if (approval && !busy && run.pending?.length && run.revision === approval.revision) {
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'button log-button';
        retry.textContent = 'Ausstehende Rechner nachholen';
        retry.addEventListener('click', () => prepareAppearanceRelease(run.id, run.pending));
        details.append(retry);
      }
      details.append(list);
      return details;
    }));
    if (busy) releasePolling = setTimeout(loadAppearanceRelease, 2500);
  } catch (error) {
    el('appearance-release-status').textContent = error.message;
  }
}

function prepareAppearanceRelease(retry, targets) {
  if (!releaseData?.approval) return;
  releasePlan = {run_id: releaseData.approval.id, refresh: el('appearance-release-refresh').checked,
    confirmed: true, retry};
  el('appearance-release-targets').textContent = targets.join(', ') + ' · Freigabe von ' + releaseData.approval.host
    + (releasePlan.refresh ? ' · Anmeldebild auf freien Clients sofort aktualisieren' : '');
  el('appearance-release-confirm-start').textContent = 'Auf ' + targets.length + ' Clients verteilen';
  el('appearance-release-confirm').returnValue = 'cancel';
  el('appearance-release-confirm').showModal();
}

el('appearance-release-form').addEventListener('submit', event => {
  event.preventDefault();
  if (releaseData) prepareAppearanceRelease('', releaseData.targets);
});

el('appearance-release-confirm').addEventListener('close', async () => {
  if (el('appearance-release-confirm').returnValue !== 'start' || !releasePlan) return;
  el('appearance-release-start').disabled = true;
  try {
    await request('/api/appearance/rollout/start', releasePlan);
    await loadAppearanceRelease();
  } catch (error) {
    el('appearance-release-status').textContent = error.message;
    el('appearance-release-start').disabled = false;
  } finally {
    releasePlan = null;
  }
});

if (currentPage === '/desktopbilder') {
  loadImages();
  loadAppearanceRelease();
}
