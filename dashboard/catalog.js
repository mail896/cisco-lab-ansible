'use strict';

let confirmedAction = null;
const catalogDialog = el('catalog-confirm');

function confirmCatalog(text, action, label = 'Jetzt starten', title = 'Verwaltungsauftrag bestätigen') {
  el('catalog-confirm-title').textContent = title;
  el('catalog-confirm-text').textContent = text;
  const start = catalogDialog.querySelector('button[value="start"]');
  start.textContent = label;
  start.hidden = !action;
  catalogDialog.querySelector('button[value="cancel"]').textContent = action ? 'Abbrechen' : 'Schließen';
  confirmedAction = action;
  catalogDialog.returnValue = 'cancel';
  catalogDialog.showModal();
}

catalogDialog.addEventListener('close', async () => {
  const action = confirmedAction;
  confirmedAction = null;
  if (catalogDialog.returnValue === 'start' && action) await action();
});

let catalogEntries = {};
let catalogRelease = null;
let catalogStateVersion = 0;
let catalogSourceVersion = 0;
let catalogLogVersion = 0;
let catalogLog = null;
let catalogSourceJob = null;
let catalogBusy = false;
let catalogStarting = false;
const catalogLabels = {
  running: 'Läuft', finished: 'Beendet', failed: 'Fehlgeschlagen',
  interrupted: 'Unterbrochen', succeeded: 'Erfolgreich', queued: 'Wartet', stopped: 'Angehalten',
};

async function showLog(endpoint, target, refresh = false) {
  const version = ++catalogLogVersion;
  const output = el(target);
  if (!refresh || output.hidden) output.textContent = 'Protokoll wird geladen …';
  el('catalog-log-panel').hidden = false; output.hidden = false;
  try {
    const data = await request(endpoint);
    if (version === catalogLogVersion && catalogLog) output.textContent = data.output;
  } catch (error) {
    if (version === catalogLogVersion && catalogLog) output.textContent = error.message;
  }
}
function closeCatalogLog() {
  catalogLog = null; catalogLogVersion++;
  el('catalog-log-panel').hidden = true; el('catalog-output').hidden = true; el('catalog-output').textContent = '';
}
el('catalog-log-close').addEventListener('click',closeCatalogLog);
function catalogButtons() {
  const disabled = labRole !== 'admin' || !catalogRelease?.approval || catalogBusy || catalogStarting;
  el('catalog-rollout').disabled = disabled;
  for (const retry of document.querySelectorAll('[data-catalog-retry]')) retry.disabled = disabled;
}
function catalogActivity(text, state = 'ready') {
  el('catalog-activity').textContent = text; el('catalog-activity').dataset.state = state;
}

function textButton(label, click) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'button log-button';
  button.textContent = label;
  if (['Fortsetzen', 'Anhalten', 'Ausstehende Rechner nachholen'].includes(label)) button.dataset.adminOnly = '';
  button.addEventListener('click', click);
  return button;
}

function startCatalogRelease(job, retry, targets) {
  const approval = catalogRelease?.approval;
  if (labRole !== 'admin' || !approval || catalogBusy || catalogStarting || el('catalog-job').value !== job || !targets.length) return;
  const revision = approval.revision;
  confirmCatalog(catalogEntries[job].label + ' · ' + targets.join(', '), async () => {
    if (el('catalog-job').value !== job || catalogRelease?.approval?.id !== approval.id || catalogRelease?.approval?.revision !== revision || catalogBusy) {
      el('catalog-status').textContent = 'Die Auswahl oder Freigabe hat sich geändert. Bitte den Status erneut prüfen.'; return;
    }
    catalogStarting = true; catalogButtons(); catalogActivity('Start wird angefordert','busy');
    let message = '';
    try {
      await request('/api/rollout/start', {job, run_id: approval.id, retry, confirmed: true});
      resetHistory('catalog-runs');
    } catch (error) {
      message = error.message;
    } finally {
      catalogStarting = false;
      if (el('catalog-job').value === job) await loadCatalogRelease(job);
      if (message && el('catalog-job').value === job) el('catalog-status').textContent = message;
    }
  },'Verteilung starten','Raumverteilung bestätigen');
}

