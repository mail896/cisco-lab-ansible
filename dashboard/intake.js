'use strict';

const intakeLabels = {managed: 'In Verwaltung', ready: 'Bereit für Baseline', hardware: 'Gerät geprüft',
  new: 'Neues Image vorgesehen', running: 'Läuft', queued: 'In Warteschlange', failed: 'Fehler prüfen',
  interrupted: 'Unterbrochen', stopped: 'Angehalten'};
let intakeData = null;
let intakeSecretTimer = null;
let intakeBusy = false;
let intakePilotRun = null;
let intakeLinkApplied = false;

function clearIntakeSecrets() {
  clearTimeout(intakeSecretTimer);
  el('intake-password').value = '';
  el('intake-sudo').value = '';
}
window.addEventListener('pagehide', clearIntakeSecrets);
function intakeError(error) { el('intake-status').textContent = error.message; }

const intakeSelected = new Set();
let intakeAvailable = [];
let intakeRequestPending = false;
const intakeShownFailures = new Set();
function selectedIntakeHosts() { return [...intakeSelected]; }
function selectedIntakeReads() {
  return selectedIntakeHosts().map(host => intakeData?.reads.find(row => row.host === host)).filter(Boolean);
}
function renderIntakeRead() {
  const previous = new Map([...document.querySelectorAll('[data-intake-adapter]')]
    .map(select => [select.dataset.intakeAdapter, select.value]));
  const runs = selectedIntakeReads();
  const good = runs.filter(run => run.status === 'succeeded');
  el('intake-results').replaceChildren();
  for (const run of runs) {
    const row = document.createElement('div'); row.className = 'intake-result'; row.dataset.state = run.status;
    const heading = document.createElement('div');
    const name = document.createElement('strong'); name.textContent = run.host; heading.append(name);
    const state = document.createElement('span');
    state.textContent = {succeeded:'Gerät geprüft',running:'Wird geprüft …',queued:'Wartet …',
      key_changed:'Schlüsselwechsel bestätigen',failed:'Prüfung fehlgeschlagen',interrupted:'Unterbrochen'}[run.status] || run.status;
    heading.append(state); row.append(heading);
    if (run.status === 'succeeded') {
      const school = run.device.adapters.find(adapter => adapter.school);
      const info = document.createElement('p'); info.textContent = 'Schulkarte ' + school.interface + ' · ' + school.mac;
      row.append(info);
      const label = document.createElement('label'); label.textContent = 'USB-LAB-Adapter';
      const select = document.createElement('select'); select.dataset.intakeAdapter = run.id;
      select.setAttribute('aria-label', 'USB-LAB-Adapter ' + run.host);
      const labs = run.device.adapters.filter(adapter => adapter.usb);
      select.replaceChildren(...labs.map(adapter => new Option(adapter.interface + ' · ' + adapter.mac, adapter.interface)));
      if (labs.some(adapter => adapter.interface === previous.get(run.id))) select.value = previous.get(run.id);
      select.disabled = intakeBusy || intakeRequestPending;
      label.append(select); row.append(label);
    } else if (run.error) {
      const error = document.createElement('p'); error.textContent = run.error; row.append(error);
    }
    el('intake-results').append(row);
  }
  el('intake-review').hidden = !good.length;
  el('intake-summary').textContent = good.length === 1 ? '1 Gerät geprüft · Zugang einrichten.'
    : good.length + ' Geräte geprüft · Zugang gemeinsam einrichten.';
  if (!el('intake-student').value && good.some(run => run.student))
    el('intake-student').value = good.find(run => run.student).student;
  el('intake-fingerprint').textContent = good.map(run => run.host + ': ' + run.fingerprint).join(' · ');
  el('intake-setup').disabled = intakeBusy || intakeRequestPending || !good.length;
  if (!intakeRequestPending && runs.length && !good.length && runs.every(run => ['failed','interrupted','key_changed'].includes(run.status))) {
    if (runs.some(run => !intakeShownFailures.has(run.id))) clearIntakeSecrets();
    for (const run of runs) intakeShownFailures.add(run.id);
    el('intake-status').textContent = runs.length === 1 ? runs[0].error : 'Geräteprüfung fehlgeschlagen. Ergebnisse je Client beachten.';
  }
}
function renderIntakeSelection() {
  const available = new Map(intakeAvailable.map(row => [row.host, row]));
  const hosts = new Set([...available.keys(), ...intakeSelected]);
  el('intake-targets').replaceChildren();
  for (const host of [...hosts].sort()) {
    const label = document.createElement('label'); label.className = 'check-label';
    const input = document.createElement('input'); input.type = 'checkbox'; input.dataset.intakeHost = host;
    input.checked = intakeSelected.has(host); input.disabled = intakeBusy || intakeRequestPending || !available.has(host);
    label.append(input, host); el('intake-targets').append(label);
  }
  el('intake-all').disabled = el('intake-clear').disabled = intakeBusy || intakeRequestPending;
  el('intake-read').disabled = intakeBusy || intakeRequestPending || !intakeSelected.size
    || selectedIntakeHosts().some(host => !available.has(host));
}
function changeIntakeSelection() {
  clearIntakeSecrets(); el('intake-image').checked = false;
  el('intake-status').textContent = 'Imageinstallation der ausgewählten Clients bestätigen.';
  renderIntakeSelection(); renderIntakeRead();
}

