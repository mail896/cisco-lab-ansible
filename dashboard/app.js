'use strict';

const el = id => document.getElementById(id);
const basePath = document.documentElement.dataset.basePath || '';
const labRole = document.documentElement.dataset.role === 'reader' ? 'reader' : 'admin';
const csrfToken = document.querySelector('meta[name="cisco-lab-csrf"]')?.content || '';
// Backend enforces the same boundary. These annotations keep the read-only GUI
// consistent even when polling updates a button's disabled state.
const adminControls = ['refresh', 'plan-form', 'bootstrap-form', 'intake-form', 'intake-review',
  'intake-accept-panel', 'fresh-form', 'pilot-form', 'pilot-start', 'pilot-review-form',
  'backup-form', 'appearance-form', 'appearance-release-form', 'catalog-rollout', 'maintenance-controls'];
for (const id of adminControls) if (el(id)) el(id).dataset.adminOnly = '';
function enforceReadOnly() {
  if (labRole !== 'reader') return;
  for (const container of document.querySelectorAll('[data-admin-only]')) {
    const controls = container.matches('button,input,select,textarea') ? [container] : container.querySelectorAll('button,input,select,textarea');
    for (const control of controls) if (!control.disabled) { control.disabled = true; control.title = 'Nur der Administrator darf Aktionen starten.'; }
  }
}
if (labRole === 'reader') {
  enforceReadOnly();
  new MutationObserver(enforceReadOnly).observe(document.querySelector('main'), {subtree: true, childList: true, attributes: true, attributeFilter: ['disabled', 'data-admin-only']});
}
el('auth-logout')?.addEventListener('click', async event => {
  event.preventDefault();
  try { const result = await request('/logout', {}); window.location.assign(result.redirect); }
  catch (error) { el('session-label').textContent = error.message; }
});
if (basePath && window.location.pathname === basePath) {
  window.history.replaceState(null, '', basePath + '/' + window.location.hash);
}
const pages = [...document.querySelectorAll('[data-page]')];
const currentPath = window.location.pathname.startsWith(basePath + '/')
  ? window.location.pathname.slice(basePath.length) : '/';
const previous = currentPath === '/' && window.location.hash
  ? el(window.location.hash.slice(1)) : null;
if (previous?.dataset.page) window.history.replaceState(null, '', basePath + previous.dataset.page);
const currentPage = window.location.pathname.slice(basePath.length) || '/';
el('lab-home')?.addEventListener('click', event => {
  if (currentPage === '/') { event.preventDefault(); window.location.reload(); }
});
const views = [...document.querySelectorAll('nav a')];
const navigationToggle = el('nav-toggle');
function showNavigation(open) {
  document.body.classList.toggle('nav-open', open);
  navigationToggle?.setAttribute('aria-expanded', String(open));
}
navigationToggle?.addEventListener('click', () => showNavigation(navigationToggle.getAttribute('aria-expanded') !== 'true'));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && document.body.classList.contains('nav-open')) {
    showNavigation(false);
    navigationToggle?.focus();
  }
});
for (const page of pages) page.hidden = page.dataset.page !== currentPage;
for (const view of views) {
  if (view.getAttribute('href') === basePath + currentPage) {
    view.setAttribute('aria-current', 'page');
    document.title = view.textContent.trim() + ' · CISCO-LAB';
    if (view.closest('details')) view.closest('details').open = true;
  }
}
const labels = {
  install_if_still_free: ['Installation möglich', 'good'],
  evening_logout_install: ['Abendwartung mit Abmeldung und Neustart', 'good'],
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

let clientState = null;
let clientsGeneration = 0;
let clientsProbePending = false;
let clientsReadFailed = false;
function clientPackageList(client, key, count, label, opened) {
  const names = client[key];
  if (!count) return '0';
  if (!Array.isArray(names) || !names.length) return String(count);
  const details = document.createElement('details'); details.className = 'client-package-list';
  details.dataset.packageKey = client.host + ':' + key;
  details.open = opened.has(details.dataset.packageKey);
  const caption = count === 1 ? label === 'Updates' ? 'Update' : 'Kandidat' : label;
  const summary = document.createElement('summary'); summary.textContent = count + ' ' + caption;
  summary.setAttribute('aria-label', count + ' ' + caption + ' für ' + client.host);
  const list = document.createElement('ul');
  for (const name of names) { const row = document.createElement('li'); row.textContent = name; list.append(row); }
  details.append(summary,list); return details;
}
function renderClientRows(state) {
  const query = el('clients-search').value.toLowerCase().replace(/[^a-z0-9]/g,'');
  const selected = state.clients.filter(client => client.host.toLowerCase().replace(/[^a-z0-9]/g,'').includes(query));
  const headings = ['Wartung','Updates','Paketbereinigung','Winbind','Update-Steuerung'];
  const opened = new Set([...el('client-rows').querySelectorAll('details[open]')].map(node => node.dataset.packageKey));
  const rows = selected.map(client => {
    const row = document.createElement('tr');
    const title = document.createElement('th'); title.scope = 'row'; title.dataset.label = 'Client';
    const link = document.createElement('a'); link.href = basePath + '/sammelaktionen#' + encodeURIComponent(client.host);
    link.textContent = client.host; link.title = 'Diesen Client in Sammelaktionen auswählen'; title.append(link); row.append(title);
    const known = client.action !== 'unknown';
    const apt = {central: ['APP-gesteuert','good'],local: ['Lokale Automatik aktiv','warn'],disabled: ['Lokale Timer aus','neutral'],unknown: ['Unbekannt','neutral']};
    const cells = [badge(...(labels[client.action] || labels.unknown)),
      known && client.updates_known !== false ? clientPackageList(client,'update_packages',client.updates,'Updates',opened) : '—',
      known && client.removals_known !== false && client.removals ? clientPackageList(client,'removal_packages',client.removals,'Kandidaten',opened) : '—',
      client.patch === 'patched' ? badge('Fix vorhanden','good') : client.patch === 'stock' ? 'Standardbibliothek' : 'Unbekannt',
      known ? badge(...(apt[client.apt_mode] || apt.unknown)) : 'Unbekannt'];
    for (const [index,value] of cells.entries()) {
      const cell = document.createElement('td'); cell.dataset.label = headings[index]; cell.append(value); row.append(cell);
      if ((index === 1 && client.updates_known === false) || (index === 2 && client.removals_known === false)) {
        cell.title = client.reason || 'Kein aktueller Paketstand verfügbar. Client einschalten und erneut prüfen.';
        cell.setAttribute('aria-label', headings[index] + ': Paketstand nicht bekannt. ' + cell.title);
      }
    }
    return row;
  });
  el('client-rows').replaceChildren(...rows);
  el('clients-visible').textContent = selected.length + ' von ' + state.clients.length + ' Clients';
  el('clients-empty').hidden = selected.length > 0;
}
function render(state) {
  clientState = state;
  el('count-clients').textContent = state.clients.length;
  el('count-free').textContent = state.timestamp ? (state.counts.install_if_still_free || 0) + (state.counts.evening_logout_install || 0) : '—';
  el('count-busy').textContent = state.counts.download_only || 0;
  el('count-review').textContent = state.timestamp ? (state.counts.review || 0) + (state.counts.wol_candidate || 0) + (state.counts.unknown || 0) : '—';
  el('stamp').textContent = state.timestamp ? new Intl.DateTimeFormat('de-DE', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/Berlin'
  }).format(new Date(state.timestamp)) : 'Noch kein Bericht gespeichert';
  el('notice').textContent = state.refresh_error || (state.refreshing ? 'Wartungsvorschau läuft …' :
    state.timestamp ? 'Nach einer Wartung wird der Bericht automatisch neu eingelesen. „Clients prüfen“ fragt zusätzlich den aktuellen Stand ab.' : 'Noch keine Wartungsvorschau vorhanden. Mit „Clients prüfen“ einen Bericht erstellen.');
  el('refresh').disabled = labRole !== 'admin' || state.refreshing || clientsProbePending;
  el('clients-activity').textContent = state.refreshing || clientsProbePending ? 'Abfrage läuft' : state.refresh_error ? 'Abfrage fehlgeschlagen' : state.timestamp ? 'Bericht geladen' : 'Noch kein Bericht';
  el('clients-activity').dataset.state = state.refresh_error ? 'error' : state.refreshing || clientsProbePending ? 'busy' : 'ready';
  renderClientRows(state);
  if (!el('host').options.length) {
    for (const client of state.clients) {
      el('host').add(new Option(client.host, client.host));
    }
  }
}

// Refresh only visible pages. Failed retries slow down; expired sessions stop.
const backgroundPolls = new Map();
let backgroundPaused = false;
let authenticationExpired = false;
function cancelPoll(key) {
  const state = backgroundPolls.get(key);
  if (state) { clearTimeout(state.timer); state.pending = false; }
}
function schedulePoll(key, run, delay, failed = false) {
  const state = backgroundPolls.get(key) || {failures: 0};
  clearTimeout(state.timer);
  state.failures = failed ? Math.min(state.failures + 1, 5) : 0;
  state.run = run;
  state.delay = Math.min(60000, delay * 2 ** Math.max(0, state.failures - 1));
  state.pending = !authenticationExpired;
  backgroundPolls.set(key, state);
  if (state.pending && !document.hidden && !backgroundPaused) {
    state.timer = setTimeout(() => { state.pending = false; run(); }, state.delay);
  }
}
function pausePolls() {
  for (const state of backgroundPolls.values()) clearTimeout(state.timer);
}
function resumePolls() {
  if (document.hidden || backgroundPaused || authenticationExpired) return;
  for (const state of backgroundPolls.values()) {
    if (state.pending) { clearTimeout(state.timer); state.pending = false; state.run(); }
  }
}
document.addEventListener('visibilitychange', () => document.hidden ? pausePolls() : resumePolls());
window.addEventListener('pagehide', () => { backgroundPaused = true; pausePolls(); });
window.addEventListener('pageshow', event => {
  backgroundPaused = false;
  if (event.persisted) resumePolls();
});

