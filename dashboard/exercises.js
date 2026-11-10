'use strict';

let exerciseState = null;
let exerciseTask = null;
let exerciseDevice = 'R1';
let exerciseBusy = false;
let exerciseTeamNames = Array(8).fill('');
const exerciseEditors = new Map();
let exerciseEditorLoading = null;
const exerciseTabs = ['plan','text','wireshark'];

async function exerciseOpenTab(name) {
  for(const key of exerciseTabs){
    const selected=key===name;el('exercise-panel-'+key).hidden=!selected;
    el('exercise-tab-'+key).setAttribute('aria-selected',String(selected));el('exercise-tab-'+key).tabIndex=selected?0:-1;
  }
  if(name==='plan')return;
  el('exercise-panel-'+name).append(el('exercise-editor-actions'));
  try {
    if(!window.Jodit){
      if(!exerciseEditorLoading)exerciseEditorLoading=new Promise((resolve,reject)=>{
        const css=document.createElement('link');css.rel='stylesheet';css.href=basePath+'/vendor/jodit/jodit.min.css';document.head.append(css);
        const script=document.createElement('script');script.src=basePath+'/vendor/jodit/jodit.min.js';script.onload=resolve;script.onerror=()=>reject(new Error('Der Texteditor konnte nicht geladen werden.'));document.head.append(script);
      });
      await exerciseEditorLoading;
    }
    for(const area of el('exercise-panel-'+name).querySelectorAll('[data-exercise-rich]')){
      if(exerciseEditors.has(area.id))continue;
      const editor=window.Jodit.make(area,{
        language:'de',height:area.id==='exercise-work'?300:180,toolbarAdaptive:false,toolbarSticky:false,
        showXPathInStatusbar:false,showCharsCounter:false,showWordsCounter:false,
        allowResizeX:false,allowResizeY:false,
        buttons:['undo','redo','|','bold','italic','underline','|','ul','ol','|','eraser','fullsize'],
        disablePlugins:['file','image','video','source','iframe','speech-recognize','powered-by-jodit'],
        uploader:{insertImageAsBase64URI:false},askBeforePasteHTML:false,
        askBeforePasteFromWord:false,defaultActionOnPasteFromWord:'insert_clear_html',
        theme:document.documentElement.dataset.theme==='dark'?'dark':'default',
      });
      exerciseEditors.set(area.id,editor);
    }
  }catch(error){exerciseMessage(error.message+' Du kannst die Texte weiterhin im Textfeld bearbeiten.');}
}
const exerciseOptionIds = {ipv4:'ipv4',ipv6:'ipv6',level:'level',teams:'teams',sizes:'sizes',
  lan_prefix:'lan-prefix',transfer_prefix:'transfer-prefix',lan_min:'lan-min',lan_max:'lan-max',
  transfer_min:'transfer-min',transfer_max:'transfer-max',addresses:'addresses',wireshark:'wireshark',ws_level:'ws-level'};
const exerciseTextIds = ['title','introduction','work','hints','reset','reset_ipv6',
  'ws_title','ws_work','ws_advanced','ws_solution','ws_solution_advanced'];
