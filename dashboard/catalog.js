'use strict';

let confirmedAction = null;
const catalogDialog = el('catalog-confirm');

function confirmCatalog(text, action, label = 'Jetzt starten') {
  el('catalog-confirm-text').textContent = text;
  catalogDialog.querySelector('button[value="start"]').textContent = label;
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
let catalogTimer = null;
const catalogLabels = {
  running: 'Läuft', finished: 'Beendet', failed: 'Fehlgeschlagen',
  interrupted: 'Unterbrochen', succeeded: 'Erfolgreich', queued: 'Wartet', stopped: 'Angehalten',
};

async function showLog(endpoint, target) {
  const output = el(target);
  try {
    const data = await request(endpoint);
    output.textContent = data.output;
  } catch (error) {
    output.textContent = error.message;
  }
  output.hidden = false;
}

function textButton(label, click) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'button log-button';
  button.textContent = label;
  button.addEventListener('click', click);
  return button;
}

function startCatalogRelease(job, retry, targets) {
  const approval = catalogRelease?.approval;
  if (!approval) return;
  confirmCatalog(catalogEntries[job].label + ' · ' + targets.join(', '), async () => {
    el('catalog-rollout').disabled = true;
    try {
      await request('/api/rollout/start', {job, run_id: approval.id, retry, confirmed: true});
      await loadCatalogRelease(job);
    } catch (error) {
      el('catalog-status').textContent = error.message;
      el('catalog-rollout').disabled = false;
    }
  });
}

async function loadCatalogRelease(job) {
  clearTimeout(catalogTimer);
  const button = el('catalog-rollout');
  button.disabled = true;
  catalogRelease = null;
  if (catalogEntries[job]?.kind !== 'pilot' || job === 'appearance') {
    el('catalog-runs').replaceChildren();
    el('catalog-status').textContent = {
      appearance: 'Die Freigabe für Bilder findest du unter Desktopbilder.',
      bootstrap: 'SSH-Erstaufnahme direkt auf LAB-Teacher mit lokalen Passwortabfragen.',
      fresh: 'Die Warteschlange für vorbereitete Clients steht unter Neuinstallation.',
      maintenance: 'Status und Start stehen unter Wartung.',
    }[job] || '';
    return;
  }
  try {
    const data = await request('/api/rollout/state/' + job);
    if (el('catalog-job').value !== job) return;
    catalogRelease = data;
    const {approval, targets, runs} = data;
    const busy = runs.some(run => run.status === 'running');
    button.disabled = !approval || busy;
    el('catalog-status').textContent = busy ? 'Raumverteilung läuft …'
      : approval ? 'Abgenommen auf ' + approval.host + ' · Stand ' + approval.revision.slice(0, 12)
      : 'Für den aktuellen Stand fehlt ein abgenommener Pilotlauf.';
    el('catalog-runs').replaceChildren(...runs.map((run, index) => {
      const details = document.createElement('details');
      details.open = index === 0;
      const heading = document.createElement('summary');
      const assigned = run.targets || targets;
      heading.textContent = [new Date(run.started).toLocaleString('de-DE'), catalogLabels[run.status],
        Object.keys(run.results).length + ' von ' + assigned.length, run.error].filter(Boolean).join(' · ');
      details.append(heading);
      if (approval && !busy && run.pending.length && run.revision === approval.revision) {
        details.append(textButton('Ausstehende Rechner nachholen', () => startCatalogRelease(job, run.id, run.pending)));
      }
      const list = document.createElement('ul');
      for (const host of assigned) {
        const item = document.createElement('li');
        const pending = run.status !== 'running' ? 'Kein Ergebnis' : run.active?.includes(host) ? 'In Arbeit' : 'Wartet';
        item.textContent = host + ' · ' + (run.results[host] || pending);
        if (run.results[host]) {
          item.append(' ', textButton('Protokoll', () => showLog('/api/appearance/rollout/log/' + run.id + '/' + host, 'catalog-output')));
        }
        list.append(item);
      }
      details.append(list);
      return details;
    }));
    if (busy) catalogTimer = setTimeout(() => loadCatalogRelease(job), 2500);
  } catch (error) {
    el('catalog-status').textContent = error.message;
  }
}

async function selectCatalog() {
  const job = el('catalog-job').value;
  el('catalog-output').hidden = true;
  el('catalog-code-details').open = false;
  await loadCatalogRelease(job);
  try {
    const data = await request('/api/playbooks/' + job);
    if (el('catalog-job').value !== job) return;
    el('catalog-source').textContent = data.entry.source;
    el('catalog-code').textContent = data.content;
  } catch (error) {
    el('catalog-status').textContent = error.message;
  }
}