async function request(path, payload) {
  if (payload !== undefined && labRole === 'reader' && !['/logout', '/api/room/start'].includes(path)) {
    throw new Error('Du hast Lesezugriff mit Sammelaktionen. Diese Verwaltungsaktion ist dem Administrator vorbehalten.');
  }
  const response = await fetch(basePath + path, payload === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json', ...(csrfToken ? {'X-CSRF-Token': csrfToken} : {})}, body: JSON.stringify(payload),
  });
  if (response.status === 401) {
    authenticationExpired = true; pausePolls();
    throw new Error('Die Anmeldung ist abgelaufen. Bitte die Seite neu laden und mit IServ anmelden.');
  }
  if (!response.headers.get('content-type')?.includes('application/json')) {
    throw new Error('APP antwortet derzeit nicht wie erwartet. Bitte gleich erneut versuchen.');
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Die Anfrage konnte nicht ausgeführt werden.');
  if (payload !== undefined && path !== '/logout') loadActivity();
  return data;
}

let activityLoading = false;
async function loadActivity() {
  cancelPoll('activity');
  if (activityLoading || authenticationExpired) return;
  activityLoading = true;
  let failed = false;
  try {
    const activity = await request('/api/activity');
    el('activity-banner').hidden = !activity.busy;
    el('activity-label').textContent = activity.label || '';
    el('activity-targets').textContent = (activity.hosts || []).join(', ');
    el('activity-started').textContent = activity.started
      ? 'seit ' + new Intl.DateTimeFormat('de-DE',{timeZone:'Europe/Berlin',timeStyle:'short'}).format(new Date(activity.started)) : '';
    el('activity-link').hidden = !activity.href;
    if (activity.href) el('activity-link').href = basePath + activity.href;
  } catch (error) {
    failed = true;
    if (!el('activity-banner').hidden) {
      el('activity-label').textContent = error.message;
      el('activity-targets').textContent = ''; el('activity-started').textContent = ''; el('activity-link').hidden = true;
    }
  } finally {
    activityLoading = false;
    schedulePoll('activity', loadActivity, 30000, failed);
  }
}

async function load() {
  cancelPoll('clients');
  const generation = ++clientsGeneration;
  try {
    const state = await request('/api/state');
    if (generation !== clientsGeneration) return;
    clientsReadFailed = false;
    render(state);
    schedulePoll('clients', load, state.refreshing ? 2200 : 30000);
  } catch (error) {
    if (generation !== clientsGeneration) return;
    clientsReadFailed = true;
    el('notice').textContent = error.message;
    el('refresh').disabled = true;
    el('clients-activity').textContent = 'Bericht nicht verfügbar'; el('clients-activity').dataset.state = 'error';
    schedulePoll('clients', load, 30000, true);
  }
}
el('clients-search').addEventListener('input',() => { if (clientState) renderClientRows(clientState); });
el('clients-reload').addEventListener('click',load);