function renderIntakeGate() {
  if (!intakeData) return;
  const accepted = intakeData.pilot_accepted;
  el('intake-gate').hidden = accepted;
  el('intake-gate').textContent = accepted
    ? ''
    : 'Zuerst LAB-08B allein über die GUI einrichten und vor Ort testen. Danach weitere Clients freigeben.';
  for (const input of el('fresh-targets').querySelectorAll('input[data-host]')) {
    const blocked = !accepted && input.dataset.host !== 'LAB-08B';
    input.disabled = freshBusy || blocked;
    if (blocked) input.checked = false;
  }
  updateFreshButtons();
  el('intake-accept-panel').hidden = accepted || !intakePilotRun;
  el('intake-accept').disabled = !el('intake-tested').checked || intakeBusy;
}

async function loadIntake() {
  cancelPoll('intake');
  try {
    const data = await request('/api/intake');
    intakeData = data;
    intakeBusy = data.busy;
    intakeAvailable = data.clients.filter(row => row.host !== 'LAB-08A' && !['managed', 'ready', 'running', 'queued'].includes(row.status));
    if (!intakeLinkApplied) {
      const requested = window.location.hash.slice(1);
      const initial = intakeAvailable.find(row => row.host === requested) || intakeAvailable.find(row => row.host === 'LAB-08B');
      if (initial) intakeSelected.add(initial.host);
      intakeLinkApplied = true;
    }
    setHostOptions(el('host'), data.clients.map(row => row.host));
    renderIntakeSelection(); renderIntakeRead();
    renderIntakeGate();
    schedulePoll('intake', loadIntake, data.busy ? 2500 : 15000);
  } catch (error) {
    el('intake-read').disabled = true;
    el('intake-setup').disabled = true;
    intakeError(error);
    schedulePoll('intake', loadIntake, 6000, true);
  }
}

el('intake-targets').addEventListener('change', event => {
  const host = event.target.dataset.intakeHost; if (!host) return;
  if (event.target.checked) intakeSelected.add(host); else intakeSelected.delete(host);
  changeIntakeSelection();
});
el('intake-all').addEventListener('click', () => {
  for (const row of intakeAvailable) intakeSelected.add(row.host);
  changeIntakeSelection();
});
el('intake-clear').addEventListener('click', () => { intakeSelected.clear(); changeIntakeSelection(); });
el('intake-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (intakeRequestPending || !intakeSelected.size) return;
  intakeRequestPending = true; renderIntakeSelection(); renderIntakeRead();
  el('intake-status').textContent = 'Geräte werden geprüft …';
  const hosts = selectedIntakeHosts();
  const payload = {hosts, ssh_password: el('intake-password').value,
    image_confirmed: el('intake-image').checked, replacement_fingerprints: {}};
  try {
    for (const run of selectedIntakeReads().filter(row => row.status === 'key_changed')) {
      if (!window.confirm(run.host + ': Der SSH-Schlüssel hat sich geändert. Ist das Gerät tatsächlich neu installiert?\n' + run.fingerprint))
        throw new Error('Schlüsselwechsel nicht bestätigt. Betroffenen Client abwählen.');
      payload.replacement_fingerprints[run.host] = run.fingerprint;
    }
    if (hosts.length === 1) {
      const single = {host: hosts[0], student:'', ssh_password: payload.ssh_password,
        image_confirmed: payload.image_confirmed, replacement_fingerprint: payload.replacement_fingerprints[hosts[0]] || ''};
      try {
        let run = await request('/api/intake/read', single);
        if (run.needs_key_confirmation) {
          if (!window.confirm(run.host + ': Der SSH-Schlüssel hat sich geändert. Ist das Gerät tatsächlich neu installiert?\n' + run.fingerprint))
            throw new Error('Schlüsselwechsel nicht bestätigt.');
          single.replacement_fingerprint = run.fingerprint;
          run = await request('/api/intake/read', single);
          if (run.needs_key_confirmation) throw new Error('SSH-Identität hat sich erneut geändert. Gerät prüfen.');
        }
      } finally { single.ssh_password = ''; }
    } else await request('/api/intake/read-many', payload);
    clearTimeout(intakeSecretTimer);
    intakeSecretTimer = setTimeout(clearIntakeSecrets, 5 * 60 * 1000);
  } catch (error) { intakeError(error); clearIntakeSecrets(); }
  finally { payload.ssh_password = ''; intakeRequestPending = false; await loadIntake(); }
});