async function loadCatalog() {
  try {
    catalogEntries = await request('/api/playbooks');
    const field = el('catalog-job');
    for (const [key, entry] of Object.entries(catalogEntries)) field.add(new Option(entry.label, key));
    if (catalogEntries.firefox) field.value = 'firefox';
    await selectCatalog();
  } catch (error) {
    el('catalog-status').textContent = error.message;
  }
}

el('catalog-job').addEventListener('change', selectCatalog);
el('catalog-rollout').addEventListener('click', () => {
  if (catalogRelease) startCatalogRelease(el('catalog-job').value, '', catalogRelease.targets);
});

let freshTimer = null;
let freshLog = null;
let freshBusy = true;
let freshHardware = [];
let freshGeneration = 0;

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
  list.replaceChildren(...data.hosts.map(host => {
    const row = document.createElement('div');
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
    remoteLabel.append(remote, ' Veyon für ' + host);
    row.append(targetLabel, remoteLabel);
    return row;
  }));
}

async function freshAction(path, payload, status) {
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
  const phase = run.kind === 'bootstrap' ? 'SSH-Erstaufnahme'
    : run.phase === 'cisco' ? 'Sammelkonto' : run.phase === 'maintenance' ? 'Abendwartung' : 'LAB-Basis';
  row.textContent = [new Date(run.started).toLocaleString('de-DE', {timeZone: 'Europe/Berlin'}), run.host, phase,
    catalogLabels[run.status] || run.status].join(' · ');
  if (run.veyon) row.append(' · mit Veyon');
  if (run.resumed_from) row.append(' · Fortsetzung');
  if (run.acceptance) row.append(' · ' + run.acceptance);
  if (run.stop_requested && ['running', 'queued'].includes(run.status)) row.append(' · Anhalten vorgemerkt');
  if (run.task) row.append(' · ' + run.task);
  if (run.quiet) row.append(' · Seit mindestens 5 Minuten keine neue Protokollausgabe');
  if (run.status !== 'queued' && !(run.kind === 'bootstrap' && run.status === 'running')) {
    row.append(' ', textButton('Protokoll', () => {
      freshLog = run.id;
      showLog('/api/fresh/log/' + run.id, 'fresh-output');
    }));
  }
  if (run.retryable && !freshBusy) {
    const step = run.phase === 'maintenance' ? 'Nur die Abendwartung erneut einrichten'
      : run.phase === 'cisco' ? 'Beim lokalen Sammelkonto fortsetzen'
      : 'LAB-Basis mit erneuter Vorprüfung wiederholen';
    row.append(' ', textButton('Fortsetzen', () => confirmCatalog(run.host + ' · ' + step, () =>
      freshAction('/api/fresh/retry', {run_id: run.id}, 'fresh-status'))));
  }
  if (!run.stop_requested && run.phase && (run.status === 'queued'
      || (run.status === 'running' && run.phase === 'basis'))) {
    const when = run.status === 'queued' ? 'Vor Beginn anhalten'
      : 'Nach Abschluss der Basis anhalten. Laufende Arbeiten dürfen fertig werden';
    row.append(' ', textButton('Anhalten', () => confirmCatalog(run.host + ' · ' + when, () =>
      freshAction('/api/fresh/stop', {run_id: run.id}, 'fresh-status'), 'Anhalten')));
  }
  return row;
}