el('refresh').addEventListener('click', async () => {
  if (el('refresh').disabled) return;
  clientsProbePending = true;
  let failed = false;
  el('refresh').disabled = true; el('clients-activity').textContent = 'Abfrage wird angefordert'; el('clients-activity').dataset.state = 'busy';
  try {
    await request('/api/refresh', {});
    await load();
  } catch (error) {
    failed = true;
    el('notice').textContent = error.message;
    el('clients-activity').textContent = 'Abfrage nicht gestartet'; el('clients-activity').dataset.state = 'error';
  } finally {
    clientsProbePending = false;
    if (!failed && !clientsReadFailed && clientState) render(clientState);
    el('refresh').disabled = labRole !== 'admin' || clientsReadFailed || !clientState || clientState.refreshing;
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

if (currentPage === '/schuelerclients') load();

let pilotPlan = null;
let pilotBusy = false;
let selectedRun = null;
let reviewRun = null;
let pilotLoaded = false;
let pilotPlanning = false;
let pilotStarting = false;
let pilotReadFailed = false;
let pilotPlanGeneration = 0;
let pilotLogGeneration = 0;
let pilotLoadGeneration = 0;
let pilotLogRun = null;
let pilotReviewBusy = false;
let pilotJobs = new Map();
let pilotRuns = new Map();
const pilotDescriptions = {
  firefox: 'Startseite, neue Tabs und Richtlinien für den Browser.',
  serial: 'Konsolenzugriff mit PuTTY und Minicom.',
  session: 'KDE-Sitzung, Anmeldung und Startverhalten.',
  appearance: 'Den gespeicherten Desktop- und Sperrbildstand übernehmen.',
  cisco: 'Lokales Sammelkonto, Desktop und LAB-Werkzeuge einrichten.',
};
const pilotStatus = {
  running: 'Läuft', succeeded: 'Erfolgreich', failed: 'Fehlgeschlagen', interrupted: 'Unterbrochen',
};

function pilotButtons() {
  const blocked = labRole !== 'admin' || !pilotLoaded || pilotReadFailed || pilotBusy || pilotPlanning || pilotStarting;
  el('pilot-action').hidden = !pilotPlan;
  el('pilot-start').disabled = blocked || !pilotPlan;
  el('pilot-plan').disabled = blocked || !el('pilot-job').value || !el('pilot-host').value;
  for (const id of ['pilot-host','pilot-job']) el(id).disabled = blocked;
  for (const review of document.querySelectorAll('[data-pilot-review]')) review.disabled = labRole !== 'admin' || pilotReadFailed;
  const activity = el('pilot-activity');
  activity.textContent = pilotReadFailed ? 'Status nicht verfügbar' : pilotStarting ? 'Start wird angefordert' : pilotPlanning ? 'Aufgaben werden geprüft'
    : pilotBusy ? 'Pilot läuft' : !pilotLoaded ? 'Wird geladen' : pilotPlan ? 'Vorschau geprüft' : 'Bereit';
  activity.dataset.state = pilotReadFailed ? 'error' : !pilotLoaded ? 'loading' : pilotBusy || pilotPlanning || pilotStarting ? 'busy' : 'ready';
}
function pilotSelectionDescription() {
  const job = el('pilot-job').value;
  el('pilot-job-description').textContent = pilotDescriptions[job] || 'Den gewählten Auftrag auf einem Pilotrechner prüfen.';
  el('pilot-images-link').hidden = job !== 'appearance';
}
function canReviewPilot(run, job) {
  return run?.status === 'succeeded' && !run.review && !run.source_changed && !run.superseded
    && Object.keys(job?.checks || {}).length > 0;
}

async function pilotLog(id, refresh = false) {
  if (!refresh) {
    selectedRun = id; pilotLogRun = pilotRuns.get(id) || null;
    el('pilot-log').hidden = false; el('pilot-output').hidden = false;
    el('pilot-output').textContent = 'Protokoll wird geladen …';
  }
  const generation = ++pilotLogGeneration;
  const run = pilotLogRun;
  el('pilot-log-title').textContent = run ? run.host + ' · ' + (pilotJobs.get(run.job)?.label || run.job) + ' · '
    + new Date(run.started).toLocaleString('de-DE', {timeZone:'Europe/Berlin',dateStyle:'short',timeStyle:'short'}) : 'Laufprotokoll';
  try {
    const data = await request('/api/pilot/log/' + id);
    if (selectedRun !== id || generation !== pilotLogGeneration) return;
    el('pilot-output').textContent = data.output || 'Ansible startet …';
  } catch (error) {
    if (selectedRun === id && generation === pilotLogGeneration) el('pilot-output').textContent = error.message;
  }
}
el('pilot-log-close').addEventListener('click', () => {
  selectedRun = null; pilotLogRun = null; pilotLogGeneration++;
  el('pilot-log').hidden = true; el('pilot-output').hidden = true; el('pilot-output').textContent = '';
});

function renderPilotRun(run) {
  const job = pilotJobs.get(run.job);
  const row = document.createElement('details');
  const summary = document.createElement('summary');
  const host = document.createElement('span'); host.className = 'pilot-run-host'; host.textContent = run.host;
  const title = document.createElement('strong'); title.textContent = job?.label || run.job;
  const time = document.createElement('time'); time.dateTime = run.started;
  time.textContent = new Date(run.started).toLocaleString('de-DE',{timeZone:'Europe/Berlin',dateStyle:'short',timeStyle:'short'});
  summary.append(host,title,badge(pilotStatus[run.status] || run.status,
    ['failed','interrupted'].includes(run.status) ? 'warn' : run.status === 'succeeded' ? 'good' : 'neutral'));
  if (run.review) summary.append(badge(run.review.decision === 'accepted' ? 'Abgenommen' : 'Abgelehnt',run.review.decision === 'accepted' ? 'good' : 'warn'));
  else if (canReviewPilot(run,job)) summary.append(badge('Abnahme offen','warn'));
  summary.append(time);
  const body = document.createElement('div'); body.className = 'pilot-run-body';
  function paragraph(text, className = '') { const p = document.createElement('p'); p.textContent = text; p.className = className; body.append(p); }
  if (run.finished) paragraph('Beendet am ' + new Date(run.finished).toLocaleString('de-DE',{timeZone:'Europe/Berlin'}));
  if (run.error) paragraph(run.error,'notice');
  if (run.review) {
    paragraph((run.review.decision === 'accepted' ? 'Praxistest abgenommen' : 'Praxistest abgelehnt') + ' · '
      + new Date(run.review.reviewed).toLocaleString('de-DE',{timeZone:'Europe/Berlin'}));
    if (run.review.note) paragraph(run.review.note,'pilot-review-note');
    const checks = (run.review.checks || []).map(key => job?.checks?.[key] || key);
    if (checks.length) paragraph('Bestätigt: ' + checks.join(' '));
  } else if (run.superseded) paragraph('Durch einen neueren erfolgreichen Lauf ersetzt. Die Abnahme erfolgt beim aktuellen Lauf.');
  else if (run.source_changed) paragraph('Der gespeicherte Quellenstand hat sich geändert. Für eine Abnahme ist ein neuer Pilotlauf erforderlich.');
  else if (canReviewPilot(run,job)) paragraph('Technisch erfolgreich. Prüfe die Funktionen vor Ort und erfasse danach den Praxistest.');
  const actions = document.createElement('div'); actions.className = 'pilot-run-actions';
  const log = document.createElement('button'); log.type = 'button'; log.className = 'pager-button'; log.textContent = 'Protokoll';
  log.addEventListener('click',() => pilotLog(run.id)); actions.append(log);
  if (labRole === 'admin' && canReviewPilot(run,job)) {
    const review = document.createElement('button'); review.type = 'button'; review.className = 'button';
    review.textContent = 'Praxistest erfassen'; review.dataset.adminOnly = '';
    review.dataset.pilotReview = '';
    review.addEventListener('click',() => openReview(run,job)); actions.append(review);
  }
  body.append(actions); row.append(summary,body); return row;
}

async function loadPilots() {
  cancelPoll('pilot');
  const generation = ++pilotLoadGeneration;
  try {
    const data = await requestHistory('/api/pilot', 'pilot-runs', loadPilots);
    if (!data || generation !== pilotLoadGeneration) return;
    if (pilotReadFailed) el('pilot-status').textContent = '';
    pilotLoaded = true; pilotReadFailed = false;
    if (!el('pilot-job').options.length) {
      for (const host of data.hosts) el('pilot-host').add(new Option(host, host));
      for (const job of data.jobs) el('pilot-job').add(new Option(job.label, job.id));
      const requestedJob = window.location.hash.slice(1);
      if (data.jobs.some(job => job.id === requestedJob)) el('pilot-job').value = requestedJob;
      el('pilot-status').textContent = '';
    }
    pilotSelectionDescription();
    pilotJobs = new Map(data.jobs.map(job => [job.id, job]));
    pilotRuns = new Map(data.runs.map(run => [run.id,run]));
    el('pilot-empty').hidden = data.pagination ? data.pagination.total > 0 : data.runs.length > 0;
    pilotBusy = data.busy ?? data.runs.some(run => run.status === 'running');
    pilotButtons();
    renderHistory('pilot-runs', data, renderPilotRun);
    if (reviewRun && el('pilot-review').open) {
      reviewRun = pilotRuns.get(reviewRun.id) || null;
      updatePilotReview();
    }
    if (selectedRun) {
      const previous = pilotLogRun?.status;
      pilotLogRun = pilotRuns.get(selectedRun) || pilotLogRun;
      if (previous === 'running' || pilotLogRun?.status === 'running') await pilotLog(selectedRun,true);
    }
    if (pilotBusy) schedulePoll('pilot', loadPilots, 2400);
  } catch (error) {
    if (generation !== pilotLoadGeneration) return;
    pilotReadFailed = true; clearPlan();
    el('pilot-status').textContent = error.message;
  }
}
el('pilot-refresh').addEventListener('click',loadPilots);

function clearPlan() {
  pilotPlanGeneration++;
  pilotPlan = null;
  el('pilot-tasks').hidden = true;
  el('pilot-task-count').hidden = true;
  el('pilot-preview-target').hidden = true;
  el('pilot-preview-images').hidden = true;
  el('pilot-preview-empty').hidden = false;
  pilotButtons();
}

el('pilot-form').addEventListener('change', () => {
  clearPlan(); pilotSelectionDescription(); el('pilot-status').textContent = '';
});
el('pilot-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (el('pilot-plan').disabled) return;
  clearPlan();
  const generation = pilotPlanGeneration;
  const host = el('pilot-host').value;
  const job = el('pilot-job').value;
  pilotPlanning = true; pilotButtons();
  el('pilot-status').textContent = 'Aufgabenliste wird gelesen …';
  try {
    const plan = await request('/api/pilot/plan', {host, job});
    if (generation !== pilotPlanGeneration || host !== el('pilot-host').value || job !== el('pilot-job').value) return;
    if (plan.host !== host || plan.job !== job || !Array.isArray(plan.tasks) || !plan.tasks.every(task => typeof task === 'string')) {
      throw new Error('Die Vorschau gehört nicht mehr zur Auswahl. Bitte erneut prüfen.');
    }
    pilotPlan = plan;
    el('pilot-tasks').replaceChildren(...plan.tasks.map(task => {
      const item = document.createElement('li');
      item.textContent = task;
      return item;
    }));
    el('pilot-tasks').hidden = false;
    el('pilot-task-count').textContent = plan.tasks.length + (plan.tasks.length === 1 ? ' Aufgabe' : ' Aufgaben');
    el('pilot-task-count').hidden = false;
    el('pilot-preview-target').textContent = host + ' · ' + (pilotJobs.get(job)?.label || job);
    el('pilot-preview-target').hidden = false; el('pilot-preview-empty').hidden = true;
    el('pilot-start').textContent = 'Auf ' + host + ' starten';
    const images = el('pilot-preview-images'); images.replaceChildren();
    if (plan.images) for (const [key,label] of [['desktop','Desktop'],['lock','Sperre & Anmeldung']]) {
      const term = document.createElement('dt'); term.textContent = label;
      const value = document.createElement('dd'); value.textContent = plan.images[key]; images.append(term,value);
    }
    images.hidden = !plan.images;
    el('pilot-status').textContent = 'Vorschau geprüft. Auf dem Pilotrechner wurde noch nichts geändert.';
  } catch (error) {
    if (generation === pilotPlanGeneration) el('pilot-status').textContent = error.message;
  } finally {
    pilotPlanning = false; pilotButtons();
  }
});

el('pilot-start').addEventListener('click', () => {
  if (el('pilot-start').disabled || !pilotPlan) return;
  const name = el('pilot-job').selectedOptions[0].text;
  const images = pilotPlan.images;
  const extra = images ? ' Desktop: ' + images.desktop + '. Sperre und Anmeldung: ' + images.lock + '.' : '';
  el('pilot-confirm-target').textContent = name + ' auf ' + pilotPlan.host + ' anwenden?' + extra;
  el('pilot-confirm').returnValue = 'cancel';
  el('pilot-confirm').showModal();
});

el('pilot-confirm').addEventListener('close', async () => {
  if (el('pilot-confirm').returnValue !== 'start' || el('pilot-start').disabled || !pilotPlan) return;
  if (pilotPlan.host !== el('pilot-host').value || pilotPlan.job !== el('pilot-job').value) { clearPlan(); return; }
  const {host, job, revision, token} = pilotPlan;
  pilotStarting = true;
  clearPlan();
  let started = null;
  try {
    const run = await request('/api/pilot/start', {host, job, revision, token});
    started = run;
    resetHistory('pilot-runs');
    el('pilot-status').textContent = 'Pilotstart angenommen. Fortschritt und Ergebnis stehen im Verlauf.';
  } catch (error) {
    el('pilot-status').textContent = error.message;
  } finally {
    pilotStarting = false;
    await loadPilots();
    if (started) await pilotLog(started.id);
    pilotButtons();
  }
});

function openReview(run, job) {
  if (labRole !== 'admin' || pilotReadFailed || !canReviewPilot(run,job) || pilotReviewBusy) return;
  reviewRun = run;
  el('pilot-review-target').textContent = run.host + ' · ' + job.label + ' · '
    + new Date(run.started).toLocaleString('de-DE',{timeZone:'Europe/Berlin',dateStyle:'short',timeStyle:'short'});
  const fieldset = el('pilot-review-checks');
  fieldset.querySelectorAll('label').forEach(label => label.remove());
  for (const [id, text] of Object.entries(job.checks)) {
    const label = document.createElement('label');
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.value = id;
    const description = document.createElement('span'); description.textContent = text;
    label.append(check, description);
    fieldset.append(label);
  }
  el('pilot-review-note').value = '';
  el('pilot-review-error').textContent = '';
  updatePilotReview();
  el('pilot-review').showModal();
}

function updatePilotReview() {
  const checks = [...el('pilot-review-checks').querySelectorAll('input')];
  const checked = checks.filter(check => check.checked).length;
  const current = labRole === 'admin' && !pilotReadFailed && canReviewPilot(reviewRun,pilotJobs.get(reviewRun?.job));
  const note = el('pilot-review-note').value;
  const tooLong = new TextEncoder().encode(note).length > 300;
  el('pilot-review-progress').textContent = checked + ' von ' + checks.length + ' Prüfpunkten bestätigt';
  el('pilot-review-accept').disabled = !current || pilotReviewBusy || tooLong || !checks.length || checked !== checks.length;
  el('pilot-review-reject').disabled = !current || pilotReviewBusy || tooLong || !note.trim();
  el('pilot-review-cancel').disabled = pilotReviewBusy;
  el('pilot-review-note').disabled = !current || pilotReviewBusy;
  for (const check of checks) check.disabled = !current || pilotReviewBusy;
  if (!current) el('pilot-review-error').textContent = 'Dieser Lauf kann nicht mehr abgenommen werden. Bitte die aktuelle Auswertung im Verlauf öffnen.';
  else if (tooLong) el('pilot-review-error').textContent = 'Die Anmerkung ist zu lang. Bitte kürzen.';
}
for (const event of ['change','input']) el('pilot-review-form').addEventListener(event, () => {
  el('pilot-review-error').textContent = ''; updatePilotReview();
});
el('pilot-review').addEventListener('cancel',event => { if (pilotReviewBusy) event.preventDefault(); });
el('pilot-review').addEventListener('close',() => { if (!pilotReviewBusy) reviewRun = null; });

el('pilot-review-cancel').addEventListener('click', () => el('pilot-review').close());
el('pilot-review-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.submitter;
  if (!reviewRun || !button || button.disabled || pilotReviewBusy || labRole !== 'admin') return;
  const checks = [...el('pilot-review-checks').querySelectorAll('input:checked')].map(input => input.value);
  pilotReviewBusy = true; updatePilotReview();
  try {
    await request('/api/pilot/review', {
      run_id: reviewRun.id, decision: button.value, checks, note: el('pilot-review-note').value,
    });
    el('pilot-review').close();
    reviewRun = null;
    el('pilot-status').textContent = button.value === 'accepted' ? 'Praxistest abgenommen.' : 'Praxistest mit Begründung abgelehnt.';
    await loadPilots();
  } catch (error) {
    el('pilot-review-error').textContent = error.message;
  } finally {
    pilotReviewBusy = false;
    if (el('pilot-review').open) updatePilotReview();
  }
});

if (currentPage === '/pilotdeploys') loadPilots();

let roomData = null;
let roomPlan = null;
let roomBusy = false;
function selectedRoomHosts() {
  return [...el('room-targets').querySelectorAll('input[data-room-host]:checked')].map(input => input.value).sort();
}
function chooseRoomHosts(hosts) {
  for (const input of el('room-targets').querySelectorAll('input[data-room-host]')) input.checked = hosts.includes(input.value);
  updateRoomActions();
}
function renderRoomTargets() {
  if (el('room-targets').querySelector('input[data-room-host]')) return;
  const hosts = roomData.scopes.room;
  const requested = window.location.hash.slice(1);
  const defaults = requested ? roomData.scopes[requested] || (hosts.includes(requested) ? [requested] : [])
    : hosts;
  const groups = new Map();
  for (const host of hosts) {
    const team = /^LAB-(\d{2})[AB]$/.exec(host)?.[1] || host;
    if (!groups.has(team)) {
      const group = document.createElement('div'); group.className = 'room-team';
      const heading = document.createElement('h3');
      const button = document.createElement('button'); button.type = 'button';
      button.className = 'room-team-toggle'; button.dataset.roomTeam = team;
      button.textContent = team === '08' ? 'Pilotrechner' : 'Team ' + team;
      heading.append(button); group.append(heading); groups.set(team, group);
      group.addEventListener('click', event => {
        if (event.target.closest('label,input') || roomBusy) return;
        const inputs = [...group.querySelectorAll('input[data-room-host]')];
        const selected = !inputs.every(input => input.checked);
        for (const input of inputs) input.checked = selected;
        updateRoomActions();
      });
    }
    const label = document.createElement('label'); label.className = 'room-device';
    const check = document.createElement('input'); check.type = 'checkbox'; check.value = host;
    check.dataset.roomHost = host; check.checked = defaults.includes(host);
    check.setAttribute('aria-label', host);
    const hint = document.createElement('small'); hint.className = 'room-access-hint'; hint.hidden = true; hint.textContent = 'Nur WOL';
    label.append(check, hint); groups.get(team).append(label);
  }
  const teacher = document.createElement('label'); teacher.className = 'room-device room-teacher';
  const check = document.createElement('input'); check.type = 'checkbox'; check.value = 'LAB-Teacher';
  check.setAttribute('aria-label', 'LAB-Teacher');
  check.dataset.roomHost = 'LAB-Teacher'; check.checked = defaults.includes('LAB-Teacher');
  const hint = document.createElement('small'); hint.textContent = 'Aufwecken · Herunterfahren';
  teacher.append(check, hint);
  for(const [team,group] of groups) {
    for(const host of LabRoomLayout.hosts(team)) {
      const label=group.querySelector('input[value="'+host+'"]')?.closest('label');
      if(label)group.append(label);
    }
  }
  LabRoomLayout.render(el('room-targets'),groups,teacher);
}
function updateRoomActions() {
  if (!roomData) return;
  const hosts = selectedRoomHosts();
  const allowed = Object.keys(roomData.actions).filter(action => hosts.every(host =>
    (roomData.target_capabilities?.[host] || (host === 'LAB-Teacher' ? ['wake'] : Object.keys(roomData.actions))).includes(action)));
  for (const option of el('room-action').options) option.disabled = !allowed.includes(option.value);
  if (!allowed.includes(el('room-action').value)) el('room-action').value = allowed[0] || '';
  el('room-prepare').disabled = roomBusy || !hosts.length || !allowed.length;
  const missing = hosts.filter(host => host !== 'LAB-Teacher' && roomData.access?.[host] === false);
  const notices = [];
  if (missing.length) notices.push(missing.length + (missing.length === 1 ? ' Client ist' : ' Clients sind') + ' noch nicht auf APP aufgenommen. Für diese Geräte ist bisher nur Aufwecken möglich.');
  if (hosts.includes('LAB-Teacher')) notices.push('Teacher: Aufwecken oder regulär herunterfahren. Lokale Sitzungen und Paketläufe blockieren das Ausschalten.');
  el('room-capability').hidden = !notices.length;
  el('room-capability').textContent = notices.join(' ');
  for (const hint of el('room-targets').querySelectorAll('.room-access-hint')) {
    const host = hint.closest('label').querySelector('input').value;
    hint.hidden = roomData.access?.[host] !== false;
    hint.title = 'Aufwecken möglich. Andere Aktionen erst nach erfolgreicher APP-Aufnahme.';
  }
  el('room-selection-count').textContent = hosts.length === 1 ? '1 Gerät ausgewählt' : hosts.length + ' Geräte ausgewählt';
  el('room-action-hint').textContent = {
    wake:'Sendet Wake-on-LAN an die ausgewählten Rechner.',
    logout:'Meldet IServ-Schüler und das Sammelkonto cisco regulär ab. Offene Arbeiten vorher speichern lassen.',
    reboot:'Startet die ausgewählten Clients neu. Angemeldete Sitzungen werden beendet.',
    poweroff:hosts.includes('LAB-Teacher')
      ? 'Fährt Schülerclients regulär herunter. Der Teacher wird nur ohne lokale Sitzung und ohne Paketlauf ausgeschaltet.'
      : 'Fährt die ausgewählten Clients regulär herunter. Angemeldete Sitzungen werden beendet.',
    demo:'Meldet das lokale Sammelkonto cisco auf freien, vorbereiteten Clients an. Ohne IServ-Freigaben.',
    'demo-end':'Meldet das lokale Sammelkonto cisco regulär ab.',
  }[el('room-action').value] || '';
  for (const input of el('room-targets').querySelectorAll('input')) input.disabled = roomBusy;
  for (const group of el('room-targets').querySelectorAll('.room-team')) {
    const inputs = [...group.querySelectorAll('input[data-room-host]')];
    const selected = inputs.every(input => input.checked) ? 'true' : inputs.some(input => input.checked) ? 'mixed' : 'false';
    const button = group.querySelector('button[data-room-team]');
    button.setAttribute('aria-pressed', selected);
    button.disabled = roomBusy;
    group.dataset.selection = selected;
  }
  for (const id of ['room-all-students', 'room-ready', 'room-with-teacher', 'room-clear', 'room-action']) el(id).disabled = roomBusy;
}
el('room-targets').addEventListener('change', updateRoomActions);
el('room-action').addEventListener('change', updateRoomActions);
el('room-all-students').addEventListener('click', () => chooseRoomHosts(roomData.scopes.room));
el('room-with-teacher').addEventListener('click', () => chooseRoomHosts(roomData.scopes['room-with-teacher']));
el('room-clear').addEventListener('click', () => chooseRoomHosts([]));
el('room-ready').addEventListener('click', () => chooseRoomHosts(roomData.scopes.room.filter(host => roomData.access?.[host] !== false)));
const roomStatus = {running: 'Läuft', finished: 'Beendet', partial: 'Teilweise fehlgeschlagen', failed: 'Fehlgeschlagen', interrupted: 'Unterbrochen'};

async function loadRoom() {
  cancelPoll('room');
  try {
    const page = await requestHistory('/api/room', 'room-runs', loadRoom);
    if (!page) return;
    roomData = page;
    if (!el('room-action').options.length) {
      for (const [value, label] of Object.entries(roomData.actions)) {
        el('room-action').add(new Option(label, value));
      }
    }
    renderRoomTargets();
    const busy = roomData.busy ?? roomData.runs.some(run => run.status === 'running');
    roomBusy = busy;
    updateRoomActions();
    el('room-activity').textContent = busy ? 'Auftrag läuft' : 'Bereit';
    el('room-activity').dataset.state = busy ? 'busy' : 'ready';
    el('room-empty').hidden = roomData.pagination.total > 0;
    renderHistory('room-runs', roomData, run => {
      const details = document.createElement('details');
      const hosts = run.targets || roomData.scopes[run.scope] || Object.keys(run.results);
      const title = document.createElement('summary');
      const action = document.createElement('span'); action.textContent = roomData.actions[run.action] || run.action;
      const time = document.createElement('time'); time.dateTime = run.started;
      time.textContent = new Date(run.started).toLocaleString('de-DE', {timeZone:'Europe/Berlin', dateStyle:'medium', timeStyle:'short'});
      const count = document.createElement('span'); count.className = 'room-run-count';
      count.textContent = hosts.length + (hosts.length === 1 ? ' Gerät' : ' Geräte');
      const failed = Object.values(run.results).filter(result => String(result).startsWith('Fehler:')).length;
      const skipped = Object.values(run.results).filter(result => String(result).startsWith('Übersprungen:')).length;
      const state = run.status === 'finished' && failed ? failed === hosts.length ? 'failed' : 'partial' : run.status;
      title.append(action, badge(roomStatus[state] || state,
        ['interrupted','partial','failed'].includes(state) ? 'warn' : 'neutral'), count);
      if (failed) title.append(badge(failed + ' Fehler', 'warn'));
      if (skipped) title.append(badge(skipped + ' übersprungen', 'neutral'));
      title.append(time);
      const list = document.createElement('ul');
      for (const host of hosts) {
        const item = document.createElement('li');
        item.textContent = host + ' · ' + (run.results[host] || (run.status === 'running' ? 'Wartet auf Ergebnis …' : 'Kein Ergebnis'));
        list.append(item);
      }
      details.append(title, list);
      return details;
    });
    if (busy) schedulePoll('room', loadRoom, 2400);
  } catch (error) {
    el('room-status').textContent = error.message;
    roomBusy = true;
    updateRoomActions();
    el('room-prepare').disabled = true;
    el('room-activity').textContent = 'Status nicht verfügbar';
    el('room-activity').dataset.state = 'error';
    schedulePoll('room', loadRoom, 6000, true);
  }
}

el('room-form').addEventListener('submit', event => {
  event.preventDefault();
  const hosts = selectedRoomHosts();
  if (roomBusy || !hosts.length) return;
  roomPlan = {action: el('room-action').value, targets: hosts, confirmed: true};
  el('room-confirm-target').textContent = roomData.actions[roomPlan.action] + ' · ' + hosts.length + (hosts.length === 1 ? ' Gerät' : ' Geräte');
  el('room-confirm-hosts').replaceChildren(...hosts.map(host => badge(host, 'neutral')));
  el('room-confirm').returnValue = 'cancel';
  el('room-confirm').showModal();
});

el('room-confirm').addEventListener('close', async () => {
  const plan = roomPlan;
  roomPlan = null;
  if (el('room-confirm').returnValue !== 'start' || !plan) return;
  roomBusy = true;
  updateRoomActions();
  try {
    await request('/api/room/start', plan);
    resetHistory('room-runs');
    el('room-status').textContent = 'Aktion gestartet. Ergebnisse erscheinen pro Rechner.';
  } catch (error) {
    el('room-status').textContent = error.message;
  } finally {
    await loadRoom();
  }
});

if (currentPage === '/sammelaktionen') loadRoom();

let backupMode = 'teacher';
let backupBusy = true;
let backupTransferAvailable = false;
let backupLoaded = false;
let backupDownloadRun = null;
let backupGeneration = 0;
let backupReadFailed = false;
let backupCleanupLoading = false;
let backupCleanupNotice = '';
const backupPreferenceKey = 'cisco-lab:backup-targets:v1:' + (basePath || '/');
function restoreBackupTargets() {
  let targets = ['apps'];
  try {
    const saved = JSON.parse(localStorage.getItem(backupPreferenceKey));
    if (Array.isArray(saved) && saved.length <= 3 && saved.every(target => ['apps','mac','download'].includes(target))) targets = saved;
  } catch (_) { /* Storage may be blocked; the local APP default still works. */ }
  for (const input of document.querySelectorAll('[data-backup-target]')) input.checked = targets.includes(input.dataset.backupTarget);
}
function backupDate(value) {
  const date = new Date(value);
  return value && !Number.isNaN(date.getTime()) ? date.toLocaleString('de-DE', {
    dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/Berlin'
  }) : 'Zeitpunkt unbekannt';
}
function backupSize(value) {
  if (!Number.isFinite(value) || value <= 0) return '';
  const unit = value >= 1024 ** 3 ? 'GiB' : 'MiB';
  return (value / 1024 ** (unit === 'GiB' ? 3 : 2)).toLocaleString('de-DE', {maximumFractionDigits: 1}) + ' ' + unit;
}
function backupState(run) {
  if (run.pruned_at) return ['Archiv entfernt', 'neutral'];
  return ({running: ['Läuft', 'warn'], queued: ['Wartet', 'neutral'], finished: ['Gesichert', 'good'],
    failed: ['Fehlgeschlagen', 'warn'], interrupted: ['Unterbrochen', 'warn']})[run.status] || ['Status unbekannt', 'neutral'];
}
function backupProofs(run) {
  const running = ['running', 'queued'].includes(run.status);
  const targets = run.targets || ['apps', ...(run.transfer || run.copied ? ['mac'] : [])];
  const local = !targets.includes('apps') ? ['neutral', 'Keine APP-Kopie beauftragt', '']
    : run.pruned_at || run.apps_removed_at ? ['neutral', 'Lokales Archiv entfernt', run.pruned_reason === 'manual_cleanup' || run.apps_removed_at ? 'Manuell bereinigt.' : 'Aufbewahrungsfrist beendet.']
    : run.data_copy ? ['good', 'Lokal gesichert', 'Verschlüsseltes Archiv auf ' + (backupMode === 'apps' ? 'APP.' : 'data.')]
    : [running ? 'neutral' : 'warn', running ? 'Lokales Archiv ausstehend' : 'Kein lokaler Archivnachweis', ''];
  const remote = !targets.includes('mac') ? ['neutral', 'Keine Mac-Kopie beauftragt', '']
    : (run.pruned_at || run.mac_removed_at) && run.copied ? ['neutral', 'Externe Kopie entfernt', run.pruned_reason === 'manual_cleanup' || run.mac_removed_at ? 'Manuell bereinigt.' : 'Aufbewahrungsfrist beendet.']
    : run.copied ? ['good', 'Externe Kopie übertragen', run.remote_sha256_verified ? 'Externe Prüfsumme bestätigt.' : 'Keine externe Prüfsummenbestätigung gespeichert.']
    : [running ? 'neutral' : 'warn', running ? 'Externe Kopie ausstehend' : 'Keine externe Kopie bestätigt', ''];
  const restore = run.remote_restore_verified_at
    ? ['good', 'Aus Mac-Kopie wiederhergestellt', backupDate(run.remote_restore_verified_at)]
    : run.restore_verified_at ? [backupMode === 'apps' ? 'warn' : 'good', 'Lokal wiederhergestellt',
      backupDate(run.restore_verified_at) + (backupMode === 'apps' ? ' · Externe Wiederherstellung nicht nachgewiesen.' : '')]
    : ['neutral', 'Wiederherstellung ungeprüft', 'Eine erfolgreiche Übertragung ist noch kein Wiederherstellungsnachweis.'];
  const proofs = [local, remote];
  if (targets.includes('download')) proofs.push(run.download_available
    ? ['good', 'ZIP-Download bereit', run.download_expires_at ? 'Abrufbar bis ' + backupDate(run.download_expires_at) : 'Aus dem APP-Archiv abrufbar.']
    : [running ? 'neutral' : 'warn', running ? 'ZIP-Download ausstehend' : 'ZIP-Download nicht verfügbar', run.download_expires_at ? 'Temporärer Download abgelaufen.' : '']);
  return [...proofs, restore];
}
function backupChecks(run) {
  return backupProofs(run).map(([state, title, detail]) => {
    const item = document.createElement('li');
    item.dataset.state = state;
    const mark = document.createElement('span');
    mark.className = 'backup-proof-mark'; mark.textContent = state === 'good' ? '✓' : state === 'warn' ? '!' : '–';
    mark.setAttribute('aria-hidden', 'true');
    const text = document.createElement('div');
    const strong = document.createElement('strong'); strong.textContent = title;
    text.append(strong);
    if (detail) { const small = document.createElement('small'); small.textContent = detail; text.append(small); }
    item.append(mark, text); return item;
  });
}
function updateBackupButtons() {
  const selected = selectedBackupTargets();
  el('backup-start').disabled = labRole !== 'admin' || !backupLoaded || backupBusy || !selected.length
    || (selected.includes('mac') && !backupTransferAvailable);
  el('backup-selection-count').textContent = selected.length ? selected.length + (selected.length === 1 ? ' Ziel ausgewählt' : ' Ziele ausgewählt') : 'Bitte ein Ziel auswählen';
  for (const input of document.querySelectorAll('[data-backup-target]')) {
    input.disabled = labRole !== 'admin' || !backupLoaded || backupBusy || (input.dataset.backupTarget === 'mac' && !backupTransferAvailable);
  }
  el('backup-cleanup').hidden = labRole !== 'admin' || backupMode !== 'apps';
  el('backup-cleanup').disabled = labRole !== 'admin' || !backupLoaded || backupBusy || backupCleanupLoading;
}
function selectedBackupTargets() {
  return [...document.querySelectorAll('[data-backup-target]:checked')].map(input => input.dataset.backupTarget);
}
function renderBackupCleanup(job) {
  const target = el('backup-cleanup-status');
  if (job?.status === 'running') {
    target.textContent = 'Bereinigung läuft · ' + job.processed + ' von ' + job.total + ' Archiven bearbeitet.';
  } else if (backupCleanupNotice) {
    target.textContent = backupCleanupNotice;
  } else if (job) {
    target.textContent = job.removed + ' ältere Sicherungen bereinigt · APP: ' + (backupSize(job.bytes.apps) || '0 B')
      + ' · Mac: ' + (backupSize(job.bytes.mac) || '0 B') + '.';
    if (job.errors.length) target.textContent += ' ' + job.errors.length + ' Archive konnten nicht vollständig bereinigt werden.';
    if (job.status === 'interrupted') target.textContent += ' Der Lauf wurde unterbrochen; die Bereinigung kann erneut geprüft werden.';
  } else {
    target.textContent = '';
  }
}
function backupDownloadLink(link, run) {
  link.href = basePath + '/api/backup/download/' + encodeURIComponent(run.id);
  link.download = (run.file || 'CISCO-LAB-Sicherung.tar.gz.gpg').replace(/\.tar\.gz\.gpg$/, '.zip'); link.dataset.adminOnly = '';
  link.addEventListener('click', event => { if (labRole !== 'admin' || backupBusy) event.preventDefault(); });
}
function renderBackupLocations(data) {
  el('backup-local-label').textContent = backupMode === 'apps' ? 'APP' : 'Teacher';
  for (const target of ['apps', 'mac', 'download']) {
    const location = data.locations?.[target];
    if (target !== 'download') el('backup-' + target + '-path').textContent = location?.path || '';
    el('backup-' + target + '-last').textContent = target === 'mac' && !backupTransferAvailable ? 'Externer Zugang nicht eingerichtet'
      : location?.last ? (target === 'download' ? 'Letztes ZIP bereitgestellt: ' : 'Letzte Sicherung: ') + backupDate(location.last.time)
      : target === 'download' ? 'Ohne APP-Kopie 24 Stunden abrufbar' : 'Noch keine Sicherung gespeichert';
  }
}
function renderLatestBackup(run) {
  const [label, color] = run ? backupState(run) : ['Noch keine Sicherung', 'neutral'];
  el('backup-latest-state').className = 'badge ' + color;
  el('backup-latest-state').textContent = label;
  el('backup-latest-time').textContent = run ? backupDate(run.started) : 'Noch kein Sicherungslauf';
  el('backup-latest-file').textContent = run ? [run.file, backupSize(run.size)].filter(Boolean).join(' · ') : '';
  el('backup-latest-checks').replaceChildren(...(run ? backupChecks(run) : []));
  el('backup-latest-confirmed').hidden = !run?.confirmed_good_at;
  el('backup-latest-confirmed').textContent = run?.confirmed_good_at ? 'Als funktionierend bestätigt · ' + backupDate(run.confirmed_good_at) : '';
  el('backup-latest-error').hidden = !run?.error;
  el('backup-latest-error').textContent = run?.error || '';
}
function renderBackupRun(run) {
  const item = document.createElement('details');
  const summary = document.createElement('summary');
  const time = document.createElement('time'); time.textContent = backupDate(run.started);
  if (run.started) time.dateTime = run.started;
  summary.append(time, badge(...backupState(run)));
  if (run.confirmed_good_at) summary.append(badge('Bestätigt', 'good'));
  const size = backupSize(run.size);
  if (size) { const span = document.createElement('span'); span.className = 'backup-run-size'; span.textContent = size; summary.append(span); }
  const body = document.createElement('div'); body.className = 'backup-run-detail';
  const file = document.createElement('p'); file.className = 'backup-filename';
  file.textContent = run.file || 'Noch kein Archiv gespeichert.';
  const checks = document.createElement('ul'); checks.className = 'backup-checks'; checks.append(...backupChecks(run));
  const scope = document.createElement('p'); scope.className = 'field-hint';
  scope.textContent = run.lab_system_copy ? 'LAB-Dienste und Webroute enthalten.'
    : run.root_system_copy ? 'Rootgeschützte Systemdateien enthalten.' : 'Kein Nachweis für rootgeschützte Systemdateien gespeichert.';
  body.append(file, checks, scope);
  if (run.confirmed_good_at) {
    const confirmed = document.createElement('p'); confirmed.className = 'backup-confirmed-note';
    confirmed.textContent = 'Als funktionierend bestätigt · ' + backupDate(run.confirmed_good_at); body.append(confirmed);
  }
  if (run.error) { const error = document.createElement('p'); error.className = 'notice'; error.textContent = run.error; body.append(error); }
  if (run.status === 'finished' && !run.pruned_at && !run.confirmed_good_at
      && run.data_copy && !run.apps_removed_at && !run.mac_removed_at && (backupMode === 'teacher' || (run.copied && run.remote_restore_verified_at))) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'button log-button'; button.dataset.adminOnly = '';
    button.textContent = 'Wiederherstellung bestätigen'; button.disabled = backupBusy || labRole !== 'admin';
    button.addEventListener('click', () => confirmCatalog(
      'Diese Sicherung wurde erfolgreich wiederhergestellt?',
      async () => {
        try {
          await request('/api/backup/confirm', {run_id: run.id, confirmed: true});
          el('backup-status').textContent = 'Wiederherstellung bestätigt.';
          await loadBackups();
        } catch (error) { el('backup-status').textContent = error.message; }
      }, 'Als funktionierend bestätigen'));
    body.append(button);
  }
  if (run.download_available && labRole === 'admin') {
    const link = document.createElement('a'); link.className = 'button log-button'; link.textContent = 'ZIP herunterladen';
    backupDownloadLink(link, run); body.append(link);
  }
  item.append(summary, body); return item;
}
async function loadBackups() {
  cancelPoll('backup');
  const generation = ++backupGeneration;
  try {
    const data = await requestHistory('/api/backup', 'backup-runs', loadBackups);
    if (!data || generation !== backupGeneration) return;
    backupMode = data.mode;
    backupLoaded = true;
    backupBusy = data.busy ?? data.runs.some(run => ['running', 'queued'].includes(run.status));
    backupTransferAvailable = Boolean(data.transfer_available);
    if (!backupTransferAvailable) document.querySelector('[data-backup-target="mac"]').checked = false;
    updateBackupButtons();
    el('backup-activity').textContent = data.cleanup?.status === 'running' ? 'Bereinigung läuft' : backupBusy ? 'Sicherung läuft' : 'Bereit';
    el('backup-activity').dataset.state = backupBusy ? 'busy' : 'ready';
    renderBackupLocations(data);
    renderLatestBackup(data.latest || data.runs[0]);
    el('backup-empty').hidden = data.pagination ? data.pagination.total > 0 : data.runs.length > 0;
    renderHistory('backup-runs', data, renderBackupRun);
    renderBackupCleanup(data.cleanup);
    if (backupDownloadRun && !backupBusy) {
      const run = [data.latest, ...data.runs].find(run => run?.id === backupDownloadRun);
      if (run?.download_available) {
        const link = el('backup-download-ready'); link.hidden = false;
        link.href = basePath + '/api/backup/download/' + encodeURIComponent(run.id);
        link.download = (run.file || 'CISCO-LAB-Sicherung.tar.gz.gpg').replace(/\.tar\.gz\.gpg$/, '.zip');
        link.click(); backupDownloadRun = null;
        el('backup-status').textContent = 'ZIP-Download angefordert. Bei Bedarf kannst du ihn hier erneut starten.';
      } else if (run && ['failed', 'interrupted'].includes(run.status)) {
        backupDownloadRun = null;
      }
    }
    if (backupReadFailed) el('backup-status').textContent = '';
    backupReadFailed = false;
    if (backupBusy) schedulePoll('backup', loadBackups, 2400);
  } catch (error) {
    if (generation !== backupGeneration) return;
    backupBusy = true; backupReadFailed = true; updateBackupButtons();
    el('backup-activity').textContent = 'Status nicht verfügbar';
    el('backup-activity').dataset.state = 'error';
    el('backup-status').textContent = error.message;
  }
}