async function loadCatalogRelease(job) {
  if (el('catalog-job').value !== job) return;
  cancelPoll('catalog');
  const version = ++catalogStateVersion;
  const button = el('catalog-rollout');
  button.disabled = true;
  catalogRelease = null; catalogBusy = false; catalogButtons();
  catalogActivity('Wird geladen','loading');
  const rollout = catalogEntries[job]?.kind === 'pilot' && job !== 'appearance';
  el('catalog-history').hidden = !rollout;
  button.hidden = !rollout; el('catalog-release-hint').hidden = !rollout;
  el('catalog-workflow-link').hidden = rollout;
  el('catalog-release-heading').textContent = rollout ? 'Freigabe für den Raum' : 'Hier geht es weiter';
  if (catalogEntries[job]?.kind !== 'pilot' || job === 'appearance') {
    const workflow = {
      appearance: ['Bildauswahl, Pilotabnahme und Verteilung stehen unter Desktopbilder.','/desktopbilder','Desktopbilder öffnen','Bilder'],
      bootstrap: ['Zugang und Geräteprüfung gehören zum Einrichten eines frisch installierten Clients.','/installationsfolge','Client einrichten','Einrichtung'],
      fresh: ['Die vollständige Baseline wird nach Geräteprüfung und Zugangseinrichtung installiert.','/installationsfolge','Neuinstallation öffnen','Einrichtung'],
      winbind: ['Der Winbind-Reconnect-Fix ist Bestandteil der vollständigen Baseline.','/installationsfolge','Zur Neuinstallation','In der Baseline'],
      maintenance: ['Gespeicherte Abendläufe und der gezielte Wartungsstart stehen unter Wartung.','/wartung','Wartung öffnen','Wartung'],
    }[job] || ['Diesen technischen Auftrag kannst du im Quelltext ansehen.','/installationsfolge','Zur Einrichtung','Werkzeug'];
    el('catalog-status').textContent = workflow[0];
    el('catalog-workflow-link').href = basePath + workflow[1]; el('catalog-workflow-link').textContent = workflow[2] + ' →';
    el('catalog-release-badge').className = 'badge neutral'; el('catalog-release-badge').textContent = workflow[3];
    catalogActivity('Katalog geladen');
    return;
  }
  try {
    const data = await requestHistory('/api/rollout/state/' + job, 'catalog-runs', () => loadCatalogRelease(job));
    if (!data || version !== catalogStateVersion || el('catalog-job').value !== job) return;
    catalogRelease = data;
    const {approval, targets, runs} = data;
    const busy = data.busy ?? runs.some(run => run.status === 'running'); catalogBusy = busy; catalogButtons();
    catalogActivity(busy ? 'Verteilung läuft' : 'Status geladen',busy ? 'busy' : 'ready');
    el('catalog-release-badge').className = 'badge ' + (busy ? 'warn' : approval ? 'good' : 'neutral');
    el('catalog-release-badge').textContent = busy ? 'Läuft' : approval ? 'Freigegeben' : 'Abnahme offen';
    el('catalog-status').textContent = busy ? 'Raumverteilung läuft …'
      : approval ? 'Aktueller Stand abgenommen auf ' + approval.host + '. Zur Verteilung auf ' + targets.length + (targets.length === 1 ? ' Schülerclient' : ' Schülerclients') + ' freigegeben.'
      : 'Zuerst den aktuellen Stand auf einem Pilotrechner testen und abnehmen.';
    el('catalog-empty').hidden = data.pagination ? data.pagination.total > 0 : runs.length > 0;
    renderHistory('catalog-runs', data, run => {
      const details = document.createElement('details');
      const heading = document.createElement('summary');
      const assigned = run.targets || targets;
      const title = document.createElement('strong'); title.textContent = catalogEntries[job].label;
      const count = document.createElement('span'); count.className = 'field-hint'; count.textContent = Object.keys(run.results || {}).length + ' / ' + assigned.length + ' Rechner';
      const time = document.createElement('time'); time.dateTime = run.started;
      time.textContent = new Date(run.started).toLocaleString('de-DE',{timeZone:'Europe/Berlin',dateStyle:'short',timeStyle:'short'});
      heading.append(title,badge(catalogLabels[run.status] || run.status,['failed','interrupted','running'].includes(run.status) ? 'warn' : 'neutral'),count,time);
      details.append(heading);
      if (run.error) { const error = document.createElement('p'); error.className = 'notice'; error.textContent = run.error; details.append(error); }
      if (approval && !busy && run.pending?.length && run.revision === approval.revision) {
        const retry = textButton('Ausstehende Rechner nachholen', () => startCatalogRelease(job, run.id, run.pending));
        retry.dataset.catalogRetry = ''; retry.disabled = labRole !== 'admin' || catalogStarting; details.append(retry);
      }
      const list = document.createElement('ul'); list.className = 'appearance-result-list';
      for (const host of assigned) {
        const item = document.createElement('li');
        const pending = run.status !== 'running' ? 'Kein Ergebnis' : run.active?.includes(host) ? 'In Arbeit' : 'Wartet';
        const name = document.createElement('strong'); name.textContent = host;
        const result = document.createElement('span'); result.textContent = run.results?.[host] || pending; item.append(name,result);
        if (run.results?.[host]) {
          item.append(textButton('Protokoll', () => {
            catalogLog = {run,host,job}; el('catalog-log-title').textContent = host + ' · ' + catalogEntries[job].label;
            showLog('/api/appearance/rollout/log/' + run.id + '/' + host,'catalog-output');
          }));
        }
        list.append(item);
      }
      details.append(list);
      return details;
    });
    if (catalogLog?.job === job) {
      const previous = catalogLog.run.status;
      catalogLog.run = runs.find(run => run.id === catalogLog.run.id) || catalogLog.run;
      if (previous === 'running' || catalogLog.run.status === 'running') await showLog('/api/appearance/rollout/log/' + catalogLog.run.id + '/' + catalogLog.host,'catalog-output',true);
    }
    if (busy && version === catalogStateVersion && el('catalog-job').value === job) schedulePoll('catalog', () => loadCatalogRelease(job), 2500);
  } catch (error) {
    if (version !== catalogStateVersion || el('catalog-job').value !== job) return;
    catalogRelease = null; catalogBusy = false; catalogButtons(); catalogActivity('Status nicht verfügbar','error');
    el('catalog-release-badge').textContent = 'Nicht verfügbar'; el('catalog-release-badge').className = 'badge warn';
    el('catalog-status').textContent = error.message;
  }
}