async function loadFresh() {
  clearTimeout(freshTimer);
  const generation = ++freshGeneration;
  try {
    const data = await request('/api/fresh');
    if (generation !== freshGeneration) return;
    freshBusy = data.busy || data.runs.some(run => ['queued', 'running'].includes(run.status));
    freshHardware = data.hardware;
    setHostOptions(el('bootstrap-host'), freshHardware.map(item => item.host));
    renderFreshTargets(data);
    updateFreshButtons();
    el('bootstrap-status').textContent = freshHardware.length ? ''
      : 'Für die Erstaufnahme auf LAB-Teacher eine geprüfte Datei unter local-private/fresh-hardware/LAB-XX.json ablegen.';
    const waiting = data.runs.filter(run => run.status === 'queued').length;
    const running = data.runs.filter(run => run.status === 'running').length;
    el('fresh-status').textContent = freshBusy ? (running || waiting
      ? running + ' aktiv · ' + waiting + ' in Warteschlange'
      : 'Ein anderer Verwaltungsauftrag läuft.')
      : data.hosts.length ? 'Vorbereitete Clients auswählen.'
        : 'Derzeit ist kein frisch aufgenommenes Gerät für die Basis freigegeben.';
    if (data.hosts.length && !data.veyon) el('fresh-status').append(' Öffentlicher Veyon-Lehrerschlüssel fehlt.');
    el('fresh-runs').replaceChildren(...data.runs.map(renderFreshRun));
    const logged = data.runs.find(run => run.id === freshLog);
    if (logged && logged.status !== 'queued' && !(logged.kind === 'bootstrap' && logged.status === 'running')) {
      showLog('/api/fresh/log/' + freshLog, 'fresh-output');
    }
    freshTimer = setTimeout(loadFresh, freshBusy ? 3000 : 15000);
  } catch (error) {
    if (generation !== freshGeneration) return;
    el('fresh-status').textContent = error.message;
    freshTimer = setTimeout(loadFresh, 5000);
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
  confirmCatalog('Basisinstallation auf den vorbereiteten Clients ' + names, () =>
    freshAction('/api/fresh/start', {targets}, 'fresh-status'));
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

async function loadMaintenance() {
  el('maintenance-start').disabled = true;
  const host = el('maintenance-host').value;
  el('maintenance-policy').textContent = host !== 'LAB-08B'
    ? 'Nutzer regulär abmelden, Updates installieren und nach Erfolg neu starten. Mo–Fr ab 18 Uhr.'
    : 'Bisheriger Pilot mit Download bei belegter Sitzung und Neustart bei Updatebedarf.';
  try {
    const data = await request('/api/maintenance/' + host);
    if (el('maintenance-host').value !== host) return;
    el('maintenance-start').disabled = !data.can_start;
    el('maintenance-status').textContent = data.available ? host + ' · ' + (data.active || 'unbekannt') : 'Live-Status nicht verfügbar; der Client kann ausgeschaltet sein. Gespeicherte Ergebnisse stehen unten.';
    el('maintenance-output').textContent = data.output;
  } catch (error) {
    el('maintenance-status').textContent = error.message;
  }
}

async function loadMaintenanceHosts() {
  try {
    const [data, central] = await Promise.all([
      request('/api/maintenance-hosts'), request('/api/maintenance-runs')
    ]);
    el('maintenance-enrolled').textContent = 'Registriert: ' + (central.enrolled.join(', ') || 'noch keine Clients');
    const labels = {running: 'Läuft', finished: 'Abgeschlossen', failed: 'Fehlgeschlagen',
      succeeded: 'Erfolgreich', skipped: 'Übersprungen', deferred: 'Zurückgestellt', interrupted: 'Unterbrochen'};
    el('maintenance-runs').replaceChildren(...central.runs.map((run, index) => {
      const section = document.createElement('details');
      section.open = index === 0;
      const heading = document.createElement('summary');
      heading.textContent = new Date(run.started).toLocaleString('de-DE') + ' · ' + (labels[run.status] || run.status) + (run.manual_pilot ? ' · Pilotversuch' : '');
      const list = document.createElement('ul');
      for (const [host, result] of Object.entries(run.results)) {
        const row = document.createElement('li');
        row.textContent = host + ' · ' + (labels[result.status] || result.status) + (result.reason ? ' · ' + result.reason : '')
          + (result.woke ? ' · per WOL gestartet' : '') + (result.poweroff ? ' · ' + result.poweroff : '');
        list.append(row);
      }
      section.append(heading, list);
      return section;
    }));
    const select = el('maintenance-host');
    const previous = select.value;
    select.replaceChildren(...data.hosts.map(host => new Option(host, host)));
    select.value = data.hosts.includes(previous) ? previous : data.hosts[0];
    await loadMaintenance();
  } catch (error) {
    el('maintenance-status').textContent = error.message;
  }
}

el('maintenance-refresh').addEventListener('click', loadMaintenanceHosts);
el('maintenance-host').addEventListener('change', loadMaintenance);
el('maintenance-start').addEventListener('click', () => {
  const host = el('maintenance-host').value;
  const action = host !== 'LAB-08B' ? 'Nutzer abmelden, Updates installieren und neu starten' : 'Wartungsdienst starten';
  confirmCatalog(host + ' · ' + action, async () => {
    el('maintenance-start').disabled = true;
    try {
      await request('/api/maintenance/start', {host, confirmed: true});
      await loadMaintenance();
    } catch (error) {
      el('maintenance-status').textContent = error.message;
    }
  });
});

if (currentPage === '/playbooks') loadCatalog();
if (currentPage === '/installationsfolge') loadFresh();
if (currentPage === '/wartung') loadMaintenanceHosts();