for (const input of document.querySelectorAll('[data-backup-target]')) input.addEventListener('change', () => {
  if (labRole === 'admin') {
    try { localStorage.setItem(backupPreferenceKey,JSON.stringify(selectedBackupTargets())); } catch (_) { /* Optional browser preference. */ }
  }
  updateBackupButtons();
});
el('backup-cleanup').addEventListener('click',async () => {
  if (el('backup-cleanup').disabled) return;
  backupCleanupLoading = true; backupCleanupNotice = 'Ältere Sicherungen werden geprüft …'; updateBackupButtons();
  renderBackupCleanup(null);
  try {
    const plan = await request('/api/backup/cleanup');
    if (!plan.candidates.length) {
      backupCleanupNotice = 'Keine älteren Sicherungen zum Löschen vorhanden. Die neueste APP-Sicherung, die neueste Mac-Kopie und die letzte bestätigte Wiederherstellung bleiben erhalten. Bereits bereinigte Einträge bleiben als Verlauf sichtbar.';
      if (plan.skipped.length) backupCleanupNotice += ' ' + plan.skipped.length + ' uneindeutige Einträge wurden ausgelassen.';
      renderBackupCleanup(null);
      confirmCatalog(backupCleanupNotice, null, '', 'Sicherungen bereits bereinigt');
      return;
    }
    let description = plan.candidates.length + ' ältere Sicherungen endgültig entfernen? APP: '
      + (backupSize(plan.bytes.apps) || '0 B') + ' · Mac: ' + (backupSize(plan.bytes.mac) || '0 B')
      + '. Die letzte geprüfte Wiederherstellung und die jeweils neueste APP-/Mac-Kopie bleiben geschützt.';
    if (plan.skipped.length) description += ' ' + plan.skipped.length + ' uneindeutige Einträge bleiben erhalten.';
    backupCleanupNotice = ''; renderBackupCleanup(null);
    confirmCatalog(description,async () => {
      backupBusy = true; updateBackupButtons();
      try {
        await request('/api/backup/cleanup',{token:plan.token,confirmed:true});
        backupCleanupNotice = '';
      } catch (error) { backupCleanupNotice = error.message; }
      finally { await loadBackups(); }
    },'Endgültig bereinigen','Alte Sicherungen bereinigen');
  } catch (error) {
    backupCleanupNotice = error.message; renderBackupCleanup(null);
    confirmCatalog(backupCleanupNotice, null, '', 'Bereinigung konnte nicht geprüft werden');
  } finally {
    backupCleanupLoading = false; updateBackupButtons();
  }
});
el('backup-download-ready').dataset.adminOnly = '';
el('backup-download-ready').addEventListener('click', event => { if (labRole !== 'admin' || backupBusy) event.preventDefault(); });
el('backup-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (el('backup-start').disabled) return;
  const targets = selectedBackupTargets();
  backupBusy = true;
  updateBackupButtons();
  try {
    const run = await request('/api/backup/start', {targets});
    backupDownloadRun = targets.includes('download') ? run.id : null;
    el('backup-download-ready').hidden = true;
    resetHistory('backup-runs');
    el('backup-status').textContent = 'Sicherung gestartet.';
  } catch (error) {
    el('backup-status').textContent = error.message;
  } finally {
    await loadBackups();
  }
});