async function selectCatalog(reset = true) {
  const job = el('catalog-job').value;
  if (!catalogEntries[job]) return;
  if (reset) {
    closeCatalogLog(); el('catalog-code-details').open = false; catalogSourceJob = null; catalogSourceVersion++;
    el('catalog-code').textContent = ''; resetHistory('catalog-runs'); el('catalog-runs').replaceChildren(); el('catalog-empty').hidden = true;
    if (el('catalog-runs-pagination')) el('catalog-runs-pagination').hidden = true;
  }
  el('catalog-source').textContent = catalogEntries[job].source;
  el('catalog-description').textContent = pilotDescriptions[job] || {
    bootstrap:'Verwaltungszugang nach einer frischen Installation einrichten.',fresh:'Die vollständige LAB-Basis für ein neues IServ-Image.',
    winbind:'Verbindungsabbrüche bei der Domänenanmeldung beheben.',maintenance:'Regulär abmelden, Updates installieren und nach Erfolg neu starten.'
  }[job] || 'Hinterlegte Aufgaben und ihre Verwendung im LAB.';
  el('catalog-pilot-link').hidden = catalogEntries[job].kind !== 'pilot';
  el('catalog-pilot-link').href = basePath + '/pilotdeploys#' + encodeURIComponent(job);
  await loadCatalogRelease(job);
  if (el('catalog-job').value === job && el('catalog-code-details').open) await loadCatalogSource();
}
async function loadCatalogSource() {
  const job = el('catalog-job').value;
  const version = ++catalogSourceVersion;
  el('catalog-code').textContent = 'Quelltext wird geladen …';
  try {
    const data = await request('/api/playbooks/' + job);
    if (el('catalog-job').value !== job || version !== catalogSourceVersion) return;
    el('catalog-source').textContent = data.entry.source;
    el('catalog-code').textContent = data.content;
    catalogSourceJob = job;
  } catch (error) {
    if (el('catalog-job').value === job && version === catalogSourceVersion) el('catalog-code').textContent = error.message;
  }
}

async function loadCatalog() {
  el('catalog-refresh').disabled = true;
  try {
    catalogEntries = await request('/api/playbooks');
    const field = el('catalog-job');
    const previous = field.value;
    field.replaceChildren(...Object.entries(catalogEntries).map(([key,entry]) => new Option(entry.label,key))); field.disabled = false;
    const requested = window.location.hash.slice(1);
    field.value = catalogEntries[previous] ? previous : catalogEntries[requested] ? requested : catalogEntries.firefox ? 'firefox' : Object.keys(catalogEntries)[0];
    await selectCatalog(previous !== field.value);
  } catch (error) {
    catalogRelease = null; catalogButtons(); catalogActivity('Katalog nicht verfügbar','error');
    el('catalog-status').textContent = error.message;
  } finally { el('catalog-refresh').disabled = false; }
}

el('catalog-job').addEventListener('change',() => selectCatalog());
el('catalog-code-details').addEventListener('toggle',() => { if (el('catalog-code-details').open && catalogSourceJob !== el('catalog-job').value) loadCatalogSource(); });
el('catalog-refresh').addEventListener('click',loadCatalog);
el('catalog-rollout').addEventListener('click', () => {
  if (catalogRelease) startCatalogRelease(el('catalog-job').value, '', catalogRelease.targets);
});

let freshLog = null;
let freshBusy = true;
let freshHardware = [];
let freshGeneration = 0;
let freshLogVersion = 0;

function paintFreshLogSelection() {
  for (const button of document.querySelectorAll('.fresh-log-button')) {
    button.setAttribute('aria-expanded', String(button.dataset.runId === freshLog && !el('fresh-log-panel').hidden));
  }
}

async function showFreshLog(run) {
  const version = ++freshLogVersion;
  const output = el('fresh-output');
  if (el('fresh-log-panel').hidden || freshLog !== run.id) output.textContent = 'Protokoll wird geladen …';
  freshLog = run.id;
  el('fresh-log-title').textContent = run.host + ' · Installationsprotokoll';
  el('fresh-log-panel').hidden = false;
  output.hidden = false;
  paintFreshLogSelection();
  try {
    const data = await request('/api/fresh/log/' + run.id);
    if (freshLog !== run.id || version !== freshLogVersion) return;
    if (output.textContent !== data.output) output.textContent = data.output;
  } catch (error) {
    if (freshLog === run.id && version === freshLogVersion) output.textContent = error.message;
  }
}

el('fresh-log-close').addEventListener('click', () => {
  const previous = freshLog;
  freshLog = null;
  freshLogVersion++;
  el('fresh-log-panel').hidden = true;
  el('fresh-output').hidden = true;
  el('fresh-output').textContent = '';
  paintFreshLogSelection();
  [...document.querySelectorAll('.fresh-log-button')].find(button => button.dataset.runId === previous)?.focus({preventScroll: true});
});

function updateFreshButtons() {
  el('fresh-start').disabled = freshBusy || !el('fresh-targets').querySelector('input[data-host]:checked');
  el('bootstrap-start').disabled = freshBusy || !el('bootstrap-host').value;
  const hardware = freshHardware.find(item => item.host === el('bootstrap-host').value);
  el('bootstrap-adapters').textContent = hardware ? 'Schule ' + hardware.school + ' · Labor ' + hardware.lab : '';
}

