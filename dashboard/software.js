'use strict';

let softwareState = null;
let softwareCart = new Set();
let softwareHosts = new Set();
let softwareProfileId = '';
let softwarePlanId = null;
let softwareActivePlan = null;
let softwareSearchPage = 1;
let softwareSearchVersion = 0;
let softwareSearchItems = [];
let softwareInventoryVersion = '';
let softwareSearchTimer = null;
let softwareLoadPromise = null;
let softwareReloadRequested = false;
let softwareFreeRunningPolls = 0;
let softwarePending = false;
let softwareAvailable = false;
let softwareLog = null;
let softwareLogVersion = 0;
let softwareOwnPage=1;
const softwareLabels = {running:'Läuft',succeeded:'Erfolgreich',failed:'Fehlgeschlagen',interrupted:'Unterbrochen'};
const softwareEqual = (a,b) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
function softwareMessage(text) { el('software-status').textContent = text; }
function softwareRenderExport() {
  const profile=softwareState?.profiles.find(row=>row.id===softwareProfileId);
  const link=el('software-export');link.hidden=!profile;
  if(profile) {
    link.href=basePath+'/api/software/export/'+profile.id;
    link.textContent=(softwareState.own_packages||[]).some(row=>profile.packages.includes(row.name))
      ?'Profil mit Paketdateien herunterladen (ZIP) →':'Playbook herunterladen →';
  }
}
function softwareInvalidate() { softwarePlanId = null; softwareActivePlan=null; el('software-preview').hidden = true; softwareButtons(); softwareRenderInstalled(); }
function softwarePlanFresh(run) {
  return !run?.finished || Date.now()-Date.parse(run.finished)<=15*60*1000;
}
function softwareButton(label, click, admin = false) {
  const button = document.createElement('button'); button.type = 'button'; button.className = 'pager-button'; button.textContent = label;
  if (admin) { button.dataset.adminOnly = ''; button.disabled = labRole !== 'admin'; }
  button.addEventListener('click',click); return button;
}
function softwareButtons() {
  // The shared operation lock is authoritative. A stale historical file may
  // still say running after a failed final disk write although no worker exists.
  const busy = softwarePending || !softwareAvailable || !softwareState || softwareState.busy;
  const selected = softwareState?.profiles.find(row => row.id === softwareProfileId);
  const approved = selected?.approved && softwareEqual(selected.packages,softwareCart);
  el('software-profile-baseline').disabled = labRole !== 'admin' || !approved || softwarePending;
  if (!approved) el('software-profile-baseline').checked = false;
  el('software-profile-hint').textContent = approved
    ? 'Pilot abgenommen. Dieses Profil kann der Baseline hinzugefügt werden.'
    : 'Optional nach erfolgreicher Pilotinstallation und Abnahme.';
  el('software-plan').disabled = labRole !== 'admin' || busy || !softwareCart.size || !softwareHosts.size;
  const plan = softwareActivePlan;
  const safe = plan && plan.kind === 'plan' && plan.status === 'succeeded' && !plan.consumed
    && softwarePlanFresh(plan)
    && softwareEqual(plan.packages,softwareCart) && softwareEqual(plan.targets,softwareHosts)
    && Object.values(plan.results).every(row => row.status === 'succeeded' && !row.removals?.length)
    && Object.values(plan.results).some(row => row.changes?.length);
  el('software-install').disabled = labRole !== 'admin' || busy || !safe;
  const reason=labRole!=='admin'?'Softwareinstallationen benötigen die Administratorrolle.'
    : !softwareAvailable?'APP-Status wird geladen.'
    : softwarePending?'Anfrage läuft …'
    : softwareState?.busy?'Ein Verwaltungsauftrag läuft noch.'
    : !plan?'Zuerst die Installation prüfen.'
    : !softwareEqual(plan.packages,softwareCart)||!softwareEqual(plan.targets,softwareHosts)?'Die Auswahl wurde geändert. Bitte erneut prüfen.'
    : plan.consumed?'Diese Vorschau wurde bereits verwendet. Bitte erneut prüfen.'
    : !softwarePlanFresh(plan)?'Die Vorschau ist abgelaufen. Bitte erneut prüfen.'
    : plan.status==='running'?'Paketstand und Abhängigkeiten werden noch geprüft …'
    : plan.status!=='succeeded'?'Prüfung fehlgeschlagen. Details stehen im Prüfergebnis.'
    : Object.values(plan.results).some(row=>row.removals?.length)?'Die Auswahl würde Pakete entfernen. Bitte anpassen.'
    : !Object.values(plan.results).some(row=>row.changes?.length)?'Die ausgewählte Software ist bereits installiert.'
    : 'Prüfung erfolgreich. Die Installation kann jetzt bestätigt werden.';
  el('software-install').title=reason;el('software-install-hint').textContent=reason;
  el('software-profile-save').disabled = labRole !== 'admin' || softwarePending || !softwareCart.size;
  el('software-profile-delete').disabled = labRole !== 'admin' || softwarePending || !selected;
  el('software-catalog-refresh').disabled = labRole !== 'admin' || Boolean(softwareState?.catalog.indexing);
  el('software-inventory').disabled = labRole !== 'admin' || busy || !softwareHosts.size || Boolean(softwareState?.inventory_refreshing);
  el('software-teacher-check').disabled = labRole !== 'admin' || busy;
  el('software-own-upload').disabled=labRole!=='admin'||busy;
  el('software-selection-count').textContent = softwareCart.size + (softwareCart.size === 1 ? ' Paket · ' : ' Pakete · ') + softwareHosts.size + ' Rechner ausgewählt';
}
function softwareRenderCart() {
  el('software-cart-empty').hidden = softwareCart.size > 0;
  el('software-cart').replaceChildren(...[...softwareCart].sort().map(name => {
    const row = document.createElement('li'); const label = document.createElement('span'); label.textContent = name;
    row.append(label,softwareButton('Entfernen',() => {softwareCart.delete(name);softwareInvalidate();softwareRenderCart();},true)); return row;
  }));
  for (const input of document.querySelectorAll('[data-software-add]')) {
    input.checked = softwareCart.has(input.dataset.softwareAdd);
    input.disabled = labRole !== 'admin' || (softwareCart.size >= 50 && !input.checked);
  }
  softwareButtons();
}
function softwareRenderOwn() {
  const query=el('software-own-search').value.trim().toLowerCase();
  const rows=(softwareState?.own_packages||[]).filter(row=>(row.name+' '+row.description).toLowerCase().includes(query));
  const pages=Math.max(1,Math.ceil(rows.length/10));softwareOwnPage=Math.min(softwareOwnPage,pages);
  el('software-own-results').replaceChildren(...rows.slice((softwareOwnPage-1)*10,softwareOwnPage*10).map(item=>{
    const article=document.createElement('article');article.className='software-package';
    const label=document.createElement('label');label.className='software-package-head';
    const input=document.createElement('input');input.type='checkbox';input.dataset.softwareAdd=item.name;
    input.checked=softwareCart.has(item.name);input.disabled=labRole!=='admin'||(softwareCart.size>=50&&!input.checked);
    input.addEventListener('change',()=>{input.checked?softwareCart.add(item.name):softwareCart.delete(item.name);softwareInvalidate();softwareRenderCart();});
    const title=document.createElement('strong');title.textContent=item.name;label.append(input,title);
    const description=document.createElement('p');description.textContent=item.description;
    const meta=document.createElement('small');meta.textContent=item.version+' · '+item.architecture+' · '+(item.size/1048576).toLocaleString('de-DE',{maximumFractionDigits:1})+' MiB · Eigenes Paket';
    const installed=document.createElement('div');installed.dataset.softwareInstalled=item.name;
    article.append(label,description,meta,installed);return article;
  }));
  el('software-own-status').textContent=rows.length?rows.length+' eigene Pakete':'Noch keine passenden eigenen Pakete vorhanden.';
  el('software-own-pagination').hidden=pages<=1;el('software-own-page').textContent='Seite '+softwareOwnPage+' von '+pages;
  el('software-own-prev').disabled=softwareOwnPage===1;el('software-own-next').disabled=softwareOwnPage===pages;
  softwareRenderInstalled();
}
async function softwareSearch() {
  const version = ++softwareSearchVersion;
  const query = el('software-search').value.trim();
  if (query.length < 2) {
    softwareSearchItems = []; el('software-results').replaceChildren();
    el('software-search-pagination').hidden = true;
    el('software-search-status').textContent = 'Suche nach einer Software oder ihrer Funktion (mindestens 2 Zeichen).';
    return;
  }
  try {
    const data = await request('/api/software/search/' + softwareSearchPage + '/' + encodeURIComponent(query));
    if (version !== softwareSearchVersion) return;
    softwareSearchItems = data.packages;
    const meta = data.pagination; softwareSearchPage = meta.page;
    el('software-search-status').textContent = meta.total ? meta.total.toLocaleString('de-DE') + (meta.total===1?' passendes Paket':' passende Pakete') : 'Keine passenden Pakete gefunden.';
    el('software-search-page').textContent = 'Seite ' + meta.page + ' von ' + meta.pages;
    el('software-search-prev').disabled = meta.page === 1;
    el('software-search-next').disabled = meta.page === meta.pages;
    el('software-search-pagination').hidden = meta.pages <= 1;
    el('software-results').replaceChildren(...data.packages.map(item => {
      const row = document.createElement('article'); row.className = 'software-package';
      const head = document.createElement('label'); head.className = 'software-package-head';
      const name = document.createElement('strong'); name.textContent = item.name;
      const add = document.createElement('input');add.type='checkbox';add.dataset.softwareAdd=item.name;
      add.checked=softwareCart.has(item.name);add.disabled=labRole!=='admin'||(softwareCart.size>=50&&!add.checked);
      add.addEventListener('change',()=>{add.checked?softwareCart.add(item.name):softwareCart.delete(item.name);softwareInvalidate();softwareRenderCart();});
      head.append(add,name);
      const description = document.createElement('p'); description.textContent = item.description.split('\n')[0] || 'Keine Kurzbeschreibung im Debian-Katalog vorhanden.';
      const detail = document.createElement('small'); detail.textContent = item.version + ' · ' + item.section + ' · ' + (item.bytes / 1048576).toLocaleString('de-DE',{maximumFractionDigits:1}) + ' MB';
      const installed=document.createElement('div');installed.dataset.softwareInstalled=item.name;
      row.append(head,description,detail,installed); return row;
    }));
    softwareRenderInstalled();
  } catch (error) { if (version === softwareSearchVersion) {
    softwareSearchItems=[];el('software-results').replaceChildren();el('software-search-pagination').hidden=true;
    el('software-search-status').textContent=error.message;
  } }
}
function softwareRenderInstalled() {
  const hosts=[...softwareHosts].sort();const inventory=softwareState?.inventory || {};
  const checked=hosts.filter(host=>inventory[host]?.checked_at&&!inventory[host]?.error);
  const checkedAt=checked.map(host=>inventory[host].checked_at).sort()[0];
  el('software-inventory-hint').textContent = !hosts.length
    ? 'Unten Rechner auswählen, um installierte Software zu prüfen. Die Suche selbst fragt keine Clients ab.'
    : softwareState?.inventory_refreshing ? 'Installationsstand wird einmalig von den gewählten Rechnern gelesen …'
    : checked.length ? 'Paketstand für '+checked.length+' von '+hosts.length+' gewählten Rechnern · '+new Date(checkedAt).toLocaleString('de-DE',{dateStyle:'short',timeStyle:'short'})+'. Zum Aktualisieren erneut einlesen.'
    : 'Installationsstand für '+hosts.join(', ')+': noch nicht gelesen oder nicht erreichbar.';
  for (const output of document.querySelectorAll('[data-software-installed]')) {
    const item=[...softwareSearchItems,...(softwareState?.own_packages||[])].find(row=>row.name===output.dataset.softwareInstalled);
    output.replaceChildren();output.hidden=!hosts.length;
    if (!item||!hosts.length) continue;
    const lines=hosts.map(host=>{
      const state=inventory[host];const known=state?.checked_at&&!state.error&&item.installed_at?.[host]===state.checked_at&&Object.hasOwn(item.installed||{},host);
      const version=known?item.installed[host]:null;
      const text=state?.refreshing?'Wird geprüft …':state?.error?'Nicht erreichbar':!known?'Noch nicht geprüft':version?'Installiert · '+version:'Nicht installiert';
      return {host,text,installed:known&&Boolean(version),known:Boolean(known)};
    });
    if (hosts.length===1) output.append(badge(lines[0].host+' · '+lines[0].text,lines[0].installed?'good':'neutral'));
    else {
      const details=document.createElement('details');const summary=document.createElement('summary');
      const unknown=lines.filter(row=>!row.known).length;
      summary.textContent=lines.filter(row=>row.installed).length+' von '+hosts.length+' installiert'+(unknown?' · '+unknown+' ungeprüft':'');
      details.append(summary);const list=document.createElement('ul');
      for (const row of lines) {const line=document.createElement('li');line.textContent=row.host+' · '+row.text;list.append(line);}
      details.append(list);output.append(details);
    }
  }
}
function softwareRenderTargets() {
  const ready = new Set(softwareState.hosts);
  softwareHosts = new Set([...softwareHosts].filter(host => ready.has(host)));
  const teams=new Map(Array.from({length:8},(_,index) => {
    const number = String(index+1).padStart(2,'0');
    const team = document.createElement('div'); team.className = 'room-team';
    const heading = document.createElement('h3'); const toggle = softwareButton(index === 7 ? 'Pilotrechner' : 'Team ' + number,() => {
      const hosts = ['A','B'].map(side => 'LAB-'+number+side).filter(host => ready.has(host));
      const selected = hosts.every(host => softwareHosts.has(host));
      for (const host of hosts) selected ? softwareHosts.delete(host) : softwareHosts.add(host);
      softwareInvalidate();softwareRenderTargets();
    },true); toggle.className = 'room-team-toggle'; heading.append(toggle);team.append(heading);
    for (const host of LabRoomLayout.hosts(number)) {
      const label = document.createElement('label'); const input = document.createElement('input'); input.type = 'checkbox';
      input.dataset.softwareHost = host; input.checked = softwareHosts.has(host);
      input.setAttribute('aria-label',host);
      input.disabled = labRole !== 'admin' || !ready.has(host) || softwareState.busy;
      input.addEventListener('change',() => {input.checked ? softwareHosts.add(host) : softwareHosts.delete(host);softwareInvalidate();});
      label.append(input);team.append(label);
    }
    return [number,team];
  }));
  const teacher=document.createElement('label');
  const input=document.createElement('input');input.type='checkbox';input.dataset.softwareHost='LAB-Teacher';
  input.setAttribute('aria-label','LAB-Teacher');input.checked=softwareHosts.has('LAB-Teacher');
  input.disabled=labRole!=='admin'||!ready.has('LAB-Teacher')||softwareState.busy;
  input.addEventListener('change',()=>{input.checked?softwareHosts.add('LAB-Teacher'):softwareHosts.delete('LAB-Teacher');softwareInvalidate();});
  const hint=document.createElement('small');hint.textContent=ready.has('LAB-Teacher')?'Softwareprofile bereit'
    :softwareState.teacher?.checked_at?'Softwarezugang aktualisieren':'Softwarezugang noch nicht eingerichtet';
  teacher.append(input,hint);
  LabRoomLayout.render(el('software-targets'),teams,teacher);
  el('software-teacher-setup').hidden=Boolean(softwareState.teacher?.ready);
  el('software-teacher-message').textContent=softwareState.teacher?.error||(softwareState.teacher?.checked_at
    ?'Den Teacher-Empfänger auf den aktuellen Stand bringen.':'Für den Teacher den vorhandenen APP-Empfänger einmalig um Softwareprofile erweitern.');
  softwareButtons();softwareRenderInstalled();
}
function softwareRenderPreview(run) {
  el('software-preview').hidden = !run;
  if (!run) return;
  el('software-preview-status').textContent = run.status === 'running' ? 'Paketstand und Abhängigkeiten werden geprüft · ' + Object.keys(run.results).length + ' / ' + run.targets.length + ' abgeschlossen · ' + (run.active || []).length + ' aktiv'
    : run.status === 'succeeded' ? Object.values(run.results).every(row=>!row.changes?.length)
      ? 'Die ausgewählte Software ist bereits installiert. Keine Installation nötig.'
      : 'Prüfung abgeschlossen. Installation ausdrücklich bestätigen.' : 'Prüfung fehlgeschlagen. Angaben unten prüfen.';
  el('software-preview-results').replaceChildren(...run.targets.map(host => {
    const result = run.results[host];
    const details = document.createElement('details');details.className = 'software-preview-host';
    const summary = document.createElement('summary');
    const changed = result?.changes || [];
    const dependencies=changed.filter(row=>!run.packages.includes(row.name)).length;
    summary.textContent = host + ' · ' + (result?.error || (result ? changed.length ? changed.length + ' Paketänderungen (davon '+dependencies+(dependencies===1?' Abhängigkeit':' Abhängigkeiten')+') · ' + (result.download_bytes/1048576).toLocaleString('de-DE',{maximumFractionDigits:1}) + ' MB Download' : 'Ausgewählte Software bereits installiert' : (run.active || []).includes(host) ? 'Wird geprüft …' : 'Wartet …'));
    details.open=run.targets.length===1;
    details.append(summary);
    if (result?.removals?.length) {const warning = document.createElement('p');warning.className='notice';warning.textContent='Installation gesperrt: Würde Pakete entfernen: '+result.removals.join(', ');details.append(warning);}
    const list = document.createElement('ul');
    for (const item of changed) {const row=document.createElement('li');row.textContent=(run.packages.includes(item.name)?'Ausgewählt · ':'Abhängigkeit · ')+(item.action === 'install' ? 'Neu: ' : item.action === 'remove' ? 'Entfernen: ' : 'Aktualisieren: ')+item.name+' · '+(item.version || item.old);list.append(row);}
    if (result?.already?.length) {const row=document.createElement('li');row.textContent='Bereits installiert: '+result.already.map(item=>item.name+' ('+item.version+')').join(', ');list.append(row);}
    details.append(list);return details;
  }));
}
async function softwareOpenLog(identity,host,refresh=false) {
  softwareLog = {identity,host}; const version = ++softwareLogVersion;
  el('software-log-panel').hidden = false; el('software-log-title').textContent = host + ' · Softwareinstallation';
  if (!refresh) el('software-log').textContent = 'Protokoll wird geladen …';
  try {const data = await request('/api/software/log/'+identity+'/'+host);if (version===softwareLogVersion && softwareLog) el('software-log').textContent=data.output;}
  catch(error) {if (version===softwareLogVersion && softwareLog) el('software-log').textContent=error.message;}
}
function softwareRenderRun(run) {
  const details = document.createElement('details'); const summary = document.createElement('summary');
  const title = document.createElement('strong');title.textContent = run.kind === 'plan' ? 'Installation prüfen' : 'Software installieren';
  const time = document.createElement('time'); time.dateTime = run.started; time.textContent = new Date(run.started).toLocaleString('de-DE',{timeZone:'Europe/Berlin',dateStyle:'short',timeStyle:'short'});
  summary.append(title,badge(softwareLabels[run.status]||run.status,['failed','interrupted'].includes(run.status)?'warn':'neutral'),time);
  details.append(summary);
  if (run.status === 'running') {
    const progress = document.createElement('p'); progress.className = 'field-hint';
    progress.textContent = Object.keys(run.results).length + ' / ' + run.targets.length + ' abgeschlossen · ' + (run.active || []).length + ' aktiv · bis zu ' + (softwareState.parallelism || 16) + ' gleichzeitig';
    details.append(progress);
  }
  const packages = document.createElement('p');packages.className='field-hint';packages.textContent=run.packages.join(', ');details.append(packages);
  for (const host of run.targets) {
    const row = document.createElement('p');row.className='field-hint';row.textContent=host+' · '+(run.results[host]?.error || softwareLabels[run.results[host]?.status] || ((run.active || []).includes(host) ? run.kind === 'plan' ? 'Wird geprüft …' : 'Wird installiert …' : 'Wartet …'));
    if (run.kind==='install') row.append(' ',softwareButton('Protokoll',()=>softwareOpenLog(run.id,host)));
    details.append(row);
  }
  const alreadyPresent=run.kind==='plan'&&Object.keys(run.results).length===run.targets.length
    &&Object.values(run.results).every(row=>row.status==='succeeded'&&!row.changes?.length);
  if ((run.kind==='install'||alreadyPresent) && run.status==='succeeded' && run.profile_id && run.targets.some(host=>['LAB-08A','LAB-08B'].includes(host))) {
    const profile = softwareState.profiles.find(row=>row.id===run.profile_id);
    const approved = profile?.approved && softwareEqual(profile.packages,run.packages);
    if (approved) details.append(badge('Pilot abgenommen','good'));
    else details.append(softwareButton('Pilot abnehmen',()=>confirmCatalog(
      'Die Software aus diesem Profil wurde auf dem Pilotrechner geöffnet und vor Ort erfolgreich getestet?',
      async()=>{try {await request('/api/software/review',{run_id:run.id,confirmed:true});await loadSoftware(true);softwareMessage('Pilot abgenommen. Das Profil kann jetzt in die Baseline aufgenommen werden.');}
        catch(error){softwareMessage(error.message);}},'Praxistest bestätigen','Softwareprofil abnehmen'),true));
  }
  return details;
}
function loadSoftware(refresh = false) {
  cancelPoll('software');
  if (softwareLoadPromise) {
    if (refresh === true) {
      softwareReloadRequested = true;
      // Preserve a newly selected history page against the older response.
      historyView('software-runs').version++;
    }
    return softwareLoadPromise;
  }
  softwareLoadPromise = (async () => {
    do {
      softwareReloadRequested = false;
      await loadSoftwareSnapshot();
    } while (softwareReloadRequested);
  })().finally(() => {
    softwareLoadPromise = null; softwareButtons();
  });
  return softwareLoadPromise;
}
async function loadSoftwareSnapshot() {
  try {
    const data = await requestHistory('/api/software','software-runs',() => loadSoftware(true));
    // A completed mutation needs a response requested after that mutation.
    // Ignore the older snapshot instead of dropping the requested refresh.
    if (!data || softwareReloadRequested) return;
    const oldCount = softwareState?.catalog.count || 0;
    const inventoryVersion=JSON.stringify(data.inventory||{});
    const inventoryChanged=inventoryVersion!==softwareInventoryVersion;softwareInventoryVersion=inventoryVersion;
    const recovered = softwareState && !softwareAvailable;
    softwareState = data;
    softwareRenderOwn();
    softwareAvailable = true;
    el('software-activity').textContent = data.busy ? 'Auftrag läuft' : data.catalog.indexing ? 'Katalog wird eingelesen' : 'Bereit';
    el('software-activity').dataset.state = data.busy || data.catalog.indexing ? 'busy' : 'ready';
    el('software-catalog-count').textContent = data.catalog.count.toLocaleString('de-DE') + ' Pakete';
    el('software-catalog-date').textContent = data.catalog.release + ' · ' + data.catalog.architecture
      + (data.catalog.lists_at ? ' · Paketlisten vom '+new Date(data.catalog.lists_at).toLocaleDateString('de-DE') : '')
      + (data.catalog.source_host ? ' · '+data.catalog.source_host : '');
    if (data.catalog.error) softwareMessage(data.catalog.error);
    else if (recovered) softwareMessage('Status wieder verfügbar. Das Prüfergebnis kann jetzt bestätigt werden.');
    const select = el('software-profile'); select.replaceChildren(new Option('Neue Auswahl',''),...data.profiles.map(row=>new Option(row.name+(row.baseline?' · Baseline':''),row.id)));
    select.value = softwareProfileId;
    if (softwareProfileId && !data.profiles.some(row=>row.id===softwareProfileId)) {softwareProfileId='';select.value='';}
    softwareRenderExport();
    if (softwarePlanId) {
      const identity=softwarePlanId;
      const plan=data.runs.find(row=>row.id===identity)||await request('/api/software/run/'+identity);
      if (softwarePlanId===identity) softwareActivePlan=plan;
    }
    softwareRenderTargets();softwareRenderPreview(softwareActivePlan);
    el('software-empty').hidden = data.pagination.total > 0;
    renderHistory('software-runs',data,softwareRenderRun);
    if (softwareLog) await softwareOpenLog(softwareLog.identity,softwareLog.host,true);
    if ((data.catalog.count && !oldCount)||inventoryChanged) await softwareSearch();
    const unconfirmedRunning = !data.busy && data.runs.some(row=>row.status==='running');
    if (!unconfirmedRunning) softwareFreeRunningPolls = 0;
    if (data.busy || data.catalog.indexing || data.inventory_refreshing
        || (unconfirmedRunning && softwareFreeRunningPolls++ < 1)) schedulePoll('software',loadSoftware,2500);
  } catch(error) {softwareAvailable=false;softwareMessage(error.message);el('software-activity').textContent='Status nicht verfügbar';schedulePoll('software',loadSoftware,6000,true);}
  finally {softwareButtons();}
}
el('software-search').addEventListener('input',()=>{clearTimeout(softwareSearchTimer);softwareSearchVersion++;softwareSearchPage=1;if(el('software-search').value.trim().length<2)softwareSearch();else softwareSearchTimer=setTimeout(softwareSearch,300);});
el('software-search-prev').addEventListener('click',()=>{softwareSearchPage--;softwareSearch();});
el('software-search-next').addEventListener('click',()=>{softwareSearchPage++;softwareSearch();});
el('software-refresh').addEventListener('click',() => loadSoftware(true));
el('software-catalog-refresh').addEventListener('click',async()=>{try{await request('/api/software/catalog',{});await loadSoftware(true);}catch(error){softwareMessage(error.message);}});
el('software-inventory').addEventListener('click',async()=>{
  softwarePending=true;softwareButtons();
  try {await request('/api/software/inventory',{targets:[...softwareHosts].sort()});await loadSoftware(true);}
  catch(error){softwareMessage(error.message);}finally{softwarePending=false;softwareButtons();}
});
el('software-profile').addEventListener('change',()=>{
  softwareProfileId=el('software-profile').value;
  const profile=softwareState.profiles.find(row=>row.id===softwareProfileId);
  if (profile) {softwareCart=new Set(profile.packages);el('software-profile-name').value=profile.name;el('software-profile-baseline').checked=profile.baseline;}
  else {el('software-profile-name').value='';el('software-profile-baseline').checked=false;}
  softwareInvalidate();softwareRenderCart();softwareRenderExport();
});
el('software-profile-new').addEventListener('click',()=>{
  softwareProfileId='';softwareCart.clear();el('software-profile').value='';el('software-profile-name').value='';
  el('software-export').hidden=true;softwareInvalidate();softwareRenderCart();
});
el('software-profile-save').addEventListener('click',async()=>{
  softwarePending=true;softwareButtons();
  try {const profile=await request('/api/software/profile',{id:softwareProfileId,name:el('software-profile-name').value,
    packages:[...softwareCart].sort(),baseline:el('software-profile-baseline').checked});
    softwareProfileId=profile.id;await loadSoftware(true);softwareMessage('Softwareprofil gespeichert.');}
  catch(error){softwareMessage(error.message);}finally{softwarePending=false;softwareButtons();}
});
el('software-profile-delete').addEventListener('click',()=>confirmCatalog(
  'Dieses Softwareprofil löschen? Die installierte Software auf den Clients bleibt erhalten.',
  async()=>{try{await request('/api/software/delete',{id:softwareProfileId,confirmed:true});softwareProfileId='';el('software-profile-name').value='';await loadSoftware(true);softwareMessage('Profil gelöscht.');}
    catch(error){softwareMessage(error.message);}},'Profil löschen','Softwareprofil entfernen'));