if (currentPage === '/backup') { restoreBackupTargets(); loadBackups(); }

let loginLogGeneration = 0;
async function loadLoginLog() {
  if (labRole !== 'admin') return;
  const generation = ++loginLogGeneration;
  el('login-log-refresh').disabled = true;
  el('login-log-activity').textContent = 'Wird geladen'; el('login-log-activity').dataset.state = 'loading';
  try {
    const data = await requestHistory('/api/logins', 'login-log-rows', loadLoginLog);
    if (!data || generation !== loginLogGeneration) return;
    const results = {success: ['Angemeldet', 'good'], denied: ['Abgewiesen', 'warn'], failed: ['Fehlgeschlagen', 'warn']};
    const reasons = {not_collegium: 'Nicht Mitglied des Kollegiums', provider_denied: 'Von IServ abgewiesen / abgebrochen; kein Name übermittelt',
      verification_failed: 'Anmeldedaten nicht sicher verifiziert', provider_unavailable: 'IServ vorübergehend nicht erreichbar'};
    const date = new Intl.DateTimeFormat('de-DE', {dateStyle:'medium',timeZone:'Europe/Berlin'});
    const clock = new Intl.DateTimeFormat('de-DE', {timeStyle:'medium',timeZone:'Europe/Berlin'});
    renderHistory('login-log-rows', data, row => {
      const tr = document.createElement('tr');
      const time = document.createElement('td'); time.dataset.label = 'Zeitpunkt';
      const stamp = document.createElement('time'); stamp.dateTime = row.time; stamp.textContent = date.format(new Date(row.time));
      const hour = document.createElement('small'); hour.textContent = clock.format(new Date(row.time)); time.append(stamp,hour); time.className = 'login-time';
      const person = document.createElement('td');
      const name = document.createElement('strong'); name.textContent = row.name || row.account || 'IServ hat keinen Namen übermittelt';
      const account = document.createElement('small'); account.textContent = row.account || 'Konto nicht verifiziert';
      person.append(name, account); person.className = 'login-person'; person.dataset.label = 'Name / Konto';
      const outcome = document.createElement('td'); outcome.dataset.label = 'Ergebnis'; outcome.append(badge(...(results[row.outcome] || results.failed)));
      const detail = document.createElement('td'); detail.textContent = row.outcome === 'success'
        ? row.role === 'admin' ? 'Administrator' : 'Kollegium · Lesezugriff' : reasons[row.reason] || 'Zugang nicht erlaubt';
      detail.dataset.label = 'Rolle / Hinweis'; tr.append(time, person, outcome, detail); return tr;
    }, 'records');
    el('login-log-status').textContent = data.pagination.total + (data.pagination.total === 1 ? ' Anmeldung' : ' Anmeldungen') + ' · neueste zuerst.';
    el('login-log-empty').hidden = data.pagination.total > 0;
    el('login-log-activity').textContent = 'Protokoll geladen'; el('login-log-activity').dataset.state = 'ready';
  } catch (error) {
    if (generation !== loginLogGeneration) return;
    el('login-log-status').textContent = error.message;
    el('login-log-activity').textContent = 'Nicht verfügbar'; el('login-log-activity').dataset.state = 'error';
  } finally { if (generation === loginLogGeneration) el('login-log-refresh').disabled = false; }
}
el('login-log-refresh').addEventListener('click',loadLoginLog);
if (currentPage === '/anmeldungen') loadLoginLog();