function setHostOptions(select, hosts) {
  if ([...select.options].slice(1).map(option => option.value).join() === hosts.join()
      && select.options.length) return;
  const previous = select.value;
  select.replaceChildren(new Option('Client auswählen', ''), ...hosts.map(host => new Option(host, host)));
  select.value = hosts.includes(previous) ? previous : '';
}

function renderFreshTargets(data) {
  const list = el('fresh-targets');
  const revision = JSON.stringify([data.hosts, data.veyon, freshBusy]);
  if (list.dataset.revision === revision) return;
  list.dataset.revision = revision;
  const choices = new Map([...list.querySelectorAll('input[data-host]')].map(input =>
    [input.dataset.host, [input.checked, input.closest('div').querySelector('input[data-veyon]').checked]]));
  if (!data.hosts.length) {
    const empty = document.createElement('p'); empty.className = 'fresh-target-empty';
    const title = document.createElement('strong'); title.textContent = 'Noch kein Client vorbereitet';
    empty.append(title, 'Zuerst das Gerät prüfen und den Zugang einrichten.');
    list.replaceChildren(empty); return;
  }
  list.replaceChildren(...data.hosts.map(host => {
    const row = document.createElement('div');
    row.className = 'fresh-target';
    const [selected, veyon] = choices.get(host) || [false, false];
    const target = document.createElement('input');
    target.type = 'checkbox';
    target.dataset.host = host;
    target.checked = selected;
    target.disabled = freshBusy;
    const targetLabel = document.createElement('label');
    targetLabel.append(target, ' ' + host);
    const remote = document.createElement('input');
    remote.type = 'checkbox';
    remote.dataset.veyon = host;
    remote.disabled = freshBusy || !data.veyon;
    remote.checked = data.veyon && veyon;
    const remoteLabel = document.createElement('label');
    remoteLabel.className = 'veyon-choice';
    remote.setAttribute('aria-label', 'Veyon für ' + host);
    remoteLabel.append(remote, ' Veyon optional');
    row.append(targetLabel, remoteLabel);
    return row;
  }));
}

async function freshAction(path, payload, status) {
  resetHistory('fresh-runs');
  freshBusy = true;
  updateFreshButtons();
  try {
    const result = await request(path, {...payload, confirmed: true});
    freshLog = result.runs?.[0]?.id || result.id || freshLog;
    await loadFresh();
  } catch (error) {
    await loadFresh();
    el(status).textContent = error.message;
  }
}

function renderFreshRun(run) {
  const row = document.createElement('li');
  row.className = 'setup-run';
  row.dataset.state = run.status;
  const phase = run.kind === 'bootstrap' ? 'SSH-Erstaufnahme'
    : run.phase === 'cisco' ? 'Sammelkonto' : run.phase === 'maintenance' ? 'Abendwartung' : 'LAB-Basis';
  const heading = document.createElement('div'); heading.className = 'setup-run-heading';
  const host = document.createElement('h3'); host.textContent = run.host;
  const time = document.createElement('time'); time.dateTime = run.started;
  time.textContent = new Date(run.started).toLocaleString('de-DE', {timeZone: 'Europe/Berlin'});
  const tone = run.status === 'succeeded' ? 'good'
    : ['failed', 'interrupted'].includes(run.status) ? 'warn' : 'neutral';
  heading.append(host, badge(catalogLabels[run.status] || run.status, tone), time);
  const info = [phase];
  if (run.playbook_group) info.push(run.playbook_group.name);
  if (run.veyon) info.push('mit Veyon');
  if (run.resumed_from) info.push('Fortsetzung');
  if (run.acceptance) info.push(run.acceptance);
  if (run.stop_requested && ['running', 'queued'].includes(run.status)) info.push('Anhalten vorgemerkt');
  if (run.task) info.push(run.task);
  if (run.quiet) info.push('Seit mindestens 5 Minuten keine neue Protokollausgabe');
  const meta = document.createElement('p'); meta.className = 'setup-run-meta'; meta.textContent = info.join(' · ');
  const actions = document.createElement('div'); actions.className = 'setup-run-actions';
  row.append(heading, meta);
  if (run.status !== 'queued' && !(run.kind === 'bootstrap' && run.status === 'running')) {
    const log = textButton('Protokoll', () => showFreshLog(run));
    log.classList.add('fresh-log-button'); log.dataset.runId = run.id;
    log.setAttribute('aria-controls', 'fresh-log-panel');
    actions.append(log);
  }
  if (run.retryable && !freshBusy) {
    const step = run.phase === 'maintenance' ? 'Nur die Abendwartung erneut einrichten'
      : run.phase === 'cisco' ? 'Beim lokalen Sammelkonto fortsetzen'
      : 'LAB-Basis mit erneuter Vorprüfung wiederholen';
    actions.append(textButton('Fortsetzen', () => confirmCatalog(run.host + ' · ' + step, () =>
      freshAction('/api/fresh/retry', {run_id: run.id}, 'fresh-status'))));
  }
  if (!run.stop_requested && run.phase && (run.status === 'queued'
      || (run.status === 'running' && run.phase === 'basis'))) {
    const when = run.status === 'queued' ? 'Vor Beginn anhalten'
      : 'Nach Abschluss der Basis anhalten. Laufende Arbeiten dürfen fertig werden';
    actions.append(textButton('Anhalten', () => confirmCatalog(run.host + ' · ' + when, () =>
      freshAction('/api/fresh/stop', {run_id: run.id}, 'fresh-status'), 'Anhalten')));
  }
  if (actions.childElementCount) row.append(actions);
  return row;
}