el('intake-setup').addEventListener('click', async () => {
  const good = selectedIntakeReads().filter(run => run.status === 'succeeded');
  if (intakeRequestPending || !good.length || !el('intake-image').checked) {
    intakeError(new Error('Zuerst die frisch installierten Geräte prüfen.')); return;
  }
  if (!el('intake-password').value) {
    el('intake-password').focus(); intakeError(new Error('Administratorpasswort erneut eingeben; es wurde nicht gespeichert.')); return;
  }
  const student = el('intake-student'); student.value = student.value.trim();
  if (!student.reportValidity()) return;
  const payload = {targets: good.map(run => ({run_id: run.id,
      lab_interface: document.querySelector('[data-intake-adapter="'+run.id+'"]').value})),
    student: student.value, ssh_password: el('intake-password').value,
    sudo_password: el('intake-sudo').value || el('intake-password').value, confirmed: true};
  intakeRequestPending = true; clearIntakeSecrets(); renderIntakeSelection(); renderIntakeRead();
  try {
    let result;
    if (good.length === 1) {
      const {targets, ...single} = payload;
      Object.assign(single, targets[0]);
      try { result = await request('/api/intake/setup', single); }
      finally { single.ssh_password = ''; single.sudo_password = ''; }
    } else result = await request('/api/intake/setup-many', payload);
    freshLog = result.id || result.runs?.[0]?.id;
    for (const run of good) intakeSelected.delete(run.host);
    el('intake-status').textContent = 'Zugänge werden eingerichtet. Anschließend die vorbereiteten Clients für die Baseline auswählen.';
    await loadFresh();
  } catch (error) { intakeError(error); }
  finally { payload.ssh_password = ''; payload.sudo_password = ''; intakeRequestPending = false; await loadIntake(); }
});

document.addEventListener('fresh-state', event => {
  intakePilotRun = event.detail.pilot_run || event.detail.runs.find(run => run.host === 'LAB-08B' && !run.kind
    && run.status === 'succeeded' && run.phase === 'maintenance');
  renderIntakeGate();
});
el('intake-tested').addEventListener('change', renderIntakeGate);
el('intake-accept').addEventListener('click', async () => {
  if (!intakePilotRun || !el('intake-tested').checked) return;
  el('intake-accept').disabled = true;
  try {
    await request('/api/intake/accept', {run_id: intakePilotRun.id, confirmed: true});
    el('intake-accept-status').textContent = 'LAB-08B abgenommen.';
    await loadIntake(); await loadFresh();
  } catch (error) { el('intake-accept-status').textContent = error.message; }
});