const exerciseTextId = key => key==='reset'?'exercise-reset-text':'exercise-'+key;
function exerciseNode(tag, text, className) {
  const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;
}
function exerciseMessage(text) { el('exercise-message').textContent=text; }
function exerciseValidateNumber(node) {
  node.setCustomValidity('');
  const low=Number(node.min),high=Number(node.max);
  if(node.validity.badInput||node.value==='')node.setCustomValidity('Bitte eine ganze Zahl eingeben.');
  else if(node.validity.rangeUnderflow||node.validity.rangeOverflow)node.setCustomValidity('Bitte eine Präfixlänge zwischen '+low+' und '+high+' eingeben.');
  else if(node.validity.stepMismatch)node.setCustomValidity('Bitte eine ganze Zahl ohne Nachkommastellen eingeben.');
}
function exerciseNames(count=exerciseTask?.options.teams||Number(el('exercise-teams').value)) { return exerciseTeamNames.slice(0,count); }
function exerciseLoadNames(names=[]) {
  exerciseTeamNames=Array.from({length:8},(_,i)=>names[i]||'');exerciseRenderNames();
}
function exerciseRenderNames() {
  const count=Number(el('exercise-teams').value);
  el('exercise-team-names').replaceChildren(...Array.from({length:count},(_,i)=>{
    const label=exerciseNode('label',count===1?'Aufgabenname / Schüler im Team':'Team '+(i+1)+' · Name / Schüler');
    const input=exerciseNode('input');input.id='exercise-team-name-'+(i+1);input.type='text';input.maxLength=160;
    input.value=exerciseTeamNames[i];input.placeholder='z. B. Mia & Jonas';input.autocomplete='off';
    input.addEventListener('input',()=>{exerciseTeamNames[i]=input.value;exerciseShowName();});label.append(input);return label;
  }));
}
function exerciseShowName() {
  const name=exerciseTeamNames[Number(el('exercise-team').value||1)-1]||'';
  el('exercise-preview-name').textContent=name;el('exercise-preview-name').hidden=!name;
}
function exerciseSettings() {
  const result={};
  for(const [key,id] of Object.entries(exerciseOptionIds)) {
    const node=el('exercise-'+id);
    result[key]=node.type==='checkbox'?node.checked:node.type==='number'||key==='teams'?Number(node.value):node.value;
  }
  return result;
}
function exerciseTexts() { return Object.fromEntries(exerciseTextIds.map(key=>[key,exerciseEditors.has(exerciseTextId(key))?exerciseEditors.get(exerciseTextId(key)).value:el(exerciseTextId(key)).value])); }
function exerciseFill(text, settings) {
  for(const key of exerciseTextIds){const id=exerciseTextId(key);el(id).value=text[key];if(exerciseEditors.has(id))exerciseEditors.get(id).value=text[key];}
  for(const [key,id] of Object.entries(exerciseOptionIds)) {
    const node=el('exercise-'+id);if(node.type==='checkbox')node.checked=settings[key];else node.value=settings[key];
  }
  el('exercise-level').value='plan';el('exercise-addresses').value='fixed';
  exerciseRenderNames();
  exerciseControls();
}
function exerciseControls() {
  el('exercise-ws-settings').hidden=!el('exercise-wireshark').checked;
  const random=el('exercise-sizes').value==='random';
  el('exercise-fixed-prefixes').hidden=random;el('exercise-random-prefixes').hidden=!random;
  el('exercise-prefixes').disabled=!el('exercise-ipv4').checked;
  el('exercise-sizes').disabled=!el('exercise-ipv4').checked;
  const template=exerciseState?.templates.find(row=>row.id===el('exercise-template').value);
  el('exercise-template-save').disabled=exerciseBusy||(template?!template.editable:labRole!=='admin');
  el('exercise-template-save').textContent=template?'Vorlage aktualisieren':'Standardvorlage aktualisieren';
  el('exercise-template-delete').hidden=!template?.editable;
  el('exercise-template-remove').hidden=!template?.editable;
  el('exercise-template-remove').disabled=exerciseBusy;
  for(const id of ['exercise-ipv4','exercise-ipv6'])el(id).disabled=exerciseBusy;
  el('exercise-create').disabled=exerciseBusy||!(el('exercise-ipv4').checked||el('exercise-ipv6').checked);
  el('exercise-template-copy').disabled=exerciseBusy;
  el('exercise-template-delete').disabled=exerciseBusy;
  const deletion=exerciseState?.deletion;
  el('exercise-delete-all').disabled=exerciseBusy||!deletion?.count;
  el('exercise-delete-all').textContent=(deletion?.scope==='own'?'Meine Aufgaben löschen':'Alle Aufgaben löschen')+(deletion?.count?' ('+deletion.count+')':'');
  el('exercise-reset').disabled=exerciseBusy;
  el('exercise-standard-save').hidden=labRole!=='admin'||!template;
  el('exercise-standard-save').disabled=exerciseBusy;
  el('exercise-text-apply').disabled=exerciseBusy||!exerciseTask;
  el('exercise-save').disabled=exerciseBusy||!exerciseTask;
  const fixed=el('exercise-sizes').querySelector('option[value=fixed]');
  fixed.textContent='Fest · LAN /'+el('exercise-lan-prefix').value+', Transfer /'+el('exercise-transfer-prefix').value;
  for(const node of el('exercise-form').querySelectorAll('input[type=number]'))exerciseValidateNumber(node);
}
async function exerciseAction(action) {
  if(exerciseBusy)return;
  exerciseBusy=true;exerciseControls();
  try { await action(); }
  catch(error) { exerciseMessage(error.message); }
  finally { exerciseBusy=false;exerciseControls(); }
}
async function loadExercises(first=false) {
  try {
    const data=await requestHistory('/api/exercises','exercise-history',()=>loadExercises());
    if(!data)return;
    exerciseState=data;
    const choice=el('exercise-template').value;
    el('exercise-template').replaceChildren(new Option('Standardvorlage',''),...data.templates.map(row=>new Option(row.title+(row.editable?'':' · gemeinsam'),row.id)));
    if(data.templates.some(row=>row.id===choice))el('exercise-template').value=choice;
    if(first)exerciseFill(data.defaults.text,data.defaults.options);
    renderHistory('exercise-history',data,row=>{
      const node=exerciseNode('div',undefined,'exercise-history-row');
      const title=exerciseNode('div');title.append(exerciseNode('strong',row.label||row.title));
      title.append(exerciseNode('small',new Date(row.created).toLocaleString('de-DE',{timeZone:'Europe/Berlin',dateStyle:'short',timeStyle:'short'})+' · '+row.author+' · '+row.options.teams+' Variante(n) · '+(row.options.ipv4?'IPv4 ':'')+(row.options.ipv6?'IPv6':'')+(row.options.wireshark?' · Wireshark':'')));
      if(row.networks?.length){
        const networkLine=v=>'Team '+v.team+' · '+[['a','LAN A'],['transfer','Transfer'],['b','LAN B']].map(([key,label])=>label+': '+['ipv4','ipv6'].filter(f=>v.networks[key][f]).map(f=>v.networks[key][f]).join(' / ')).join(' · ');
        title.append(exerciseNode('small',networkLine(row.networks[0]),'exercise-networks'));
        if(row.networks.length>1){const more=exerciseNode('details');more.append(exerciseNode('summary','Weitere Teamnetze'));for(const v of row.networks.slice(1))more.append(exerciseNode('small',networkLine(v),'exercise-networks'));title.append(more);}
      }
      const controls=exerciseNode('div',undefined,'exercise-buttons');const open=exerciseNode('button','Öffnen','pager-button');open.type='button';
      open.addEventListener('click',()=>exerciseAction(async()=>{
        exerciseTask=await request('/api/exercises/task/'+row.id);exerciseFill(exerciseTask.text,exerciseTask.options);el('exercise-template').value='';
        exerciseLoadNames(exerciseTask.team_names);
        exerciseRender();exerciseMessage('Gespeicherte Aufgabe geöffnet. Geänderte Einstellungen erzeugen beim nächsten Klick eine neue Aufgabe.');
      }));controls.append(open);
      if(row.editable){const remove=exerciseNode('button','Löschen','pager-button');remove.type='button';remove.addEventListener('click',()=>exerciseAction(async()=>{
        if(!window.confirm('Aufgabe „'+row.title+'“ aus der Sammlung entfernen?'))return;
        await request('/api/exercises/delete',{kind:'tasks',identity:row.id,confirmed:true});
        if(exerciseTask?.id===row.id){exerciseTask=null;exerciseRender();}
        await loadExercises();exerciseMessage('Aufgabe entfernt.');
      }));controls.append(remove);}
      node.append(title,controls);return node;
    });
    if(!data.runs.length)el('exercise-history').append(exerciseNode('p','Noch keine Aufgaben gespeichert.','field-hint'));
    exerciseControls();
  }catch(error){exerciseMessage(error.message);}
}
function exerciseVariant() { return exerciseTask?.variants[Number(el('exercise-team').value||1)-1]; }
function exerciseRender(team='1') {
  const task=exerciseTask;
  el('exercise-empty').hidden=Boolean(task);el('exercise-result').hidden=!task;el('exercise-device-panel').hidden=!task;
  el('exercise-team-label').hidden=!task||task.variants.length===1;
  el('exercise-preview-title').textContent=task?task.title:'Dein nächstes Netzwerk';
  if(!task){el('exercise-plan').replaceChildren();return;}
  el('exercise-team').replaceChildren(...task.variants.map(row=>new Option('Team '+row.team,String(row.team))));
  el('exercise-team').value=team;
  exerciseRenderVariant();
}
function exerciseRenderVariant() {
  const task=exerciseTask,variant=exerciseVariant();if(!variant)return;
  el('exercise-meta').textContent=(task.saved?'Gespeicherter Stand':'Ungespeicherte Vorschau')+' · '+task.id.slice(0,8)+' · '+(task.options.wireshark?'mit Wireshark':'ohne Wireshark');
  exerciseShowName();
  el('exercise-save').textContent=task.saved?'Aufgabenstand speichern':'Aufgabe speichern';
  // SVG is generated from fixed primitives and escaped server-side labels.
  el('exercise-plan').innerHTML=variant.svg;
  for(const node of el('exercise-plan').querySelectorAll('[data-device]')){
    const choose=()=>{exerciseDevice=node.dataset.device;exerciseRenderDevice();};node.addEventListener('click',choose);
    node.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();choose();}});
  }
  const root=basePath+'/api/exercises/export/';
  el('exercise-pdf').href=root+'pdf/'+task.id+'/'+variant.team;el('exercise-zip').href=root+'zip/'+task.id+'/'+variant.team;
  el('exercise-pdf-all').href=root+'pdf/'+task.id+'/all';el('exercise-zip-all').href=root+'zip/'+task.id+'/all';
  for(const id of ['exercise-pdf-all','exercise-zip-all'])el(id).hidden=task.variants.length===1;
  exerciseRenderDevice();
}
function exerciseRenderDevice() {
  const variant=exerciseVariant();if(!variant)return;
  const device=variant.devices[exerciseDevice];
  el('exercise-device-title').textContent=exerciseDevice+' · '+device.model;
  el('exercise-config').textContent=variant.configs[exerciseDevice];el('exercise-config-copy').textContent='Kopieren';
  el('exercise-device-tabs').replaceChildren(...Object.keys(variant.devices).map(name=>{
    const button=exerciseNode('button',name,'pager-button');button.type='button';button.setAttribute('aria-pressed',String(name===exerciseDevice));
    button.addEventListener('click',()=>{exerciseDevice=name;exerciseRenderDevice();});return button;
  }));
  el('exercise-device-note').textContent=exerciseDevice.startsWith('PC')?'Die beiden Blöcke in „Labornetz einstellen“ übernehmen. Keine Administratorrechte nötig.':
    exerciseDevice.startsWith('S')&&exerciseTask.options.ipv6?'IPv6-Management am 2960: vorher SDM-Profil prüfen; Vorbereitung und möglicher Neustart stehen im PDF und ZIP. Die VLAN-1-Adresse dient lokalem Management.':
    'Für zurückgesetzte Übungsgeräte. Beim Speichern den vorgeschlagenen Dateinamen mit Enter bestätigen.';
}
el('exercise-form').addEventListener('input',exerciseControls);
el('exercise-form').addEventListener('invalid',event=>{if(event.target.type==='number')exerciseValidateNumber(event.target);},true);
el('exercise-form').addEventListener('submit',event=>{
  event.preventDefault();exerciseAction(async()=>{
    exerciseMessage('Aufgabe wird erzeugt …');
    exerciseTask=await request('/api/exercises/preview',{settings:exerciseSettings(),text:exerciseTexts(),team_names:exerciseNames(Number(el('exercise-teams').value))});
    exerciseRender();exerciseMessage('Ungespeicherte Vorschau. Würfle erneut oder speichere die passende Aufgabe. Die Vorschau verfällt bei einem APP-Neustart.');
  });
});
el('exercise-template').addEventListener('change',()=>{
  const template=exerciseState.templates.find(row=>row.id===el('exercise-template').value);
  exerciseFill(template?.text||exerciseState.defaults.text,template?.options||exerciseState.defaults.options);
});
for(const [id,copy] of [['exercise-template-save',false],['exercise-template-copy',true]])el(id).addEventListener('click',()=>exerciseAction(async()=>{
  const identity=el('exercise-template').value;
  if(!copy&&!identity){await exerciseSaveStandard();return;}
  const text=exerciseTexts();
  if(copy){
    const name=window.prompt('Name der neuen Vorlage',text.title+' (Kopie)');
    if(name===null)return;
    if(!name.trim())throw new Error('Bitte einen Namen für die neue Vorlage eingeben.');
    text.title=name.trim();
  }
  const result=await request('/api/exercises/template',{identity:copy?'':identity,text,settings:exerciseSettings()});
  await loadExercises();el('exercise-template').value=result.id;exerciseFill(result.text,result.options);
  exerciseMessage(copy?'Neue Vorlage gespeichert.':'Vorlage aktualisiert.');
}));
for(const id of ['exercise-template-delete','exercise-template-remove'])el(id).addEventListener('click',()=>exerciseAction(async()=>{
  if(!window.confirm('Diese Vorlage entfernen? Gespeicherte Aufgaben bleiben erhalten.'))return;
  await request('/api/exercises/delete',{kind:'templates',identity:el('exercise-template').value,confirmed:true});
  el('exercise-template').value='';await loadExercises();exerciseMessage('Vorlage entfernt.');
}));
el('exercise-team').addEventListener('change',exerciseRenderVariant);
el('exercise-reload').addEventListener('click',()=>loadExercises());
el('exercise-config-copy').addEventListener('click',async()=>{
  try { await navigator.clipboard.writeText(el('exercise-config').textContent);el('exercise-config-copy').textContent='Kopiert'; }
  catch { const range=document.createRange();range.selectNodeContents(el('exercise-config'));window.getSelection().removeAllRanges();window.getSelection().addRange(range);el('exercise-config-copy').textContent='Markiert · bitte kopieren'; }
});
if(currentPage==='/aufgaben')loadExercises(true);
for(const name of exerciseTabs){
  el('exercise-tab-'+name).addEventListener('click',()=>exerciseOpenTab(name));
  el('exercise-tab-'+name).addEventListener('keydown',event=>{
    if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
      event.preventDefault();const next=event.key==='Home'?'plan':event.key==='End'?'wireshark':exerciseTabs[(exerciseTabs.indexOf(name)+(event.key==='ArrowLeft'?-1:1)+exerciseTabs.length)%exerciseTabs.length];
      exerciseOpenTab(next);el('exercise-tab-'+next).focus();
    }
  });
}
el('exercise-back-plan').addEventListener('click',()=>exerciseOpenTab('plan'));
for(const node of document.querySelectorAll('[data-exercise-open]'))node.addEventListener('click',()=>exerciseOpenTab(node.dataset.exerciseOpen));
el('exercise-reset').addEventListener('click',()=>{
  if(!exerciseState||!window.confirm('Den aktuellen Entwurf samt Vorschau zurücksetzen? Gespeicherte Aufgaben und Vorlagen bleiben erhalten.'))return;
  exerciseTask=null;exerciseDevice='R1';el('exercise-template').value='';
  exerciseLoadNames();
  exerciseFill(exerciseState.defaults.text,exerciseState.defaults.options);exerciseRender();exerciseOpenTab('plan');exerciseMessage('Entwurf zurückgesetzt. Die Sammlung bleibt erhalten.');
});
el('exercise-delete-all').addEventListener('click',()=>exerciseAction(async()=>{
  const preview=exerciseState.deletion;
  if(!window.confirm(preview.count+' '+(preview.scope==='all'?'gespeicherte Aufgaben aus der gesamten Sammlung':'eigene gespeicherte Aufgaben')+' unwiderruflich löschen? Vorlagen bleiben erhalten.'))return;
  const result=await request('/api/exercises/delete-all',{token:preview.token,confirmed:true});
  if(exerciseTask?.editable){exerciseTask=null;exerciseRender();}
  resetHistory('exercise-history');await loadExercises();exerciseMessage(result.deleted+' Aufgaben gelöscht.');
}));
new MutationObserver(()=>{
  const dark=document.documentElement.dataset.theme==='dark';
  for(const editor of exerciseEditors.values()){
    editor.o.theme=dark?'dark':'default';editor.container.classList.toggle('jodit_theme_dark',dark);editor.container.classList.toggle('jodit_theme_default',!dark);
  }
}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});