let freshTrackedRuns = [];
async function freshHistoryPage(number) {
  const page = await request('/api/fresh/page/' + number);
  const rows = [...page.active_runs, ...page.runs];
  const previous = freshTrackedRuns.find(run => run.id === freshLog);
  if (previous && !rows.some(run => run.id === freshLog)) {
    // Only an open log needs its final metadata when its row is on another page.
    rows.push(['running', 'queued'].includes(previous.status)
      ? await request('/api/fresh/run/' + freshLog) : previous);
  }
  freshTrackedRuns = rows;
  return page;
}

function renderFreshActive(runs) {
  let panel = el('fresh-active-panel');
  if (!panel) {
    panel = document.createElement('article'); panel.id = 'fresh-active-panel'; panel.className = 'panel setup-history';
    panel.setAttribute('aria-labelledby','fresh-active-title');
    const heading = document.createElement('h2'); heading.id = 'fresh-active-title'; heading.textContent = 'Laufende Installationen';
    const description = document.createElement('p'); description.className = 'field-hint';
    description.textContent = 'Aktive und wartende Aufträge bleiben hier sichtbar. Abgeschlossene Läufe stehen im Verlauf.';
    const list = document.createElement('ul'); list.id = 'fresh-active-runs'; list.className = 'setup-runs';
    panel.append(heading,description,list);
    el('intake-gate').insertAdjacentElement('afterend',panel);
  }
  panel.hidden = !runs.length;
  el('fresh-active-runs').replaceChildren(...runs.map(renderFreshRun));
}

async function loadFresh() {
  cancelPoll('fresh');
  const generation = ++freshGeneration;
  try {
    const data = await requestHistory('/api/fresh', 'fresh-runs', loadFresh, freshHistoryPage);
    if (!data) return;
    if (generation !== freshGeneration) return;
    freshBusy = data.busy || data.runs.some(run => ['queued', 'running'].includes(run.status));
    const selectedGroup = el('fresh-group').value;
    el('fresh-group').replaceChildren(new Option('Baseline · vollständige Grundkonfiguration', ''),
      ...(data.playbook_groups || []).map(group => new Option(group.name + ' · mit Baseline', group.id)));
    el('fresh-group').value = (data.playbook_groups || []).some(group => group.id === selectedGroup) ? selectedGroup : '';
    el('fresh-group').disabled = freshBusy;
    freshHardware = data.hardware;
    el('fresh-parallelism').textContent = data.parallelism ? 'Maximal ' + data.parallelism + ' gleichzeitig · weitere in Warteschlange' : '';
    setHostOptions(el('bootstrap-host'), freshHardware.map(item => item.host));
    renderFreshTargets(data);
    updateFreshButtons();
    el('bootstrap-status').textContent = freshHardware.length ? '' : 'Zuerst oben das frisch installierte Gerät prüfen.';
    const waiting = data.active_counts?.queued ?? data.runs.filter(run => run.status === 'queued').length;
    const running = data.active_counts?.running ?? data.runs.filter(run => run.status === 'running').length;
    el('fresh-status').textContent = freshBusy ? (running || waiting
      ? running + ' aktiv · ' + waiting + ' in Warteschlange'
      : 'Ein anderer Verwaltungsauftrag läuft.')
      : data.hosts.length ? 'Vorbereitete Clients auswählen.'
        : 'Derzeit ist kein frisch aufgenommenes Gerät für die Basis freigegeben.';
    if (data.hosts.length && !data.veyon) el('fresh-status').append(' Öffentlicher Veyon-Lehrerschlüssel fehlt.');
    renderFreshActive(data.active_runs);
    renderHistory('fresh-runs', data, renderFreshRun);
    el('fresh-history-title').textContent = 'Abgeschlossene Installationen';
    el('fresh-empty').textContent = 'Noch keine abgeschlossene Installation.';
    el('fresh-empty').hidden = data.pagination.total > 0;
    document.dispatchEvent(new CustomEvent('fresh-state', {detail: data}));
    const logged = [...data.active_runs, ...data.runs, ...freshTrackedRuns].find(run => run.id === freshLog);
    if (logged && logged.status !== 'queued' && !(logged.kind === 'bootstrap' && logged.status === 'running')) {
      showFreshLog(logged);
    }
    paintFreshLogSelection();
    schedulePoll('fresh', loadFresh, freshBusy ? 3000 : 15000);
  } catch (error) {
    if (generation !== freshGeneration) return;
    el('fresh-status').textContent = error.message;
    schedulePoll('fresh', loadFresh, 5000, true);
  }
}

