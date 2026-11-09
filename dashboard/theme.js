/* Apply before CSS paints; preference is scoped to this LAB app. */
(() => {
  'use strict';
  const key='cisco-lab.theme';
  const root=document.documentElement;
  const media=window.matchMedia('(prefers-color-scheme: dark)');
  const valid=value=>['light','dark','system'].includes(value)?value:'system';
  let preference='system';
  try {preference=valid(localStorage.getItem(key));}catch(_error){}
  function apply(value) {
    preference=valid(value);
    root.dataset.themePreference=preference;
    root.dataset.theme=preference==='system'?(media.matches?'dark':'light'):preference;
    for(const select of document.querySelectorAll('[data-theme-select]'))select.value=preference;
  }
  apply(preference);
  media.addEventListener('change',()=>{if(preference==='system')apply('system');});
  window.addEventListener('storage',event=>{if(event.key===key||event.key===null)apply(event.newValue);});
  document.addEventListener('DOMContentLoaded',()=>{
    for(const select of document.querySelectorAll('[data-theme-select]')) {
      select.value=preference;
      select.addEventListener('change',()=>{
        apply(select.value);
        try {localStorage.setItem(key,preference);}catch(_error){}
      });
    }
  });
})();
