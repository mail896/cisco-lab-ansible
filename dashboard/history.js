'use strict';

// A single paging component for every growing LAB history. Paging performs GETs.
const historyViews = new Map();
function historyView(id, endpoint) {
  let state = historyViews.get(id);
  if (!state) { state = {page: 1, version: 0}; historyViews.set(id, state); }
  if (endpoint && state.endpoint !== endpoint) { state.endpoint = endpoint; state.page = 1; }
  return state;
}
function resetHistory(id) { historyView(id).page = 1; }
async function requestHistory(endpoint, id, reload, fetchPage = null) {
  const state = historyView(id, endpoint);
  state.reload = reload;
  const version = ++state.version;
  const data = await (fetchPage ? fetchPage(state.page) : request(endpoint + '/page/' + state.page));
  if (state.version !== version) return null;
  state.meta = data.pagination;
  state.page = data.pagination.page;
  return data;
}
function renderHistory(id, data, render, key = 'runs') {
  const target = el(id);
  const state = historyView(id);
  const opened = new Map([...target.children].filter(node => node.tagName === 'DETAILS')
    .map(node => [node.dataset.historyKey, node.open]));
  target.replaceChildren(...data[key].map((row, index) => {
    const node = render(row, index);
    node.dataset.historyKey = row.id || row.time || String(index);
    if (node.tagName === 'DETAILS' && opened.has(node.dataset.historyKey)) node.open = opened.get(node.dataset.historyKey);
    return node;
  }));
  let pager = el(id + '-pagination');
  if (!pager) {
    pager = document.createElement('div'); pager.id = id + '-pagination'; pager.className = 'history-pagination';
    pager.setAttribute('role', 'navigation'); pager.setAttribute('aria-label', 'Seitennavigation des Verlaufs');
    const anchor = target.tagName === 'TBODY' ? target.closest('.scroll') : target;
    anchor.insertAdjacentElement('afterend', pager);
  }
  const {page, pages, total, page_size} = data.pagination;
  pager.hidden = pages <= 1;
  const range = document.createElement('span'); range.className = 'history-range';
  range.textContent = total ? ((page - 1) * page_size + 1) + '–' + Math.min(page * page_size, total) + ' von ' + total + ' Einträgen' : 'Keine Einträge';
  const number = document.createElement('span'); number.className = 'history-number'; number.setAttribute('aria-live', 'polite');
  number.textContent = 'Seite ' + page + ' von ' + pages;
  function button(label, direction, disabled) {
    const control = document.createElement('button'); control.type = 'button'; control.className = 'pager-button';
    control.textContent = label; control.disabled = disabled; control.dataset.direction = direction;
    control.addEventListener('click', async () => {
      if (state.loading) return;
      state.loading = true; state.page = page + (direction === 'next' ? 1 : -1);
      for (const item of pager.querySelectorAll('button')) item.disabled = true;
      try { await state.reload(); }
      finally {
        state.loading = false;
        for (const item of pager.querySelectorAll('button')) {
          item.disabled = item.dataset.direction === 'next' ? state.meta.page === state.meta.pages : state.meta.page === 1;
        }
        const next = pager.querySelector('button[data-direction="' + direction + '"]:not(:disabled)') || pager.querySelector('button:not(:disabled)');
        next?.focus({preventScroll: true});
      }
    });
    return control;
  }
  pager.replaceChildren(range, button('Zurück', 'previous', page === 1), number, button('Weiter', 'next', page === pages));
}