el('fresh-targets').addEventListener('change', updateFreshButtons);
el('bootstrap-host').addEventListener('change', updateFreshButtons);
el('fresh-form').addEventListener('submit', event => {
  event.preventDefault();
  const targets = [...el('fresh-targets').querySelectorAll('input[data-host]:checked')].map(input =>
    ({host: input.dataset.host, veyon: input.closest('div').querySelector('input[data-veyon]').checked}));
  if (!targets.length) return;
  const names = targets.map(({host, veyon}) => host + (veyon ? ' mit Veyon' : '')).join(', ');
  const group_id = el('fresh-group').value;
  const selection = el('fresh-group').selectedOptions[0].textContent;
  confirmCatalog(selection + ' auf den vorbereiteten Clients ' + names, () =>
    freshAction('/api/fresh/start', group_id ? {targets, group_id} : {targets}, 'fresh-status'));
});

el('bootstrap-form').addEventListener('submit', event => {
  event.preventDefault();
  const host = el('bootstrap-host').value;
  const fingerprint = el('bootstrap-fingerprint').value.trim();
  if (!host) return;
  confirmCatalog(host + ' · Fingerabdruck an der Konsole unabhängig bestätigt?', async () => {
    const ssh_password = el('bootstrap-ssh').value;
    const sudo_password = el('bootstrap-sudo').value;
    el('bootstrap-ssh').value = '';
    el('bootstrap-sudo').value = '';
    await freshAction('/api/fresh/bootstrap', {host, fingerprint, ssh_password, sudo_password}, 'bootstrap-status');
  });
});

let maintenanceProbeVersion = 0;
let maintenanceBusy = true;
let maintenanceStarting = false;
const maintenanceSelection = new Set();
let maintenanceHosts = [];
let maintenanceParallelism = 16;
function updateMaintenanceControls() {
  const disabled = labRole !== 'admin' || maintenanceBusy || maintenanceStarting;
  el('maintenance-start').disabled = disabled || !maintenanceSelection.size;
  el('maintenance-refresh').disabled = disabled || !maintenanceSelection.size;
  el('maintenance-all').disabled = el('maintenance-none').disabled = disabled || !maintenanceHosts.length;
  for (const check of el('maintenance-targets').querySelectorAll('input')) check.disabled = disabled;
  el('maintenance-selection').textContent = maintenanceSelection.size + ' von ' + maintenanceHosts.length
    + ' Schülerclients ausgewählt · bis zu ' + maintenanceParallelism + ' gleichzeitig';
}
function resetMaintenanceStatus() {
  maintenanceProbeVersion++;
  el('maintenance-details').hidden = true;
  el('maintenance-details').open = false;
  el('maintenance-output').textContent = '';
  el('maintenance-status').textContent = maintenanceSelection.size
    ? 'Manueller Start jederzeit möglich. „Status prüfen“ liest nur den aktuellen Zustand.'
    : maintenanceHosts.length ? 'Bitte Schülerclients auswählen.' : 'Noch kein Client für die zentrale Wartung registriert.';
  updateMaintenanceControls();
}

async function loadMaintenance() {
  const version = ++maintenanceProbeVersion;
  const hosts = [...maintenanceSelection].sort();
  if (!hosts.length || labRole !== 'admin' || maintenanceBusy || maintenanceStarting) return;
  el('maintenance-refresh').disabled = true;
  el('maintenance-status').textContent = 'Live-Status für ' + hosts.length + ' Schülerclients wird geprüft …';
  try {
    // Use the same client concurrency as the controller.
    const results = new Map();
    let next = 0;
    await Promise.all(Array.from({length: Math.min(maintenanceParallelism, hosts.length)}, async () => {
      while (next < hosts.length && version === maintenanceProbeVersion) {
        const host = hosts[next++];
        try { results.set(host, await request('/api/maintenance/' + host)); }
        catch (error) { results.set(host, {available:false, output:error.message}); }
      }
    }));
    if (version !== maintenanceProbeVersion) return;
    const labels = {inactive:'Bereit', active:'Wartung läuft', activating:'Wartung startet',
      deactivating:'Wartung wird beendet', failed:'Letzte Wartung fehlgeschlagen'};
    const lines = hosts.map(host => {
      const data = results.get(host);
      return host + ' · ' + (data.available ? labels[data.active] || 'Dienststatus unbekannt'
        : 'Nicht erreichbar; beim Start wird Aufwecken versucht.');
    });
    el('maintenance-status').textContent = lines.join('\n');
    el('maintenance-output').textContent = hosts.map(host => host + '\n' + (results.get(host).output || 'Kein Protokoll.')).join('\n\n');
    el('maintenance-details').hidden = false;
  } catch (error) {
    if (version === maintenanceProbeVersion) {
      el('maintenance-status').textContent = error.message;
      el('maintenance-details').hidden = true;
    }
  } finally {
    if (version === maintenanceProbeVersion) updateMaintenanceControls();
  }
}

const maintenanceLabels = {running: 'Läuft', finished: 'Abgeschlossen', failed: 'Fehlgeschlagen',
  succeeded: 'Erfolgreich', skipped: 'Übersprungen', deferred: 'Zurückgestellt', interrupted: 'Unterbrochen',
  disabled: 'Noch gesperrt', blocked: 'Wartet auf Schülerabschluss'};