let appearanceData = null;
let appearanceLoading = false;
let appearanceReleaseBusy = false;
let appearanceReleaseReadFailed = false;
let appearanceLogKey = null;
let appearanceUploading = false;

function appearanceDirty() {
  return Boolean(appearanceData && ['desktop', 'lock'].some(key => el('appearance-' + key).value !== appearanceData.selected[key]));
}
function updateAppearanceActions() {
  const dirty = appearanceDirty();
  const blocked = labRole !== 'admin' || !appearanceData || appearanceLoading || appearanceReleaseBusy || appearanceUploading;
  el('appearance-save').disabled = blocked;
  for (const key of ['desktop', 'lock']) el('appearance-' + key).disabled = blocked;
  el('appearance-upload-files').disabled = blocked;
  el('appearance-upload-start').disabled = blocked || !el('appearance-upload-files').files.length;
  el('appearance-release-start').disabled = blocked || dirty || !releaseData?.approval;
  for (const retry of document.querySelectorAll('[data-appearance-retry]')) retry.disabled = el('appearance-release-start').disabled;
  el('appearance-release-refresh').disabled = el('appearance-release-start').disabled;
  el('appearance-selection-hint').textContent = dirty ? 'Auswahl geändert · vor der Pilotprüfung speichern.' : 'Speichern ändert noch keinen Client.';
  const activity = el('appearance-activity');
  activity.textContent = appearanceReleaseReadFailed ? 'Status nicht verfügbar' : appearanceUploading ? 'Bilder werden hochgeladen' : appearanceLoading ? 'Wird geladen' : appearanceReleaseBusy ? 'Verteilung läuft' : dirty ? 'Ungespeicherte Auswahl' : appearanceData ? 'Bereit' : 'Nicht verfügbar';
  activity.dataset.state = appearanceReleaseReadFailed ? 'error' : appearanceLoading ? 'loading' : appearanceReleaseBusy || appearanceUploading || dirty ? 'busy' : appearanceData ? 'ready' : 'error';
  const link = el('appearance-pilot-link');
  link.hidden = dirty || !appearanceData || appearanceLoading;
  el('appearance-pilot-status').textContent = dirty ? 'Zuerst die geänderte Auswahl speichern. Die bisherige Abnahme gehört zum gespeicherten Stand.'
    : releaseData?.approval ? 'Aktueller Bildstand abgenommen auf ' + releaseData.approval.host + '.' : 'Die Abnahme gilt genau für den gespeicherten Bildstand.';
}
function updateImagePreview(key) {
  const name = el('appearance-' + key).value;
  const saved = name === appearanceData?.selected[key];
  const token = saved ? appearanceData?.previews?.[key]?.active
    : name ? appearanceData?.image_previews?.[name] : appearanceData?.previews?.[key]?.standard;
  el('appearance-' + key + '-caption').textContent = (saved ? 'Gespeichert · ' : 'Vorschau · ') + (name || 'LAB-Standardbild');
  const oldImage = el('appearance-' + key + '-preview');
  const placeholder = el('appearance-' + key + '-placeholder');
  if (token && oldImage.dataset.previewToken === token && (!oldImage.hidden || oldImage.dataset.loading === 'true')) return;
  const image = oldImage.cloneNode(false);
  image.hidden = true; image.removeAttribute('src'); image.dataset.previewToken = token || ''; image.dataset.loading = token ? 'true' : 'false';
  oldImage.replaceWith(image);
  placeholder.hidden = false;
  placeholder.textContent = token ? 'Vorschau wird geladen …' : 'Für diese Auswahl ist keine Vorschau verfügbar.';
  if (token) {
    image.addEventListener('load', () => { if (image.isConnected) { image.dataset.loading = 'false'; image.hidden = false; placeholder.hidden = true; } });
    image.addEventListener('error', () => { if (image.isConnected) { image.dataset.loading = 'false'; image.hidden = true; placeholder.hidden = false; placeholder.textContent = 'Vorschau nicht verfügbar. Bitte Bilder neu laden.'; } });
    image.src = basePath + '/api/appearance/preview/' + encodeURIComponent(token);
  }
}
function renderImages(data, draft = null) {
  appearanceData = data;
  el('appearance-folder').textContent = data.folder;
  for (const key of ['desktop', 'lock']) {
    const select = el('appearance-' + key);
    const choices = new Set(data.images);
    if (data.selected[key]) choices.add(data.selected[key]);
    if (draft?.[key]) choices.add(draft[key]);
    const standard = new Option(data.previews?.[key]?.standard ? 'LAB-Standardbild' : 'LAB-Standardbild (nicht vorhanden)', '');
    standard.disabled = !data.previews?.[key]?.standard;
    select.replaceChildren(standard,
      ...[...choices].map(name => new Option(name, name)));
    select.value = draft?.[key] ?? data.selected[key];
    updateImagePreview(key);
  }
  const count = data.images.length === 1 ? '1 Bild verfügbar' : data.images.length + ' Bilder verfügbar';
  el('appearance-library-count').textContent = count + (data.excluded ? ' · ' + data.excluded + ' leer oder zu groß' : '');
  updateAppearanceActions();
}