el('software-target-all').addEventListener('click',()=>{softwareHosts=new Set(softwareState.hosts.filter(host=>host!=='LAB-Teacher'));softwareInvalidate();softwareRenderTargets();});
el('software-target-none').addEventListener('click',()=>{softwareHosts.clear();softwareInvalidate();softwareRenderTargets();});
el('software-own-search').addEventListener('input',()=>{softwareOwnPage=1;softwareRenderOwn();});
el('software-own-prev').addEventListener('click',()=>{softwareOwnPage--;softwareRenderOwn();});
el('software-own-next').addEventListener('click',()=>{softwareOwnPage++;softwareRenderOwn();});
el('software-own-upload').addEventListener('change',async event=>{
  const files=[...event.target.files];if(!files.length)return;
  softwarePending=true;softwareButtons();
  try {
    if(files.length>50)throw new Error('Höchstens 50 Pakete gleichzeitig auswählen.');
    for(const file of files) {
      if(!file.name.toLowerCase().endsWith('.deb')||!file.size||file.size>512*1048576)throw new Error('Bitte .deb-Dateien bis 512 MiB auswählen.');
      softwareMessage(file.name+' wird hochgeladen …');
      const response=await fetch(basePath+'/api/software/package/upload',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-LAB-Package-Name':encodeURIComponent(file.name),'X-CSRF-Token':csrfToken},body:file});
      const data=await response.json();if(!response.ok)throw new Error(data.error||'Paketupload fehlgeschlagen.');
    }
    softwareOwnPage=1;el('software-own-search').value='';softwareInvalidate();await loadSoftware(true);softwareMessage('Eigene Pakete aufgenommen. Zum Installieren anhaken oder in einem Profil speichern.');
  } catch(error){softwareMessage(error.message);await loadSoftware(true);}
  finally{event.target.value='';softwarePending=false;softwareButtons();}
});
el('software-teacher-check').addEventListener('click',async()=>{
  softwarePending=true;softwareButtons();el('software-teacher-message').textContent='Teacher-Zugang wird geprüft …';
  try {const state=await request('/api/software/teacher/check',{});await loadSoftware(true);
    softwareMessage(state.ready?'Teacher ist für Softwareprofile bereit. Unten separat auswählen.':state.error);}
  catch(error){softwareMessage(error.message);}finally{softwarePending=false;softwareButtons();}
});
el('software-plan').addEventListener('click',async()=>{
  softwarePending=true;softwareButtons();softwareMessage('Installation wird geprüft …');
  try {const profile=softwareState.profiles.find(row=>row.id===softwareProfileId);
    const run=await request('/api/software/plan',{packages:[...softwareCart].sort(),targets:[...softwareHosts].sort(),
      profile_id:profile && softwareEqual(profile.packages,softwareCart)?profile.id:''});
    softwarePlanId=run.id;softwareActivePlan=run;resetHistory('software-runs');await loadSoftware(true);softwareMessage('Prüfung gestartet. Es wird noch keine Software installiert.');}
  catch(error){softwareMessage(error.message);}finally{softwarePending=false;softwareButtons();}
});
el('software-install').addEventListener('click',()=>{
  const identity=softwarePlanId;
  confirmCatalog([...softwareCart].sort().join(', ')+' installieren auf '+[...softwareHosts].sort().join(', ')+'?',
    async()=>{
      if (identity!==softwarePlanId) {softwareMessage('Die Auswahl hat sich geändert. Bitte erneut prüfen.');return;}
      softwarePending=true;softwareButtons();
      try {await request('/api/software/start',{run_id:identity,confirmed:true});softwarePlanId=null;softwareActivePlan=null;resetHistory('software-runs');await loadSoftware(true);softwareMessage('Installation gestartet. Fortschritt steht im Verlauf.');}
      catch(error){softwareMessage(error.message);}finally{softwarePending=false;softwareButtons();}
    },'Jetzt installieren','Softwareinstallation bestätigen');
});
el('software-log-close').addEventListener('click',()=>{softwareLog=null;softwareLogVersion++;el('software-log-panel').hidden=true;el('software-log').textContent='';});
if (currentPage==='/repo-software') {loadSoftware();softwareSearch();}
