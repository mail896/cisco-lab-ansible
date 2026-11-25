'use strict';

// Linux diagnosis generator. Same collection, template and export rules as the
// routing generator; scenarios and outputs are simulated server-side.
let cliState = null;
let cliTask = null;
let cliBusy = false;
let cliSolution = false;
let cliTeamNames = Array(8).fill('');
const cliEditors = new Map();
let cliEditorLoading = null;
const cliTextIds = ['title', 'introduction', 'work', 'hints'];
const cliApi = '/api/cli-exercises';

function cliNode(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function cliMessage(text) { el('cli-message').textContent = text; }
function cliLevel() { return document.querySelector('input[name=cli-level]:checked')?.value || 'einstieg'; }
function cliThemes() { return [...document.querySelectorAll('input[name=cli-theme]:checked')].map(node => node.value); }
function cliNames(count = cliTask?.options.teams || Number(el('cli-teams').value)) { return cliTeamNames.slice(0, count); }
function cliSettings() {
  return {level: cliLevel(), teams: Number(el('cli-teams').value), themes: cliThemes(),
          ipv6: el('cli-ipv6').checked, fault: el('cli-fault').value,
          part_a: el('cli-part-a').checked, part_b: el('cli-part-b').checked, part_c: el('cli-part-c').checked,
          wireshark: el('cli-part-a').checked && el('cli-wireshark').checked};
}
function cliTexts() {
  return Object.fromEntries(cliTextIds.map(key => {
    const id = 'cli-' + key;
    return [key, cliEditors.has(id) ? cliEditors.get(id).value : el(id).value];
  }));
}

function cliRenderFaults(selected = el('cli-fault').value) {
  const catalog = cliState?.catalog;
  if (!catalog) return;
  const themes = cliThemes(), ipv6 = el('cli-ipv6').checked;
  const groups = Object.entries(catalog.themes).filter(([key]) => themes.includes(key)).map(([key, label]) => {
    const group = document.createElement('optgroup'); group.label = label;
    for (const fault of catalog.faults.filter(row => row.theme === key && (ipv6 || !row.ipv6))) group.append(new Option(fault.title, fault.id));
    return group;
  });
  el('cli-fault').replaceChildren(new Option('Zufällig, je Team verschieden', 'zufall'), ...groups);
  el('cli-fault').value = [...el('cli-fault').options].some(option => option.value === selected) ? selected : 'zufall';
}
function cliRenderNames() {
  const count = Number(el('cli-teams').value);
  el('cli-team-names').replaceChildren(...Array.from({length: count}, (_, i) => {
    const label = cliNode('label', count === 1 ? 'Aufgabenname / Schüler im Team' : 'Team ' + (i + 1) + ' · Name / Schüler');
    const input = cliNode('input'); input.type = 'text'; input.maxLength = 160; input.autocomplete = 'off';
    input.value = cliTeamNames[i]; input.placeholder = 'z. B. Mia & Jonas';
    input.addEventListener('input', () => { cliTeamNames[i] = input.value; cliShowName(); });
    label.append(input); return label;
  }));
}
function cliLoadNames(names = []) { cliTeamNames = Array.from({length: 8}, (_, i) => names[i] || ''); cliRenderNames(); }
function cliShowName() {
  const name = cliTeamNames[Number(el('cli-team').value || 1) - 1] || '';
  el('cli-preview-name').textContent = name; el('cli-preview-name').hidden = !name;
}
function cliFill(text, settings) {
  for (const key of cliTextIds) {
    const id = 'cli-' + key; el(id).value = text[key];
    if (cliEditors.has(id)) cliEditors.get(id).value = text[key];
  }
  for (const node of document.querySelectorAll('input[name=cli-level]')) node.checked = node.value === settings.level;
  for (const node of document.querySelectorAll('input[name=cli-theme]')) node.checked = settings.themes.includes(node.value);
  el('cli-ipv6').checked = settings.ipv6;
  el('cli-teams').value = String(settings.teams);
  for (const key of ['a', 'b', 'c']) el('cli-part-' + key).checked = settings['part_' + key] ?? key === 'b';
  el('cli-wireshark').checked = Boolean(settings.wireshark);
  cliRenderFaults(settings.fault);
  cliRenderNames();
  cliControls();
}
function cliControls() {
  const template = cliState?.templates.find(row => row.id === el('cli-template').value);
  const parts = ['a', 'b', 'c'].some(key => el('cli-part-' + key).checked);
  el('cli-wireshark').disabled = !el('cli-part-a').checked;
  el('cli-fault').disabled = !el('cli-part-b').checked;
  el('cli-create').disabled = cliBusy || !cliThemes().length || !parts;
  el('cli-reset').disabled = cliBusy;
  el('cli-save').disabled = cliBusy || !cliTask;
  el('cli-text-apply').disabled = cliBusy || !cliTask;
  el('cli-template-save').disabled = cliBusy || (template ? !template.editable : labRole !== 'admin');
  el('cli-template-save').textContent = template ? 'Vorlage aktualisieren' : 'Standardvorlage aktualisieren';
  el('cli-template-copy').disabled = cliBusy;
  for (const id of ['cli-template-delete', 'cli-template-remove']) { el(id).hidden = !template?.editable; el(id).disabled = cliBusy; }
  el('cli-standard-save').hidden = labRole !== 'admin' || !template;
  el('cli-standard-save').disabled = cliBusy;
  const deletion = cliState?.deletion;
  el('cli-delete-all').disabled = cliBusy || !deletion?.count;
  el('cli-delete-all').textContent = (deletion?.scope === 'own' ? 'Meine Aufgaben löschen' : 'Alle Aufgaben löschen') + (deletion?.count ? ' (' + deletion.count + ')' : '');
}
async function cliAction(action) {
  if (cliBusy) return;
  cliBusy = true; cliControls();
  try { await action(); }
  catch (error) { cliMessage(error.message); }
  finally { cliBusy = false; cliControls(); }
}

async function loadCliExercises(first = false) {
  try {
    const data = await requestHistory(cliApi, 'cli-history', () => loadCliExercises());
    if (!data) return;
    cliState = data;
    const choice = el('cli-template').value;
    el('cli-template').replaceChildren(new Option('Standardvorlage', ''), ...data.templates.map(row => new Option(row.title + (row.editable ? '' : ' · gemeinsam'), row.id)));
    if (data.templates.some(row => row.id === choice)) el('cli-template').value = choice;
    if (first) cliFill(data.defaults.text, data.defaults.options);
    renderHistory('cli-history', data, row => {
      const node = cliNode('div', undefined, 'exercise-history-row');
      const title = cliNode('div'); title.append(cliNode('strong', row.label || row.title));
      title.append(cliNode('small', new Date(row.created).toLocaleString('de-DE', {timeZone: 'Europe/Berlin', dateStyle: 'short', timeStyle: 'short'}) + ' · ' + row.author + ' · ' + row.options.teams + ' Variante(n)' + (row.options.ipv6 ? ' · mit IPv6' : '')));
      if (row.faults?.length) {
        const chips = cliNode('div', undefined, 'cli-chips');
        for (const fault of row.faults) chips.append(cliNode('span', (row.faults.length > 1 ? 'T' + fault.team + ' · ' : '') + fault.title, 'cli-chip'));
        title.append(chips);
      }
      const controls = cliNode('div', undefined, 'exercise-buttons');
      const open = cliNode('button', 'Öffnen', 'pager-button'); open.type = 'button';
      open.addEventListener('click', () => cliAction(async () => {
        cliTask = await request(cliApi + '/task/' + row.id);
        cliFill(cliTask.text, cliTask.options); el('cli-template').value = '';
        cliLoadNames(cliTask.team_names); cliRender();
        cliMessage('Gespeicherte Aufgabe geöffnet. Geänderte Einstellungen erzeugen beim nächsten Klick eine neue Aufgabe.');
      }));
      controls.append(open);
      if (row.editable) {
        const remove = cliNode('button', 'Löschen', 'pager-button'); remove.type = 'button';
        remove.addEventListener('click', () => cliAction(async () => {
          if (!window.confirm('Aufgabe „' + row.title + '“ aus der Sammlung entfernen?')) return;
          await request(cliApi + '/delete', {kind: 'tasks', identity: row.id, confirmed: true});
          if (cliTask?.id === row.id) { cliTask = null; cliRender(); }
          await loadCliExercises(); cliMessage('Aufgabe entfernt.');
        }));
        controls.append(remove);
      }
      node.append(title, controls); return node;
    });
    if (!data.runs.length) el('cli-history').append(cliNode('p', 'Noch keine Linux-Aufgaben gespeichert.', 'field-hint'));
    cliControls();
  } catch (error) { cliMessage(error.message); }
}

function cliVariant() { return cliTask?.variants[Number(el('cli-team').value || 1) - 1]; }
function cliRender(team = '1') {
  const task = cliTask;
  el('cli-empty').hidden = Boolean(task); el('cli-result').hidden = !task;
  el('cli-solution-toggle').hidden = !task;
  el('cli-team-label').hidden = !task || task.variants.length === 1;
  el('cli-preview-title').textContent = task ? task.title : labStudent ? 'Ihre nächste Störung' : 'Deine nächste Störung';
  if (!task) { el('cli-terminals').replaceChildren(); return; }
  el('cli-team').replaceChildren(...task.variants.map(row => new Option('Team ' + row.team, String(row.team))));
  el('cli-team').value = team;
  cliRenderVariant();
}
function cliTerminal(variant, row) {
  const box = cliNode('figure', undefined, 'cli-terminal');
  if (row.card) box.classList.add('is-card');
  if (row.noise) box.classList.add('is-noise');
  const bar = cliNode('figcaption', undefined, 'cli-terminal-bar');
  bar.append(cliNode('span', undefined, 'cli-dots'), cliNode('span', variant.host.user + '@' + variant.host.name, 'cli-terminal-title'));
  if (row.card) bar.append(cliNode('span', 'Ausgabekarte · auf Nachfrage', 'cli-tag'));
  if (row.noise) bar.append(cliNode('span', 'Zusatzausgabe', 'cli-tag cli-tag-noise'));
  const pre = cliNode('pre'); pre.tabIndex = 0;
  const prompt = cliNode('span', undefined, 'cli-prompt-line');
  prompt.append(cliNode('span', variant.host.user + '@' + variant.host.name, 'cli-user'), document.createTextNode(':'),
                cliNode('span', '~', 'cli-path'), document.createTextNode('$ '), cliNode('strong', row.command));
  pre.append(prompt, document.createTextNode('\n'));
  for (const line of row.text.split('\n')) {
    const span = cliNode('span', line || ' ', 'cli-line');
    if (row.evidence.some(token => token && line.includes(token))) span.classList.add('is-evidence');
    pre.append(span, document.createTextNode('\n'));
  }
  box.append(bar, pre);
  return box;
}
function cliRenderSolution(variant) {
  const sol = variant.solution, box = el('cli-solution');
  const list = (title, rows, code) => {
    const part = cliNode('div'); part.append(cliNode('h3', title));
    if (code) { const pre = cliNode('pre', rows.join('\n'), 'cli-code'); part.append(pre); }
    else part.append(cliNode('p', rows));
    return part;
  };
  const head = cliNode('div', undefined, 'cli-solution-head');
  head.append(cliNode('span', variant.fault_title, 'cli-chip cli-chip-strong'), cliNode('span', cliState?.catalog.themes[variant.theme] || '', 'cli-chip'));
  box.replaceChildren(head, list('Ursache', sol.cause), list('Beleg', sol.evidence), list('Korrektur', sol.fix, true),
                      list('Prüfung danach', sol.verify, true), list('Hintergrund', sol.explanation));
  const glossary = cliNode('div', undefined, 'cli-glossary');
  glossary.append(cliNode('h3', 'Begriffe kurz erklärt'));
  const terms = cliNode('dl');
  for (const row of variant.terms || []) terms.append(cliNode('dt', row.term), cliNode('dd', row.text));
  glossary.append(terms);
  el('cli-terms').replaceChildren(glossary);
  el('cli-terms').dataset.empty = String(!variant.terms?.length);
}
function cliTable(headings, rows) {
  const table = cliNode('table', undefined, 'cli-table');
  const head = cliNode('tr'); for (const value of headings) head.append(cliNode('th', value));
  table.append(head);
  for (const row of rows) {
    const line = cliNode('tr');
    for (const value of row) { const cell = cliNode('td'); if (value instanceof Node) cell.append(value); else cell.textContent = value; line.append(cell); }
    table.append(line);
  }
  const wrap = cliNode('div', undefined, 'cli-table-wrap'); wrap.append(table); return wrap;
}
function cliSolutionOnly(node) { node.classList.add('cli-solution-only'); return node; }
function cliRenderLive(task, variant) {
  const box = el('cli-view-a'), live = variant.live;
  box.hidden = !live;
  if (!live) { box.replaceChildren(); return; }
  const level = task.options.level;
  const command = value => level === 'einstieg' ? value : level === 'fortgeschritten' ? 'Werkzeug: ' + value.split(' ')[0] : '';
  const rows = live.tasks.map((row, index) => {
    const cell = cliNode('div'); cell.append(cliNode('p', row.task));
    const shown = command(row.command);
    if (shown) cell.append(cliNode('code', shown, 'cli-cmd'));
    cell.append(cliSolutionOnly(cliNode('code', row.command, 'cli-cmd')));
    const expected = cliSolutionOnly(cliNode('div'));
    expected.append(cliNode('p', row.expected), cliNode('small', row.method));
    return ['B' + (index + 1), cell, expected];
  });
  const parts = [cliNode('h3', 'Teil B · Live am LAB-Client erkunden', 'cli-part-title'),
                 cliNode('p', 'Nur lesende Befehle am echten Client. Erwartete Werte laut APP-Inventar, Stand ' + live.stamp + '.', 'field-hint'),
                 cliTable(['Nr.', 'Auftrag', 'Erwartung'], rows)];
  parts[2].querySelector('table').classList.add('cli-live-table');
  if (live.capture) {
    const capture = cliNode('div', undefined, 'cli-capture');
    capture.append(cliNode('h4', 'Wireshark-Mitschnitt im Labornetz ' + live.capture.network), cliNode('p', live.capture.setup));
    live.capture.steps.forEach((step, index) => {
      const card = cliNode('div', undefined, 'cli-step');
      card.append(cliNode('strong', 'W' + (index + 1) + ' · ' + step.task), cliNode('code', step.command, 'cli-cmd'),
                  cliNode('code', 'Filter: ' + step.filter, 'cli-cmd'), cliNode('p', step.question),
                  cliSolutionOnly(cliNode('p', 'Erwartung: ' + step.expected, 'cli-expected')));
      capture.append(card);
    });
    parts.push(capture);
  }
  box.replaceChildren(...parts);
}
function cliRenderPt(variant) {
  const box = el('cli-view-c'), pt = variant.pt;
  box.hidden = !pt;
  if (!pt) { box.replaceChildren(); return; }
  const card = cliNode('div', undefined, 'cli-ticket cli-faultcard');
  card.append(cliNode('p', '✂ Fehlerkarte · nur für Team ' + variant.team, 'cli-ticket-label'), cliNode('p', pt.fault.card));
  const solution = cliSolutionOnly(cliNode('div', undefined, 'cli-solution'));
  const item = (title, value) => { const part = cliNode('div'); part.append(cliNode('h3', title), cliNode('p', value)); return part; };
  solution.append(item('Fehler', pt.fault.title), item('Symptom beim Partnerteam', pt.fault.symptom),
                  item('So wird es gefunden', pt.fault.detect), item('Korrektur', pt.fault.fix));
  box.replaceChildren(cliNode('h3', 'Teil C · Packet-Tracer-Teamtausch', 'cli-part-title'),
    cliNode('p', 'LAN A ' + pt.networks.lan_a + ' · Transfernetz ' + pt.networks.transfer + ' · LAN B ' + pt.networks.lan_b +
                 (pt.partner ? ' · Fehlerdatei geht an Team ' + pt.partner : ' · Tausch mit einem anderen Team oder der Lehrkraft'), 'field-hint'),
    cliTable(['Gerät', 'Anschluss', 'Adresse', 'Gateway', 'DNS'], pt.devices.map(d => [d.device, d.port, d.address, d.gateway, d.dns])),
    cliNode('pre', pt.routes.join('\n'), 'cli-code'), card, solution);
}
function cliRenderVariant() {
  const task = cliTask, variant = cliVariant();
  if (!variant) return;
  el('cli-meta').textContent = (task.saved ? 'Gespeicherter Stand' : 'Ungespeicherte Vorschau') + ' · ' + task.id.slice(0, 8) + ' · Niveau ' + task.level_label + ' · ' + task.theme_labels.join(' + ');
  cliShowName();
  el('cli-ticket-host').textContent = variant.host.name;
  el('cli-symptom').textContent = variant.symptom;
  el('cli-terminals').replaceChildren(...variant.outputs.map(row => cliTerminal(variant, row)));
  cliRenderSolution(variant);
  const partB = task.options.part_b !== false;
  const several = [task.options.part_a, partB, task.options.part_c].filter(Boolean).length > 1;
  el('cli-view-b').hidden = !partB;
  el('cli-view-b').querySelector('.cli-part-title').hidden = !several;
  cliRenderLive(task, variant);
  cliRenderPt(variant);
  cliApplySolution();
  el('cli-save').textContent = task.saved ? 'Aufgabenstand speichern' : 'Aufgabe speichern';
  const root = basePath + cliApi + '/export/';
  el('cli-pdf').href = root + 'pdf/' + task.id + '/' + variant.team; el('cli-zip').href = root + 'zip/' + task.id + '/' + variant.team;
  el('cli-pdf-all').href = root + 'pdf/' + task.id + '/all'; el('cli-zip-all').href = root + 'zip/' + task.id + '/all';
  for (const id of ['cli-pdf-all', 'cli-zip-all']) el(id).hidden = task.variants.length === 1;
  const hints = [];
  if (partB) hints.push(task.options.level === 'profi'
    ? 'Teil A: Im Schüler-PDF steht nur die erste Ausgabe; die übrigen liegen als Ausgabekarten in der Musterlösung.'
    : 'Teil A: Störung, Ausgaben und leeres Diagnoseprotokoll' + (task.options.level === 'einstieg' ? ', dazu Leitfragen und Befehlshilfe.' : '.'));
  if (task.options.part_a) hints.push('Teil B: Aufträge mit Ergebnisspalte; die Musterlösung enthält die Sollwerte.');
  if (task.options.part_c) hints.push('Teil C: Bauplan, Prüfauftrag und am Ende jeder Teamseite die Fehlerkarte zum Abtrennen.');
  el('cli-export-hint').textContent = hints.join(' ');
}
function cliApplySolution() {
  el('cli-result').classList.toggle('show-solution', cliSolution);
  el('cli-solution').hidden = !cliSolution;
  el('cli-terms').hidden = !cliSolution || el('cli-terms').dataset.empty === 'true';
  el('cli-solution-toggle').setAttribute('aria-pressed', String(cliSolution));
  el('cli-solution-toggle').textContent = cliSolution ? 'Lösung ausblenden' : 'Lösung zeigen';
}

async function cliOpenTab(name) {
  for (const key of ['scenario', 'text']) {
    const selected = key === name;
    el('cli-panel-' + key).hidden = !selected;
    el('cli-tab-' + key).setAttribute('aria-selected', String(selected)); el('cli-tab-' + key).tabIndex = selected ? 0 : -1;
  }
  if (name !== 'text') return;
  try {
    if (!window.Jodit) {
      if (!cliEditorLoading) cliEditorLoading = new Promise((resolve, reject) => {
        const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = basePath + '/vendor/jodit/jodit.min.css'; document.head.append(css);
        const script = document.createElement('script'); script.src = basePath + '/vendor/jodit/jodit.min.js'; script.onload = resolve;
        script.onerror = () => reject(new Error('Der Texteditor konnte nicht geladen werden.')); document.head.append(script);
      });
      await cliEditorLoading;
    }
    for (const area of el('cli-panel-text').querySelectorAll('[data-cli-rich]')) {
      if (cliEditors.has(area.id)) continue;
      cliEditors.set(area.id, window.Jodit.make(area, {
        language: 'de', height: area.id === 'cli-work' ? 280 : 170, toolbarAdaptive: false, toolbarSticky: false,
        showXPathInStatusbar: false, showCharsCounter: false, showWordsCounter: false, allowResizeX: false, allowResizeY: false,
        buttons: ['undo', 'redo', '|', 'bold', 'italic', 'underline', '|', 'ul', 'ol', '|', 'eraser', 'fullsize'],
        disablePlugins: ['file', 'image', 'video', 'source', 'iframe', 'speech-recognize', 'powered-by-jodit'],
        uploader: {insertImageAsBase64URI: false}, askBeforePasteHTML: false, askBeforePasteFromWord: false,
        defaultActionOnPasteFromWord: 'insert_clear_html', theme: document.documentElement.dataset.theme === 'dark' ? 'dark' : 'default',
      }));
    }
  } catch (error) { cliMessage(error.message + (labStudent ? ' Sie können die Texte weiterhin im Textfeld bearbeiten.' : ' Du kannst die Texte weiterhin im Textfeld bearbeiten.')); }
}
async function cliApplyTexts(save = false) {
  if (!cliTask) return;
  const team = el('cli-team').value;
  cliTask = await request(cliApi + '/' + (save ? 'save' : 'text'), {identity: cliTask.id, text: cliTexts(), team_names: cliNames()});
  cliRender(team);
}
async function cliSaveStandard() {
  if (!window.confirm('Diese Texte und Einstellungen als gemeinsame Standardvorlage für neue Linux-Aufgaben festlegen? Gespeicherte Aufgaben bleiben unverändert.')) return;
  await request(cliApi + '/standard', {settings: cliSettings(), text: cliTexts()});
  el('cli-template').value = ''; await loadCliExercises(); cliMessage('Standardvorlage aktualisiert.');
}

if (el('cli-form')) {
  el('cli-form').addEventListener('change', event => {
    if (event.target.name === 'cli-theme' || event.target.id === 'cli-ipv6') cliRenderFaults();
    if (event.target.id === 'cli-teams') cliRenderNames();
    if (event.target.id === 'cli-part-a' && !event.target.checked) el('cli-wireshark').checked = false;
    cliControls();
  });
  el('cli-form').addEventListener('submit', event => {
    event.preventDefault();
    cliAction(async () => {
      cliMessage('Störung wird erzeugt …');
      cliTask = await request(cliApi + '/preview', {settings: cliSettings(), text: cliTexts(), team_names: cliNames(Number(el('cli-teams').value))});
      cliRender();
      cliMessage(labStudent ? 'Neue Übungsaufgabe erzeugt. Laden Sie Aufgaben-PDF und Musterlösung herunter; gespeichert wird nichts.' : 'Ungespeicherte Vorschau. Würfle erneut oder speichere die passende Aufgabe. Die Vorschau verfällt bei einem APP-Neustart.');
    });
  });
  el('cli-solution-toggle').addEventListener('click', () => { cliSolution = !cliSolution; cliApplySolution(); });
  el('cli-team').addEventListener('change', cliRenderVariant);
  el('cli-reload').addEventListener('click', () => loadCliExercises());
  el('cli-template').addEventListener('change', () => {
    const template = cliState.templates.find(row => row.id === el('cli-template').value);
    cliFill(template?.text || cliState.defaults.text, template?.options || cliState.defaults.options);
  });
  for (const [id, copy] of [['cli-template-save', false], ['cli-template-copy', true]]) el(id).addEventListener('click', () => cliAction(async () => {
    const identity = el('cli-template').value;
    if (!copy && !identity) { await cliSaveStandard(); return; }
    const text = cliTexts();
    if (copy) {
      const name = window.prompt('Name der neuen Vorlage', text.title + ' (Kopie)');
      if (name === null) return;
      if (!name.trim()) throw new Error('Bitte einen Namen für die neue Vorlage eingeben.');
      text.title = name.trim();
    }
    const result = await request(cliApi + '/template', {identity: copy ? '' : identity, text, settings: cliSettings()});
    await loadCliExercises(); el('cli-template').value = result.id; cliFill(result.text, result.options);
    cliMessage(copy ? 'Neue Vorlage gespeichert.' : 'Vorlage aktualisiert.');
  }));
  for (const id of ['cli-template-delete', 'cli-template-remove']) el(id).addEventListener('click', () => cliAction(async () => {
    if (!window.confirm('Diese Vorlage entfernen? Gespeicherte Aufgaben bleiben erhalten.')) return;
    await request(cliApi + '/delete', {kind: 'templates', identity: el('cli-template').value, confirmed: true});
    el('cli-template').value = ''; await loadCliExercises(); cliMessage('Vorlage entfernt.');
  }));
  el('cli-standard-save').addEventListener('click', () => cliAction(cliSaveStandard));
  el('cli-reset').addEventListener('click', () => {
    if (!cliState || !window.confirm('Den aktuellen Entwurf samt Vorschau zurücksetzen? Gespeicherte Aufgaben und Vorlagen bleiben erhalten.')) return;
    cliTask = null; cliSolution = false; el('cli-template').value = ''; cliLoadNames();
    cliFill(cliState.defaults.text, cliState.defaults.options); cliRender(); cliOpenTab('scenario');
    cliMessage('Entwurf zurückgesetzt. Die Sammlung bleibt erhalten.');
  });
  el('cli-delete-all').addEventListener('click', () => cliAction(async () => {
    const preview = cliState.deletion;
    if (!window.confirm(preview.count + ' ' + (preview.scope === 'all' ? 'gespeicherte Linux-Aufgaben aus der gesamten Sammlung' : 'eigene gespeicherte Linux-Aufgaben') + ' unwiderruflich löschen? Vorlagen bleiben erhalten.')) return;
    const result = await request(cliApi + '/delete-all', {token: preview.token, confirmed: true});
    if (cliTask?.editable) { cliTask = null; cliRender(); }
    resetHistory('cli-history'); await loadCliExercises(); cliMessage(result.deleted + ' Aufgaben gelöscht.');
  }));
  el('cli-save').addEventListener('click', () => cliAction(async () => {
    await cliApplyTexts(true); resetHistory('cli-history'); await loadCliExercises();
    cliMessage('Aufgabenstand mit den aktuellen Texten gespeichert.');
  }));
  el('cli-text-apply').addEventListener('click', () => cliAction(async () => {
    await cliApplyTexts(); cliOpenTab('scenario'); cliMessage('Aktuelle Texte übernommen. Störung und Ausgaben sind unverändert.');
  }));
  for (const id of ['cli-pdf', 'cli-zip', 'cli-pdf-all', 'cli-zip-all']) el(id).addEventListener('click', event => {
    event.preventDefault();
    cliAction(async () => {
      await cliApplyTexts();
      const link = document.createElement('a'); link.href = el(id).href; link.hidden = true; document.body.append(link); link.click(); link.remove();
      cliMessage(labStudent ? 'Download gestartet.' : 'Export mit den aktuellen Texten. ' + (cliTask.saved ? 'Dieser Stand ist gespeichert.' : 'Die Vorschau ist noch nicht in der Sammlung gespeichert.'));
    });
  });
  for (const name of ['scenario', 'text']) {
    el('cli-tab-' + name).addEventListener('click', () => cliOpenTab(name));
    el('cli-tab-' + name).addEventListener('keydown', event => {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
        event.preventDefault(); const next = name === 'scenario' ? 'text' : 'scenario';
        cliOpenTab(next); el('cli-tab-' + next).focus();
      }
    });
  }
  el('cli-back').addEventListener('click', () => cliOpenTab('scenario'));
  new MutationObserver(() => {
    const dark = document.documentElement.dataset.theme === 'dark';
    for (const editor of cliEditors.values()) {
      editor.o.theme = dark ? 'dark' : 'default';
      editor.container.classList.toggle('jodit_theme_dark', dark); editor.container.classList.toggle('jodit_theme_default', !dark);
    }
  }).observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
  if (currentPage === '/linux-aufgaben') loadCliExercises(true);
}
