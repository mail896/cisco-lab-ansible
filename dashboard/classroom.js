'use strict';

let classroomData=null;
let classroomLoading=false;
let classroomPaused=false;
const classroomStates={up:'Link aktiv',down:'Kein Link',missing:'USB-Adapter fehlt',unmanaged:'Noch nicht aufgenommen',
  unreachable:'Nicht erreichbar',error:'Prüfung fehlgeschlagen',pending:'Noch nicht geprüft'};
const classroomPaths={
  usb:['M12 21V3m-3 3 3-3 3 3M12 15l-5-4V8m5 10 6-4v-3','M6 5h2v3H6zM17 8h2v3h-2z'],
  route:['M4 19h5V5h11M9 12h11m-3-3 3 3-3 3M17 2l3 3-3 3'],
  address:['M8 8 4 12l4 4m8-8 4 4-4 4m-3-10-2 16']
};
function classroomIcon(kind) {
  const ns='http://www.w3.org/2000/svg';const svg=document.createElementNS(ns,'svg');
  for (const [key,value] of Object.entries({viewBox:'0 0 24 24',width:'20',height:'20',fill:'none',stroke:'currentColor','stroke-width':'1.7','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'}))svg.setAttribute(key,value);
  for (const d of classroomPaths[kind]) {const path=document.createElementNS(ns,'path');path.setAttribute('d',d);svg.append(path);}
  return svg;
}
function classroomNode(tag,text,className='') {
  const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;
}
function classroomAddresses(title,rows) {
  const section=classroomNode('div',undefined,'classroom-addresses');
  const label=classroomNode('h4',title);label.prepend(classroomIcon('address'));section.append(label);
  if (!rows.length) section.append(classroomNode('p','Keine Adresse','classroom-empty'));
  else for (const row of rows) {
    const line=classroomNode('p');line.append(classroomNode('code',row.address));
    const flags=(row.flags||[]).map(flag=>({tentative:'Vorläufig',dadfailed:'Adresskonflikt',deprecated:'Veraltet'}[flag]||flag));
    if (row.scope==='link')flags.unshift('Link-lokal');
    if (flags.length)line.append(classroomNode('small',flags.join(' · '),row.flags?.includes('dadfailed')?'classroom-warning':''));
    section.append(line);
  }
  return section;
}
function classroomRoute(row) {
  const line=classroomNode('li');line.append(classroomNode('code',row.destination+' → '+(row.gateway||'direkt')));
  const meta=[];
  if (!['main',254].includes(row.table))meta.push('Tabelle '+row.table);
  if (row.type&&row.type!=='unicast')meta.push(row.type);
  if (row.source)meta.push('Quelle '+row.source);
  if (row.metric!==null&&row.metric!==undefined)meta.push('Metrik '+row.metric);
  if (row.protocol==='kernel')meta.push('automatisch');
  if (meta.length)line.append(classroomNode('small',meta.join(' · ')));
  return line;
}
function classroomRoutes(title,rows,host,open) {
  const section=classroomNode('div',undefined,'classroom-routes');
  const normal=rows.filter(row=>!['local',255].includes(row.table));const local=rows.filter(row=>['local',255].includes(row.table));
  const heading=classroomNode('h4',title);heading.prepend(classroomIcon('route'));section.append(heading);
  if (!normal.length)section.append(classroomNode('p','Keine Route','classroom-empty'));
  else {const list=classroomNode('ul');list.append(...normal.map(classroomRoute));section.append(list);}
  if (local.length) {
    const details=classroomNode('details');details.dataset.classroomFold=host+'-'+title;details.open=open.has(details.dataset.classroomFold);
    details.append(classroomNode('summary','Lokale Systemrouten ('+local.length+')'));
    const list=classroomNode('ul');list.append(...local.map(classroomRoute));details.append(list);section.append(details);
  }
  return section;
}
function classroomBrief(row) {
  const brief=classroomNode('dl',undefined,'classroom-brief');
  for(const [family,addresses] of [['IPv4',row.ipv4],['IPv6',row.ipv6]]) {
    const primary=addresses.find(address=>address.scope==='global')||addresses[0];
    const label=classroomNode('dt',family+(primary?.scope==='link'?' · LL':''));
    const value=classroomNode('dd');const address=classroomNode('code',primary?.address||'—');
    address.title=addresses.map(item=>item.address).join('\n')||'Keine Adresse';value.append(address);
    if(addresses.length>1)value.append(classroomNode('span','+'+(addresses.length-1),'classroom-more'));
    const warning=primary?.flags?.includes('dadfailed')?'Konflikt':primary?.flags?.includes('tentative')?'Vorläufig':'';
    if(warning)value.append(classroomNode('span',warning,'classroom-warning'));
    brief.append(label,value);
  }
  const normal=rows=>rows.filter(route=>!['local',255].includes(route.table));
  const routes4=normal(row.routes4),routes6=normal(row.routes6);
  const routes=classroomNode('dd','IPv4 '+routes4.length+' · IPv6 '+routes6.length);
  routes.title=[['IPv4',routes4],['IPv6',routes6]].map(([family,rows])=>family+': '+(rows.map(route=>route.destination+' → '+(route.gateway||'direkt')).join('; ')||'Keine Route')).join('\n');
  brief.append(classroomNode('dt','Routen'),routes);return brief;
}
function classroomCard(host,row,open) {
  const card=classroomNode('article',undefined,'classroom-client');card.dataset.classroomHost=host;card.dataset.state=row.state;
  card.setAttribute('aria-label',host);
  const heading=classroomNode('div',undefined,'classroom-client-heading');
  card.append(heading);
  const label=row.state==='down'&&row.admin_up===false?'Adapter deaktiviert':classroomStates[row.state]||'Unbekannt';
  const status=classroomNode('p',undefined,'classroom-link');
  status.append(classroomIcon('usb'),classroomNode('strong',label));status.setAttribute('aria-label','USB-Laboradapter: '+label);heading.append(status);
  const details=classroomNode('details',undefined,'classroom-details');
  details.dataset.classroomFold=host+'-details';details.open=open.has(details.dataset.classroomFold);
  details.append(classroomNode('summary','Details'));
  const body=classroomNode('div',undefined,'classroom-detail-body');
  if (row.hardware) {
    const hw=row.hardware;const model=hw.model&&hw.manufacturer&&!hw.model.toLowerCase().includes(hw.manufacturer.toLowerCase())
      ? hw.manufacturer+' · '+hw.model : hw.model||hw.manufacturer||'USB-Ethernetadapter';
    const summary=details.querySelector('summary');summary.textContent='Details · '+(hw.model||hw.manufacturer||'USB-Adapter');summary.title=model;
    body.append(classroomNode('p',model,'classroom-model'),classroomNode('p',[hw.usb_id,hw.driver,row.interface].filter(Boolean).join(' · '),'classroom-hardware-id'));
  }
  if (['up','down'].includes(row.state)) {
    card.append(classroomBrief(row));
    body.append(classroomAddresses('IPv4',row.ipv4),classroomAddresses('IPv6',row.ipv6),
      classroomRoutes('Routen IPv4',row.routes4,host,open),classroomRoutes('Routen IPv6',row.routes6,host,open));
  } else {
    const explanation=row.error||(row.state==='unmanaged'?'Nach der APP-Aufnahme erscheint hier der Live-Status.'
      :row.state==='missing'?'Der zugeordnete USB-Laboradapter ist nicht angeschlossen oder wurde ersetzt.':'Netzwerkstatus wird gelesen …');
    body.append(classroomNode('p',explanation,'classroom-empty'));
  }
  const checked=row.observed_at||row.checked_at;
  if (checked)body.append(classroomNode('small','Geprüft '+new Date(checked).toLocaleTimeString('de-DE',{timeZone:'Europe/Berlin'}),'classroom-checked'));
  details.append(body);card.append(details);
  return card;
}
function classroomRender() {
  if(!classroomData)return;
  const data=classroomData;const rows=Object.values(data.clients);const known=Boolean(data.checked_at);
  for (const [id,value] of Object.entries({link:rows.filter(row=>row.state==='up').length,
    ipv4:rows.filter(row=>row.ipv4?.length).length,
    ipv6:rows.filter(row=>row.ipv6?.some(address=>address.scope==='global')).length,
    offline:rows.filter(row=>row.state==='unreachable').length}))el('classroom-count-'+id).textContent=known?value:'—';
  el('classroom-status').textContent=classroomPaused?'Pausiert':data.error?'Status fehlt':data.refreshing?'Wird geprüft':'Live';
  el('classroom-status').dataset.state=classroomPaused?'loading':data.error?'error':data.refreshing?'busy':'ready';
  el('classroom-notice').textContent=data.error||'';
  const unmanaged=rows.filter(row=>row.state==='unmanaged').length;
  el('classroom-stamp').textContent=(data.checked_at?'Stand '+new Date(data.checked_at).toLocaleTimeString('de-DE',{timeZone:'Europe/Berlin'})+' · ':'')
    +(classroomPaused?'Live-Aktualisierung pausiert.':'USB-/Anmeldestatus etwa alle '+(classroomData.interval_seconds || 60)+' Sekunden bei sichtbarer Seite.')+(unmanaged?' · '+unmanaged+' noch nicht aufgenommen.':'');
  const open=new Set([...document.querySelectorAll('[data-classroom-fold][open]')].map(node=>node.dataset.classroomFold));
  const query=el('classroom-search').value.trim().toUpperCase();let count=0;
  const teams=new Map();
  for (let number=1;number<=8;number++) {
    const teamNumber=String(number).padStart(2,'0');
    const hosts=LabRoomLayout.hosts(teamNumber).filter(host=>host.includes(query));
    if(!hosts.length)continue;count+=hosts.length;
    const team=classroomNode('section',undefined,'classroom-team');team.append(classroomNode('h2',number===8?'Pilotrechner':'Team '+String(number).padStart(2,'0')));
    const clients=classroomNode('div',undefined,'classroom-team-clients');
    clients.append(...hosts.map(host=>classroomCard(host,data.clients[host]||{state:'pending'},open)));team.append(clients);teams.set(teamNumber,team);
  }
  const occupants=new Map(Object.entries(data.clients).map(([host,row])=>[host,row.users]));
  LabRoomLayout.render(el('classroom-clients'),teams,null,occupants);el('classroom-empty').hidden=count>0;
}
async function loadClassroom() {
  cancelPoll('classroom');
  if (classroomLoading||classroomPaused||document.hidden||authenticationExpired||currentPage!=='/unterricht')return;
  classroomLoading=true;
  try {
    classroomData=await request('/api/classroom');classroomRender();
    const delay=classroomData.refreshing?2000:Math.max(1000,(classroomData.interval_seconds-(classroomData.age_seconds||0))*1000+200);
    if(!classroomPaused)schedulePoll('classroom',loadClassroom,delay);
  } catch(error) {
    el('classroom-notice').textContent=error.message+' Der angezeigte Stand wird derzeit nicht aktualisiert.';
    el('classroom-status').textContent='Status nicht verfügbar';el('classroom-status').dataset.state='error';
    if(!classroomPaused)schedulePoll('classroom',loadClassroom,6000,true);
  } finally {classroomLoading=false;}
}
el('classroom-search').addEventListener('input',classroomRender);
el('classroom-pause').addEventListener('click',()=>{
  classroomPaused=!classroomPaused;el('classroom-pause').textContent=classroomPaused?'Live fortsetzen':'Live pausieren';
  pausePresence(classroomPaused);
  el('classroom-pause').setAttribute('aria-pressed',String(classroomPaused));
  if(classroomPaused){cancelPoll('classroom');classroomRender();}else loadClassroom();
});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&currentPage==='/unterricht')loadClassroom();});
window.addEventListener('pageshow',event=>{if(event.persisted&&currentPage==='/unterricht')loadClassroom();});
if(currentPage==='/unterricht')loadClassroom();