async function loadImages() {
  const refresh = el('appearance-refresh');
  if (appearanceLoading || appearanceUploading) return;
  const draft = appearanceDirty() ? Object.fromEntries(['desktop', 'lock'].map(key => [key, el('appearance-' + key).value])) : null;
  appearanceLoading = true; updateAppearanceActions();
  refresh.disabled = true;
  el('appearance-status').textContent = 'Bilderordner wird gelesen …';
  try {
    const data = await request('/api/appearance');
    renderImages(data, draft);
    el('appearance-status').textContent = draft ? 'Bildliste aktualisiert. Deine ungespeicherte Auswahl bleibt erhalten.' : '';
  } catch (error) {
    el('appearance-status').textContent = error.message;
  } finally {
    refresh.disabled = false; appearanceLoading = false; updateAppearanceActions();
  }
}

el('appearance-refresh').addEventListener('click', () => { loadImages(); loadAppearanceRelease(); });
el('appearance-upload-files').addEventListener('change', updateAppearanceActions);
el('appearance-upload-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (el('appearance-upload-start').disabled) return;
  const files = [...el('appearance-upload-files').files];
  const draft = Object.fromEntries(['desktop','lock'].map(key => [key,el('appearance-' + key).value]));
  appearanceUploading = true; updateAppearanceActions(); el('appearance-refresh').disabled = true;
  const uploaded = [], failed = [];
  for (const [index,file] of files.entries()) {
    el('appearance-upload-status').textContent = 'Bild ' + (index + 1) + ' von ' + files.length + ' · ' + file.name + ' wird hochgeladen …';
    try {
      if (!/\.(png|jpe?g)$/i.test(file.name) || !file.size || file.size > 48 * 1024 * 1024) throw new Error('Bitte PNG oder JPEG bis 48 MiB wählen.');
      const response = await fetch(basePath + '/api/appearance/upload', {method:'POST',
        headers:{'Content-Type':'application/octet-stream','X-LAB-Image-Name':encodeURIComponent(file.name),'X-CSRF-Token':csrfToken},body:file});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Hochladen fehlgeschlagen.');
      renderImages(data.choices, draft); uploaded.push(data.uploaded);
    } catch (error) { failed.push(file.name + ': ' + error.message); }
  }
  el('appearance-upload-status').textContent = (uploaded.length ? uploaded.length + (uploaded.length === 1 ? ' Bild hochgeladen: ' : ' Bilder hochgeladen: ') + uploaded.join(', ') + '. Jetzt oben auswählen und speichern.' : '')
    + (failed.length ? ' ' + failed.join(' · ') : '');
  el('appearance-upload-files').value = '';
  appearanceUploading = false; el('appearance-refresh').disabled = false; updateAppearanceActions();
});
for (const key of ['desktop', 'lock']) el('appearance-' + key).addEventListener('change', () => {
  updateImagePreview(key); updateAppearanceActions();
  el('appearance-status').textContent = '';
});
el('appearance-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (el('appearance-save').disabled) return;
  appearanceLoading = true; updateAppearanceActions();
  el('appearance-refresh').disabled = true;
  el('appearance-status').textContent = 'Bildauswahl wird gespeichert …';
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
    appearanceLoading = false; el('appearance-refresh').disabled = false; updateAppearanceActions();
  }
});

