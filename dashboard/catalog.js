'use strict';

let confirmedAction = null;
const catalogDialog = el('catalog-confirm');

function confirmCatalog(text, action) {
  el('catalog-confirm-text').textContent = text;
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
  interrupted: 'Unterbrochen', succeeded: 'Erfolgreich',
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
      fresh: 'Der Start für einen freigegebenen Einzelclient steht unter Neuinstallation.',
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
async function loadFresh() {
  clearTimeout(freshTimer);
  try {
    const data = await request('/api/fresh');
    const select = el('fresh-host');
    const previous = select.value;
    select.replaceChildren(new Option('Client auswählen', ''), ...data.hosts.map(host => new Option(host, host)));
    select.value = data.hosts.includes(previous) ? previous : '';
    const busy = data.runs.some(run => run.status === 'running');
    el('fresh-start').disabled = busy || !data.hosts.length;
    el('fresh-status').textContent = busy ? 'Basisinstallation läuft …' : data.hosts.length
      ? 'Einzelclient nach der Erstaufnahme auswählen.'
      : 'Derzeit ist kein frisch aufgenommenes Gerät freigegeben.';
    el('fresh-runs').replaceChildren(...data.runs.map(run => {
      const row = document.createElement('li');
      row.textContent = [new Date(run.started).toLocaleString('de-DE'), run.host, catalogLabels[run.status]].join(' · ');
      row.append(' ', textButton('Protokoll', () => showLog('/api/fresh/log/' + run.id, 'fresh-output')));
      return row;
    }));
    if (busy) freshTimer = setTimeout(loadFresh, 3000);
  } catch (error) {
    el('fresh-status').textContent = error.message;
  }
}

el('fresh-form').addEventListener('submit', event => {
  event.preventDefault();
  const host = el('fresh-host').value;
  if (!host) return;
  confirmCatalog('Basisinstallation auf genau einem frisch aufgenommenen Client: ' + host, async () => {
    el('fresh-start').disabled = true;
    try {
      await request('/api/fresh/start', {host, confirmed: true});
      await loadFresh();
    } catch (error) {
      el('fresh-status').textContent = error.message;
      el('fresh-start').disabled = false;
    }
  });
});

async function loadMaintenance() {
  el('maintenance-start').disabled = true;
  try {
    const data = await request('/api/maintenance');
    el('maintenance-start').disabled = !data.can_start;
    el('maintenance-status').textContent = data.available ? 'LAB-08B · ' + (data.active || 'unbekannt') : 'Wartungspilot nicht erreichbar';
    el('maintenance-output').textContent = data.output;
  } catch (error) {
    el('maintenance-status').textContent = error.message;
  }
}

el('maintenance-refresh').addEventListener('click', loadMaintenance);
el('maintenance-start').addEventListener('click', () => {
  confirmCatalog('Vorhandenen APT-Wartungsdienst auf LAB-08B starten?', async () => {
    el('maintenance-start').disabled = true;
    try {
      await request('/api/maintenance/start', {confirmed: true});
      await loadMaintenance();
    } catch (error) {
      el('maintenance-status').textContent = error.message;
    }
  });
});

if (currentPage === '/playbooks') loadCatalog();
if (currentPage === '/installationsfolge') loadFresh();
if (currentPage === '/wartung') loadMaintenance();
