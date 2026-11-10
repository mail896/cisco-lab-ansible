'use strict';

let groupState = null;
let groupSteps = [];
let groupDirty = false;
let groupPending = false;
let groupInitialized = false;
let groupLog = null;
let groupLogVersion = 0;
const groupTargets = new Set();

function groupRow(id) {
  return groupState?.modules.find(row => row.id === id)
    || groupState?.profiles.map(row => ({...row, id: 'software:' + row.id, label: row.name})).find(row => row.id === id)
    || {label: 'Nicht mehr verfügbar: ' + id};
}
function groupButtons() {
  const admin = labRole === 'admin';
  el('group-save').disabled = !admin || groupPending || !groupSteps.length || !el('group-name').value.trim();
  el('group-delete').disabled = !admin || groupPending || !el('group-choice').value;
  el('group-start').disabled = !admin || groupPending || groupState?.busy || groupDirty || !el('group-choice').value || !groupTargets.size;
  el('group-count').textContent = groupTargets.size + ' Rechner ausgewählt · maximal ' + (groupState?.parallelism || 16) + ' gleichzeitig';
  el('group-all').disabled = el('group-none').disabled = !admin || groupPending || groupState?.busy;
}
function groupChanged() { groupDirty = true; groupButtons(); }
function renderGroupSteps() {
  el('group-steps').replaceChildren(...groupSteps.map((id, index) => {
    const item = document.createElement('li');
    const row = document.createElement('div'); row.className = 'group-step-row';
    const name = document.createElement('span'); name.textContent = groupRow(id).label;
    row.append(name);
    for (const [direction, label, offset] of [['↑', 'Nach oben', -1], ['↓', 'Nach unten', 1]]) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'pager-button';
      button.textContent = direction; button.setAttribute('aria-label', label + ': ' + name.textContent);
      button.disabled = labRole !== 'admin' || groupPending || index + offset < 0 || index + offset >= groupSteps.length;
      button.addEventListener('click', () => {
        [groupSteps[index], groupSteps[index + offset]] = [groupSteps[index + offset], groupSteps[index]];
        groupChanged(); renderGroupSteps();
      });
      row.append(button);
    }
    item.append(row); return item;
  }));
}
function renderGroupModules() {
  const options = [...groupState.modules, ...groupState.profiles.map(row =>
    ({id: 'software:' + row.id, label: 'Software · ' + row.name, available: Boolean(row.approved)}))];
  el('group-modules').replaceChildren(...options.map(row => {
    const label = document.createElement('label'); label.className = 'check-label';
    const check = document.createElement('input'); check.type = 'checkbox'; check.value = row.id;
    check.checked = groupSteps.includes(row.id);
    check.disabled = labRole !== 'admin' || groupPending || row.available === false;
    label.append(check, row.label + (row.available === false ? ' · Pilotabnahme fehlt' : ''));
    check.addEventListener('change', () => {
      groupSteps = check.checked ? [...groupSteps, row.id] : groupSteps.filter(id => id !== row.id);
      groupChanged(); renderGroupSteps();
    });
    return label;
  }));
  renderGroupSteps();
}
function selectGroup() {
  const row = groupState?.groups.find(group => group.id === el('group-choice').value);
  el('group-name').value = row?.name || '';
  groupSteps = [...(row?.steps || [])]; groupDirty = false;
  el('group-message').textContent = row ? 'Gespeicherte Gruppe · bei Neuinstallationen automatisch auswählbar.' : 'Name vergeben und Bausteine auswählen.';
  renderGroupModules(); groupButtons();
}
function renderGroupTargets() {
  const available = new Set(groupState.hosts);
  for (const host of groupTargets) if (!available.has(host)) groupTargets.delete(host);
  el('group-targets').replaceChildren(...groupState.hosts.map(host => {
    const label = document.createElement('label'); label.className = 'check-label';
    const check = document.createElement('input'); check.type = 'checkbox'; check.value = host;
    check.checked = groupTargets.has(host); check.disabled = labRole !== 'admin' || groupPending || groupState.busy;
    check.addEventListener('change', () => { check.checked ? groupTargets.add(host) : groupTargets.delete(host); groupButtons(); });
    label.append(check, host.replace('LAB-', '')); return label;
  }));
}
async function loadGroupLog(run, host) {
  const version = ++groupLogVersion; groupLog = {run, host};
  el('group-log').hidden = false; el('group-log-title').textContent = run.group.name + ' · ' + host;
  try {
    const data = await request('/api/playbook-groups/log/' + run.id + '/' + host);
    if (version === groupLogVersion) el('group-output').textContent = data.output;
  } catch (error) { if (version === groupLogVersion) el('group-output').textContent = error.message; }
}
function renderGroupRun(run) {
  const details = document.createElement('details'); const heading = document.createElement('summary');
  const title = document.createElement('strong'); title.textContent = run.group.name;
  const time = document.createElement('time'); time.dateTime = run.started;
  time.textContent = new Date(run.started).toLocaleString('de-DE', {timeZone: 'Europe/Berlin', dateStyle: 'short', timeStyle: 'short'});
  heading.append(title, badge(catalogLabels[run.status] || run.status, ['failed', 'interrupted'].includes(run.status) ? 'warn' : 'neutral'), time);
  details.append(heading);
  const steps = document.createElement('p'); steps.className = 'field-hint'; steps.textContent = run.group.steps.map(row => row.label).join(' → '); details.append(steps);
  const list = document.createElement('ul'); list.className = 'appearance-result-list';
  for (const host of run.targets) {
    const row = document.createElement('li'); const name = document.createElement('strong'); name.textContent = host;
    const result = document.createElement('span'); result.textContent = run.results[host]?.message || run.active[host] || (run.status === 'running' ? 'Wartet' : 'Kein Ergebnis');
    const log = textButton('Protokoll', () => loadGroupLog(run, host)); row.append(name, result, log); list.append(row);
  }
  details.append(list); return details;
}
async function loadGroups() {
  cancelPoll('groups');
  try {
    const data = await requestHistory('/api/playbook-groups', 'group-runs', loadGroups);
    if (!data) return;
    groupState = data;
    const selected = el('group-choice').value;
    el('group-choice').replaceChildren(new Option('Neue Gruppe erstellen', ''), ...data.groups.map(row => new Option(row.name, row.id)));
    el('group-choice').value = data.groups.some(row => row.id === selected) ? selected : '';
    if (!groupInitialized) { groupInitialized = true; selectGroup(); }
    else if (selected && !el('group-choice').value) selectGroup();
    renderGroupTargets(); groupButtons();
    renderHistory('group-runs', data, renderGroupRun);
    el('group-empty').hidden = data.pagination.total > 0;
    el('group-activity').textContent = data.busy ? 'Auftrag läuft' : 'Bereit';
    if (groupLog) {
      const row = data.runs.find(run => run.id === groupLog.run.id);
      if (row && (row.status === 'running' || groupLog.run.status === 'running')) await loadGroupLog(row, groupLog.host);
    }
    schedulePoll('groups', loadGroups, data.busy ? 3000 : 15000);
  } catch (error) {
    el('group-message').textContent = error.message;
    el('group-start').disabled = true;
    schedulePoll('groups', loadGroups, 6000, true);
  }
}
el('group-choice').addEventListener('change', selectGroup);
el('group-name').addEventListener('input', groupChanged);
el('group-log-close').addEventListener('click', () => { groupLog = null; groupLogVersion++; el('group-log').hidden = true; el('group-output').textContent = ''; });
el('group-all').addEventListener('click', () => { for (const host of groupState.hosts) groupTargets.add(host); renderGroupTargets(); groupButtons(); });
el('group-none').addEventListener('click', () => { groupTargets.clear(); renderGroupTargets(); groupButtons(); });
el('group-form').addEventListener('submit', async event => {
  event.preventDefault(); if (labRole !== 'admin' || groupPending) return;
  groupPending = true; groupButtons();
  try {
    const row = await request('/api/playbook-groups/save', {identity: el('group-choice').value, name: el('group-name').value.trim(), steps: [...groupSteps]});
    await loadGroups(); el('group-choice').value = row.id; selectGroup();
    el('group-message').textContent = 'Gruppe gespeichert. Für Neuinstallationen und eingerichtete Clients verfügbar.';
  } catch (error) { el('group-message').textContent = error.message; }
  finally { groupPending = false; renderGroupModules(); groupButtons(); }
});
el('group-delete').addEventListener('click', () => {
  const identity = el('group-choice').value;
  confirmCatalog('Gruppe „' + el('group-name').value + '“ löschen? Bisherige Aufträge bleiben im Verlauf.', async () => {
    try { await request('/api/playbook-groups/delete', {identity, confirmed: true}); await loadGroups(); selectGroup(); }
    catch (error) { el('group-message').textContent = error.message; }
  }, 'Gruppe löschen');
});
el('group-start').addEventListener('click', () => {
  if (el('group-start').disabled) return;
  const group_id = el('group-choice').value; const targets = [...groupTargets].sort();
  const name = groupState.groups.find(row => row.id === group_id).name;
  confirmCatalog('Gruppe „' + name + '“ ausführen auf ' + targets.join(', ') + '?', async () => {
    groupPending = true; groupButtons();
    try { await request('/api/playbook-groups/start', {group_id, targets, confirmed: true}); resetHistory('group-runs'); await loadGroups(); }
    catch (error) { el('group-message').textContent = error.message; }
    finally { groupPending = false; renderGroupTargets(); groupButtons(); }
  }, 'Gruppe ausführen');
});
el('catalog-refresh').addEventListener('click', loadGroups);
if (currentPage === '/playbooks') loadGroups();