function maintenanceRunHeading(run) {
  const title = document.createElement('summary');
  const time = document.createElement('time'); time.dateTime = run.started;
  time.textContent = new Date(run.started).toLocaleString('de-DE', {timeZone:'Europe/Berlin', dateStyle:'medium', timeStyle:'short'});
  const tone = ['succeeded', 'finished'].includes(run.status) ? 'good'
    : ['failed', 'interrupted', 'deferred'].includes(run.status) ? 'warn' : 'neutral';
  title.append(time, badge(maintenanceLabels[run.status] || run.status, tone));
  if (run.manual_start) title.append(badge('Manuell', 'neutral'));
  else if (run.manual_pilot) title.append(badge('Pilotversuch', 'neutral'));
  if (run.targets?.length) title.append(badge(run.targets.length + (run.targets.length === 1 ? ' Gerät' : ' Geräte'), 'neutral'));
  return title;
}

function maintenancePackageInfo(result) {
  const report = result.packages;
  const note = document.createElement('small');
  if (report?.status !== 'recorded') {
    note.textContent = 'Update-Ergebnis nicht erfasst.';
    note.title = report?.reason || 'Für diesen Lauf wurden Paketänderungen noch nicht gespeichert.';
    return note;
  }
  const groups = [['upgraded', 'Aktualisiert'], ['installed', 'Neu installiert'], ['removed', 'Entfernt']];
  const count = key => Array.isArray(report[key]) ? report[key].length : 0;
  const label = [count('upgraded') ? count('upgraded')+(count('upgraded')===1?' Update installiert':' Updates installiert') : 'Keine Updates installiert',
    count('installed') ? count('installed')+(count('installed')===1?' Paket neu installiert':' Pakete neu installiert') : '',
    count('removed') ? count('removed')+(count('removed')===1?' Paket entfernt':' Pakete entfernt') : ''].filter(Boolean).join(' · ');
  if (!groups.some(([key]) => count(key))) { note.textContent=label+'.'; return note; }
  const details=document.createElement('details');details.className='maintenance-packages';
  const summary=document.createElement('summary');summary.textContent=label;
  const list=document.createElement('dl');
  for (const [key,title] of groups) {
    if (!count(key)) continue;
    const term=document.createElement('dt');term.textContent=title;
    const values=document.createElement('dd');
    values.textContent=report[key].map(row=>row.name+(row.versions?' ('+row.versions+')':'')).join(', ');
    list.append(term,values);
  }
  details.append(summary,list);return details;
}

function maintenanceResultRows(run) {
  const results = run.results || {};
  const hosts = [...new Set([...(run.targets || []), ...Object.keys(results)])].sort();
  const list = document.createElement('ul'); list.className = 'maintenance-result-list';
  for (const host of hosts) {
    const result = results[host];
    const row = document.createElement('li');
    const name = document.createElement('strong'); name.textContent = host;
    const status = document.createElement('span');
    status.textContent = result ? result.status === 'succeeded' ? 'Neustart bestätigt'
      : maintenanceLabels[result.status] || result.status
      : run.status === 'running' ? 'Endergebnis steht noch aus' : 'Kein Ergebnis gespeichert';
    status.className = 'badge ' + (result?.status === 'succeeded' ? 'good' : result?.status === 'failed' ? 'warn' : 'neutral');
    row.append(name, status);
    if (result) row.append(maintenancePackageInfo(result));
    if (result?.reason || result?.woke || result?.poweroff) {
      const detail = document.createElement('small');
      detail.textContent = [result.reason, result.woke ? 'Per WOL gestartet' : '', result.poweroff].filter(Boolean).join(' · ');
      row.append(detail);
    }
    list.append(row);
  }
  return list;
}

function renderCurrentMaintenance(run) {
  el('maintenance-current').hidden = !run || run.status !== 'running';
  if (!run || run.status !== 'running') return;
  el('maintenance-current-title').textContent = 'Aktueller Wartungslauf';
  el('maintenance-current-state').textContent = maintenanceLabels[run.status] || run.status;
  el('maintenance-current-state').className = 'badge ' + (run.status === 'finished' ? 'good' : run.status === 'failed' ? 'warn' : 'neutral');
  const count = (run.targets || Object.keys(run.results || {})).length;
  const done = Object.keys(run.results || {}).length;
  el('maintenance-current-summary').textContent = (run.manual_start ? 'Manuell' : run.manual_pilot ? 'Pilotversuch' : 'Automatisch')
    + ' · ' + new Date(run.started).toLocaleString('de-DE', {timeZone:'Europe/Berlin'})
    + ' · ' + done + ' von ' + count + ' Ergebnissen'
    + (run.status === 'running' ? ' · Anzeige aktualisiert sich alle 10 Sekunden.' : '');
  el('maintenance-current-results').replaceChildren(maintenanceResultRows(run));
}