async function exerciseApplyTexts(save=false) {
  if(!exerciseTask)return;
  const team=el('exercise-team').value;
  exerciseTask=await request('/api/exercises/'+(save?'save':'text'),{identity:exerciseTask.id,text:exerciseTexts(),team_names:exerciseNames()});
  exerciseRender(team);
}
el('exercise-save').addEventListener('click',()=>exerciseAction(async()=>{
  await exerciseApplyTexts(true);resetHistory('exercise-history');await loadExercises();
  exerciseMessage('Aufgabenstand mit den aktuellen Texten und diesen Netzen gespeichert.');
}));
el('exercise-text-apply').addEventListener('click',()=>exerciseAction(async()=>{
  await exerciseApplyTexts();exerciseOpenTab('plan');exerciseMessage('Aktuelle Texte übernommen. Die Netze sind unverändert.');
}));
for(const id of ['exercise-pdf','exercise-zip','exercise-pdf-all','exercise-zip-all'])el(id).addEventListener('click',event=>{
  event.preventDefault();exerciseAction(async()=>{
    await exerciseApplyTexts();
    const link=document.createElement('a');link.href=el(id).href;link.hidden=true;document.body.append(link);link.click();link.remove();
    exerciseMessage('Export mit den aktuellen Texten. '+(exerciseTask.saved?'Dieser Stand ist gespeichert.':'Die Vorschau ist noch nicht in der Sammlung gespeichert.'));
  });
});
async function exerciseSaveStandard() {
  if(!window.confirm('Diese Texte und Einstellungen als gemeinsame Standardvorlage für neue Aufgaben festlegen? Gespeicherte Aufgaben bleiben unverändert.'))return;
  await request('/api/exercises/standard',{settings:exerciseSettings(),text:exerciseTexts()});
  el('exercise-template').value='';await loadExercises();exerciseMessage('Standardvorlage aktualisiert. Sie gilt beim nächsten Öffnen und Zurücksetzen des Generators.');
}
el('exercise-standard-save').addEventListener('click',()=>exerciseAction(exerciseSaveStandard));
el('exercise-teams').addEventListener('change',exerciseRenderNames);
for(const family of ['ipv4','ipv6'])el('exercise-'+family).addEventListener('change',()=>{
  if(!exerciseTask)return;
  if(!el('exercise-ipv4').checked&&!el('exercise-ipv6').checked){
    el('exercise-'+family).checked=true;exerciseControls();exerciseMessage('Mindestens eine IP-Version muss ausgewählt bleiben.');return;
  }
  exerciseAction(async()=>{
    const team=el('exercise-team').value;
    try {
      exerciseTask=await request('/api/exercises/families',{identity:exerciseTask.id,ipv4:el('exercise-ipv4').checked,ipv6:el('exercise-ipv6').checked,text:exerciseTexts(),team_names:exerciseNames()});
      exerciseRender(team);
      exerciseMessage('IP-Auswahl aktualisiert. Vorhandene Netze bleiben erhalten; erneutes Anwählen stellt sie wieder her.');
    }catch(error){
      for(const f of ['ipv4','ipv6'])el('exercise-'+f).checked=exerciseTask.options[f];throw error;
    }
  });
});
