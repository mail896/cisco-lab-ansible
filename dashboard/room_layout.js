/* Opposing teams share a table; each row follows the seated A/B perspective. */
'use strict';

const LabRoomLayout = (() => {
  const islands = [
    {id:'upper-left',label:'Eingang links',teams:['06','07']},
    {id:'upper-right',label:'Eingang rechts',teams:['03','02']},
    {id:'lower-left',label:'Fenster links',teams:['01','08']},
    {id:'lower-right',label:'Fenster rechts',teams:['05','04']}
  ];
  const north = new Set(islands.map(island=>island.teams[0]));
  const hosts = team => (north.has(team)?['B','A']:['A','B']).map(side=>'LAB-'+team+side);
  let presence=null;
  let presenceUnavailable=false;
  let presencePaused=false;
  function node(tag,className,text) {
    const element=document.createElement(tag);element.className=className;
    if(text!==undefined)element.textContent=text;return element;
  }
  function paintMonitor(mark) {
    const host=mark.dataset.presenceHost;
    const row=presence?.devices?.[host];
    const state=['online','unreachable'].includes(row?.state)?row.state:'unknown';
    const caption={online:'AN',unreachable:'AUS',unknown:'?'}[state];
    const description=state==='online'?'Erreichbar'+(row.rtt_ms!=null?' · Ping '+row.rtt_ms+' ms':'')
      :state==='unreachable'?'Keine Antwort auf Ping':'Erreichbarkeit nicht bekannt';
    mark.dataset.state=state;mark.title=host+' · '+description;
    mark.setAttribute('aria-label',host+': '+caption+' · '+description);
    mark.querySelector('.lab-monitor-status').textContent=state==='unknown'?'?':'';
  }
  function paintStamp(stamp) {
    stamp.textContent=presencePaused?'Erreichbarkeit: Aktualisierung pausiert.'
      :presenceUnavailable?'Erreichbarkeit derzeit nicht verfügbar.'
      :presence?.checked_at?'Geprüft '+new Intl.DateTimeFormat('de-DE',{timeStyle:'medium',timeZone:'Europe/Berlin'}).format(new Date(presence.checked_at))+' · alle 30 s bei sichtbarer Seite'
      :'Erreichbarkeit wird geprüft …';
  }
  function updatePresence(data,unavailable=false) {
    presence=data;presenceUnavailable=unavailable;
    document.querySelectorAll('.lab-room-monitor[data-presence-host]').forEach(paintMonitor);
    document.querySelectorAll('.lab-room-presence-stamp').forEach(paintStamp);
  }
  function pausePresence(paused) {
    presencePaused=paused;document.querySelectorAll('.lab-room-presence-stamp').forEach(paintStamp);
  }
  function monitor(host,side) {
    const mark=node('span','lab-room-monitor');mark.dataset.presenceHost=host;mark.setAttribute('role','img');
    const ns='http://www.w3.org/2000/svg';const svg=document.createElementNS(ns,'svg');
    svg.setAttribute('viewBox','0 0 104 66');svg.setAttribute('width','104');svg.setAttribute('height','66');
    svg.setAttribute('focusable','false');svg.setAttribute('aria-hidden','true');
    for(const [tag,attributes] of [
      ['path',{d:'M49 56h6v2h11v2H38v-2h11Z',class:'lab-monitor-frame'}],
      ['rect',{x:6,y:2,width:92,height:55,rx:3,class:'lab-monitor-frame'}],
      ['rect',{x:10,y:6,width:84,height:47,rx:1,class:'lab-monitor-screen'}],
      ['circle',{cx:91,cy:55,r:.8,class:'lab-monitor-power'}]
    ]) {
      const part=document.createElementNS(ns,tag);
      for(const [name,value] of Object.entries(attributes))part.setAttribute(name,value);
      svg.append(part);
    }
    const display=document.createElementNS(ns,'g');
    // The north monitor faces the opposite seat; its lettering stays upright.
    if(side==='north')display.setAttribute('transform','rotate(180 52 29.5)');
    for(const [className,y,text] of [['lab-monitor-name',30,host.replace(/^LAB-/, '')],['lab-monitor-status',45,'?']]) {
      const line=document.createElementNS(ns,'text');line.setAttribute('x','52');line.setAttribute('y',y);
      line.setAttribute('class',className);line.textContent=text;display.append(line);
    }
    if(host==='LAB-Teacher')display.classList.add('lab-monitor-teacher');
    svg.append(display);mark.append(svg);paintMonitor(mark);return mark;
  }
  function workstation(station,team,side) {
    const input=station.querySelector('input');
    const host=station.dataset.roomHost||station.dataset.classroomHost||input?.dataset.softwareHost||input?.value;
    station.dataset.roomSeat=String(hosts(team).indexOf(host)+1);
    station.classList.add('lab-room-station');
    const content=node(station.matches('.classroom-client')?'div':'span','lab-room-content');
    content.append(...station.childNodes);
    station.replaceChildren(...(side==='north'?[content,monitor(host,side)]:[monitor(host,side),content]));
    return station;
  }
  function tableSide(bank,team,side,occupants) {
    const heading=bank.querySelector(':scope > h2, :scope > h3');
    const stations=[...bank.querySelectorAll(':scope > .device-card, :scope > label, :scope > .classroom-team-clients > [data-classroom-host]')];
    const seats=node('div','lab-room-seats');
    seats.append(...stations.map(station=>workstation(station,team,side)));
    bank.dataset.roomBank=team;bank.dataset.roomSide=side;bank.classList.add('lab-room-bank');
    heading.classList.add('lab-room-team-title');
    bank.replaceChildren(heading,seats);
    if(occupants) {
      const people=node('div','lab-room-occupants');
      for(const station of stations) {
        const host=station.querySelector('.lab-room-monitor').dataset.presenceHost;
        const users=occupants.get(host);
        const person=node('span','lab-room-occupant',users?.map(user=>user.account).join(' · ')||'');
        person.dataset.roomSeat=station.dataset.roomSeat;person.dataset.occupantHost=host;
        person.title=host+' · '+(users?.length?users.map(user=>user.account
          +(user.kind==='local'?' (lokales Konto)':user.kind==='iserv'?' (IServ)':'')
          +(user.locked?' · gesperrt':'')).join('\n'):users?'Niemand grafisch angemeldet':'Anmeldung derzeit nicht bekannt');
        person.setAttribute('aria-label',person.title);people.append(person);
      }
      bank.append(people);
    }
    return bank;
  }
  function render(container,teams,teacher=null,occupants=null) {
    container.classList.add('lab-room-layout');
    container.classList.toggle('lab-room-with-occupants',Boolean(occupants));
    const key=node('div','lab-room-key');
    key.append(node('span','','Raumansicht · TV / Tafel links'),node('span','','A links · B rechts – vom Sitzplatz aus'));
    const grid=node('div','lab-room-islands');
    for(const island of islands) {
      if(!island.teams.some(team=>teams.has(team)))continue;
      const table=node('section','lab-room-island');table.dataset.roomIsland=island.id;
      table.setAttribute('aria-label','Tischinsel '+island.label.toLowerCase()+' · Teams '+island.teams.join(' / '));
      table.append(node('p','lab-room-island-label',island.label));
      const banks=node('div','lab-room-banks');
      for(const [index,team] of island.teams.entries()) {
        const bank=teams.get(team);
        if(!bank)continue;
        banks.append(tableSide(bank,team,index===0?'north':'south',occupants));
      }
      table.append(banks);grid.append(table);
    }
    if(teacher) {
      const beside=node('div','lab-room-teacher');
      const caption=node('p','lab-room-island-label','Teacher · neben den Pilotrechnern');
      teacher.classList.add('lab-room-teacher-control');
      teacher.prepend(monitor('LAB-Teacher','south'));
      beside.append(caption,teacher);grid.append(beside);
    }
    const legend=node('div','lab-room-legend');
    legend.append(node('span','','Grün · erreichbar'),node('span','','Dunkel · keine Pingantwort'),node('span','','? · ungeprüft'));
    const stamp=node('span','lab-room-presence-stamp');stamp.setAttribute('role','status');paintStamp(stamp);legend.append(stamp);
    container.replaceChildren(key,grid,legend);
  }
  return {hosts,render,updatePresence,pausePresence};
})();