let releaseData = null;
let releasePlan = null;
const releaseStatus = {running: 'Läuft', finished: 'Beendet', failed: 'Fehlgeschlagen', interrupted: 'Unterbrochen'};

async function loadAppearanceRelease() {
  cancelPoll('appearance');
  try {
    const page = await requestHistory('/api/appearance/rollout', 'appearance-release-runs', loadAppearanceRelease);
    if (!page) return;
    releaseData = page;
    appearanceReleaseReadFailed = false;
    const {approval, targets, runs} = releaseData;
    const busy = releaseData.busy ?? runs.some(run => run.status === 'running');
    appearanceReleaseBusy = busy;
    updateAppearanceActions();
    const releaseBadge = el('appearance-release-badge');
    releaseBadge.className = 'badge ' + (busy ? 'warn' : approval ? 'good' : 'neutral');
    releaseBadge.textContent = busy ? 'Läuft' : approval ? 'Freigegeben' : 'Abnahme offen';
    el('appearance-release-status').textContent = busy ? 'Raumverteilung läuft …' : approval
      ? 'Gespeicherter Bildstand abgenommen auf ' + approval.host + '. Bereit zur Verteilung auf ' + targets.length + ' Schülerclients.'
      : 'Nach der Pilotabnahme kannst du diesen Bildstand im Raum verteilen.';
    el('appearance-release-empty').hidden = releaseData.pagination ? releaseData.pagination.total > 0 : runs.length > 0;
    renderHistory('appearance-release-runs', releaseData, run => {
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      const assigned = run.targets || targets;
      const title = document.createElement('strong'); title.textContent = 'Bilder verteilen';
      const finished = document.createElement('span'); finished.className = 'field-hint'; finished.textContent = Object.keys(run.results).length + ' / ' + assigned.length + ' Rechner';
      const time = document.createElement('time'); time.textContent = new Date(run.started).toLocaleString('de-DE', {dateStyle: 'short', timeStyle: 'short'}); time.dateTime = run.started;
      summary.append(title, badge(releaseStatus[run.status] || run.status, run.status === 'running' ? 'warn' : ['failed','interrupted'].includes(run.status) ? 'warn' : 'neutral'), finished, time);
      const list = document.createElement('ul'); list.className = 'appearance-result-list';
      for (const host of assigned) {
        const item = document.createElement('li');
        const pending = run.status !== 'running' ? 'Kein Ergebnis'
          : run.active?.includes(host) ? 'In Arbeit' : 'Wartet';
        const name = document.createElement('strong'); name.textContent = host;
        const result = document.createElement('span'); result.textContent = run.results[host] || pending;
        item.append(name, result);
        if (run.results[host]) {
          const log = document.createElement('button');
          log.type = 'button';
          log.className = 'button log-button';
          log.textContent = 'Protokoll';
          log.addEventListener('click', async () => {
            const key = run.id + '/' + host;
            appearanceLogKey = key;
            el('appearance-release-log-title').textContent = host + ' · ' + new Date(run.started).toLocaleString('de-DE');
            el('appearance-release-log').hidden = false;
            const output = el('appearance-release-output');
            output.hidden = false; output.textContent = 'Protokoll wird geladen …';
            try {
              const data = await request('/api/appearance/rollout/log/' + key);
              if (appearanceLogKey === key) output.textContent = data.output;
            } catch (error) {
              if (appearanceLogKey === key) output.textContent = error.message;
            }
          });
          item.append(' ', log);
        }
        list.append(item);
      }
      details.append(summary);
      if (run.error) { const error = document.createElement('p'); error.className = 'notice'; error.textContent = run.error; details.append(error); }
      if (approval && !busy && run.pending?.length && run.revision === approval.revision) {
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'button log-button';
        retry.textContent = 'Ausstehende Rechner nachholen';
        retry.addEventListener('click', () => prepareAppearanceRelease(run.id, run.pending));
        retry.dataset.adminOnly = '';
        retry.dataset.appearanceRetry = '';
        retry.disabled = el('appearance-release-start').disabled;
        details.append(retry);
      }
      details.append(list);
      return details;
    });
    if (busy) schedulePoll('appearance', loadAppearanceRelease, 2500);
  } catch (error) {
    releaseData = null; appearanceReleaseReadFailed = true; appearanceReleaseBusy = true; updateAppearanceActions();
    el('appearance-activity').textContent = 'Status nicht verfügbar'; el('appearance-activity').dataset.state = 'error';
    el('appearance-release-badge').textContent = 'Nicht verfügbar';
    el('appearance-release-badge').className = 'badge warn';
    el('appearance-release-status').textContent = error.message;
  }
}

function prepareAppearanceRelease(retry, targets) {
  if (el('appearance-release-start').disabled || !releaseData?.approval || !targets?.length) return;
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
  if (el('appearance-release-start').disabled || releasePlan.run_id !== releaseData?.approval?.id) { releasePlan = null; return; }
  appearanceReleaseBusy = true; updateAppearanceActions();
  try {
    await request('/api/appearance/rollout/start', releasePlan);
    resetHistory('appearance-release-runs');
    await loadAppearanceRelease();
  } catch (error) {
    el('appearance-release-status').textContent = error.message;
    appearanceReleaseBusy = false;
    updateAppearanceActions();
  } finally {
    releasePlan = null;
  }
});
el('appearance-release-close').addEventListener('click', () => {
  appearanceLogKey = null;
  el('appearance-release-log').hidden = true;
  el('appearance-release-output').hidden = true;
  el('appearance-release-output').textContent = '';
});

if (currentPage === '/desktopbilder') {
  loadImages();
  loadAppearanceRelease();
}

// All room views share the same demand-driven, server-cached reachability probe.
const presencePage = ['/', '/sammelaktionen', '/repo-software', '/unterricht'].includes(currentPage);
let presenceData = null;
let presenceTimer = null;
let presenceLoading = false;
let presencePaused = false;
function pausePresence(paused) {
  presencePaused=paused;clearTimeout(presenceTimer);LabRoomLayout.pausePresence(paused);
  if(!paused)loadPresence();
}
async function loadPresence() {
  clearTimeout(presenceTimer);
  if (document.hidden || presenceLoading || presencePaused || !presencePage || authenticationExpired) return;
  presenceLoading = true;
  let delay = 30000;
  try {
    presenceData = await request('/api/presence');
    LabRoomLayout.updatePresence(presenceData);
    const age = presenceData.age_seconds || 0;
    delay = presenceData.refreshing ? 2000 : Math.max(1000, (presenceData.interval_seconds - age) * 1000 + 200);
  } catch (error) {
    presenceData = null; LabRoomLayout.updatePresence(null,true);
  } finally {
    presenceLoading = false;
    if (!document.hidden && !presencePaused && !authenticationExpired) presenceTimer = setTimeout(loadPresence, delay);
  }
}
document.addEventListener('visibilitychange', () => {
  clearTimeout(presenceTimer);
  if (!document.hidden && presencePage) loadPresence();
});
window.addEventListener('pagehide', () => clearTimeout(presenceTimer));
window.addEventListener('pageshow', event => {
  if (event.persisted && presencePage) loadPresence();
});
if (presencePage) loadPresence();
loadActivity();