async function loadTeacherRuns() {
  try {
    const data = await requestHistory('/api/teacher/runs', 'teacher-runs', loadTeacherRuns);
    if (!data) return;
    el('teacher-empty').hidden = data.pagination.total > 0;
    renderHistory('teacher-runs', data, run => {
      const details = document.createElement('details');
      const body = document.createElement('p'); body.textContent = run.reason || 'Teacher-Wartungslauf';
      details.append(maintenanceRunHeading(run), body); return details;
    });
  } catch (error) { el('teacher-last').textContent = error.message; }
}
async function loadMaintenanceHosts(refreshLive = false) {
  cancelPoll('maintenance');
  try {
    const [data, central] = await Promise.all([
      request('/api/maintenance-hosts'), requestHistory('/api/maintenance-runs', 'maintenance-runs', () => loadMaintenanceHosts(false))
    ]);
    if (!central) return;
    maintenanceParallelism = data.parallelism || 16;
    // Keep the current run visible even while browsing older history pages.
    const recent = central.pagination.page === 1 ? central : await request('/api/maintenance-runs');
    renderCurrentMaintenance(recent.runs[0]);
    el('maintenance-count').textContent = central.enrolled.length;
    el('maintenance-enrolled').replaceChildren(...central.enrolled.map(host => badge(host, 'neutral')));
    if (!central.enrolled.length) el('maintenance-enrolled').textContent = 'Noch keine Clients registriert.';
    el('maintenance-cycle').textContent = central.busy ? 'Auftrag läuft' : 'Kein Lauf aktiv';
    el('maintenance-cycle').dataset.state = central.busy ? 'busy' : 'ready';
    maintenanceBusy = central.busy;
    el('maintenance-empty').hidden = central.pagination.total > 0;
    el('teacher-panel').hidden = !central.teacher;
    el('maintenance-teacher').textContent = central.teacher ? (central.teacher.configuration.enabled ? 'Freigegeben' : 'Gesperrt') : '—';
    if (central.teacher) {
      el('teacher-status').textContent = central.teacher.configuration.enabled
        ? 'Automatik freigegeben' : 'Automatik gesperrt';
      el('teacher-status').className = 'badge ' + (central.teacher.configuration.enabled ? 'good' : 'warn');
      const latest = central.teacher.runs[0];
      el('teacher-last').textContent = latest
        ? 'Letzter Lauf: ' + new Date(latest.started).toLocaleString('de-DE', {timeZone:'Europe/Berlin'}) + ' · '
          + (maintenanceLabels[latest.status] || latest.status)
        : '';
    }
    renderHistory('maintenance-runs', central, run => {
      const section = document.createElement('details');
      section.open = run.status === 'running';
      const list = maintenanceResultRows(run);
      if (run.teacher) {
        const row = document.createElement('li');
        row.textContent = 'LAB-Teacher · ' + (maintenanceLabels[run.teacher.status] || run.teacher.status)
          + (run.teacher.reason ? ' · ' + run.teacher.reason : '');
        list.append(row);
      }
      section.append(maintenanceRunHeading(run), list);
      return section;
    });
    const changed = JSON.stringify(maintenanceHosts) !== JSON.stringify(data.hosts);
    maintenanceHosts = data.hosts;
    for (const host of maintenanceSelection) if (!data.hosts.includes(host)) maintenanceSelection.delete(host);
    if (changed) {
      el('maintenance-targets').replaceChildren(...data.hosts.map(host => {
        const label = document.createElement('label');
        const check = document.createElement('input'); check.type = 'checkbox'; check.value = host;
        check.checked = maintenanceSelection.has(host); check.setAttribute('aria-label', host);
        check.addEventListener('change', () => {
          check.checked ? maintenanceSelection.add(host) : maintenanceSelection.delete(host);
          resetMaintenanceStatus();
        });
        label.append(check, host.replace('LAB-', '')); return label;
      }));
      resetMaintenanceStatus();
    }
    updateMaintenanceControls();
    if (central.teacher) await loadTeacherRuns();
    if (refreshLive) await loadMaintenance();
    if (central.busy) schedulePoll('maintenance', loadMaintenanceHosts, 10000);
  } catch (error) {
    maintenanceBusy = true;
    el('maintenance-status').textContent = error.message;
    updateMaintenanceControls();
    el('maintenance-cycle').textContent = 'Status nicht verfügbar';
    el('maintenance-cycle').dataset.state = 'error';
  }
}

el('maintenance-refresh').addEventListener('click', loadMaintenance);
for (const [id, all] of [['maintenance-all',true],['maintenance-none',false]])
  el(id).addEventListener('click', () => {
    if (labRole !== 'admin' || maintenanceBusy || maintenanceStarting) return;
    maintenanceSelection.clear();
    for (const check of el('maintenance-targets').querySelectorAll('input')) {
      check.checked = all; if (all) maintenanceSelection.add(check.value);
    }
    resetMaintenanceStatus();
  });
el('maintenance-start').addEventListener('click', () => {
  const hosts = [...maintenanceSelection].sort();
  if (!hosts.length || labRole !== 'admin' || maintenanceBusy || maintenanceStarting) return;
  const action = 'Nutzer abmelden, Updates installieren und neu starten';
  confirmCatalog(hosts.join(', ') + ' · ' + action, async () => {
    maintenanceStarting = true;
    updateMaintenanceControls();
    try {
      await request('/api/maintenance/start', {hosts, confirmed: true});
      el('maintenance-status').textContent = 'Wartung gestartet: ' + hosts.join(', ') + '. Ergebnisse im Verlauf.';
      await loadMaintenanceHosts(false);
    } catch (error) {
      el('maintenance-status').textContent = error.message;
    } finally {
      maintenanceStarting = false;
      updateMaintenanceControls();
    }
  });
});

if (currentPage === '/playbooks') loadCatalog();
if (currentPage === '/installationsfolge') loadFresh();
if (currentPage === '/wartung') loadMaintenanceHosts();