let overviewLoading = false;
async function loadOverview() {
  if (overviewLoading) return;
  overviewLoading = true;
  try {
    const [data, teacher] = await Promise.all([request('/api/intake'), request('/api/teacher/status')]);
    const managed = data.clients.filter(row => row.managed ?? row.status === 'managed').length;
    el('overview-total').textContent = data.clients.length;
    el('overview-managed').textContent = managed;
    el('overview-pending').textContent = data.clients.length - managed;
    const teacherEnabled = teacher.configuration.enabled;
    el('overview-teacher').textContent = teacherEnabled ? 'Aktiv' : 'Aus';
    el('overview-status').textContent = data.busy ? 'Auftrag läuft' : 'Bereit';
    el('overview-status').dataset.state = data.busy ? 'busy' : 'ready';
    el('overview-status').title = data.busy ? 'Ein Verwaltungsauftrag läuft.' : 'Es läuft kein manueller Verwaltungsauftrag.';
    const pending = data.clients.filter(row => !(row.managed ?? row.status === 'managed'));
    el('overview-next').textContent = !data.pilot_accepted ? 'LAB-08B einrichten' : pending.length ? 'Weitere Clients einrichten' : 'Alle Clients aufgenommen';
    el('overview-next-description').textContent = !data.pilot_accepted
      ? 'Nach dem frischen IServ-Image den Client prüfen, einrichten und vor Ort abnehmen.'
      : pending.length ? 'Frisch installierte Rechner können jetzt Schritt für Schritt folgen.'
      : 'Die Einrichtung ist abgeschlossen. Wartungsstand und Ergebnisse findest du unter Wartung.';
    el('overview-next-label').textContent = labRole === 'reader' ? 'Einrichtungsstand' : 'Als Nächstes';
    const setupLink = el('overview-setup-link');
    setupLink.href = basePath + (pending.length || !data.pilot_accepted ? '/installationsfolge' : '/wartung');
    setupLink.querySelector('span').textContent = !pending.length && data.pilot_accepted ? 'Wartung ansehen'
      : labRole === 'reader' ? 'Einrichtung ansehen' : 'Client einrichten';
    const shortLabels = {managed: 'Verwaltet', ready: 'Bereit', hardware: 'Geprüft', new: 'Image geplant',
      running: 'In Arbeit', queued: 'Wartet', failed: 'Fehler', interrupted: 'Unterbrochen', stopped: 'Angehalten'};
    const cards = data.clients.map(row => {
      const card = document.createElement('a'); card.className = 'device-card';
      card.dataset.roomHost = row.host;
      card.href = basePath + ((row.managed ?? row.status === 'managed') ? '/sammelaktionen#' : '/installationsfolge#') + row.host;
      card.dataset.state = ['failed', 'interrupted'].includes(row.status) ? 'error' : row.status;
      card.title = row.host + ' · ' + (intakeLabels[row.status] || row.status);
      card.setAttribute('aria-label',card.title);
      card.append(badge(shortLabels[row.status] || row.status,
        row.status === 'managed' ? 'good' : ['failed', 'interrupted'].includes(row.status) ? 'warn' : 'neutral'));
      return card;
    });
    const teams = new Map();
    data.clients.forEach((row, index) => {
      const match = /^LAB-(\d{2})[AB]$/.exec(row.host);
      const team = match ? match[1] : row.host;
      if (!teams.has(team)) {
        const group = document.createElement('section'); group.className = 'team-card';
        const teamName = team === '08' ? 'Pilotrechner' : 'Team ' + team;
        group.setAttribute('aria-label', teamName);
        const title = document.createElement('h3'); title.textContent = teamName;
        group.append(title); teams.set(team, group);
      }
      teams.get(team).append(cards[index]);
    });
    const teacherCard = document.createElement('a'); teacherCard.className = 'device-card teacher-card';
    teacherCard.href = basePath + '/sammelaktionen#teacher';
    teacherCard.setAttribute('aria-label','LAB-Teacher · Sammelaktionen');
    teacherCard.append(badge('Wartung ' + (teacherEnabled ? 'aktiv' : 'aus'), teacherEnabled ? 'good' : 'neutral'));
    const hint = document.createElement('span'); hint.className = 'teacher-link'; hint.textContent = 'Aktionen →';
    teacherCard.append(hint);
    for(const [team,group] of teams) {
      for(const host of LabRoomLayout.hosts(team)) {
        const card=group.querySelector('[data-room-host="'+host+'"]');
        if(card)group.append(card);
      }
    }
    LabRoomLayout.render(el('overview-devices'),teams,teacherCard);
  } catch (error) {
    el('overview-status').textContent = 'Status nicht verfügbar';
    el('overview-status').dataset.state = 'error';
    el('overview-status').title = error.message;
    el('overview-next-description').textContent = 'Status konnte nicht geladen werden: ' + error.message;
  }
  finally { overviewLoading = false; }
}

if (currentPage === '/') loadOverview();
if (currentPage === '/installationsfolge') loadIntake();
