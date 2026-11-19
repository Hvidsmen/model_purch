(() => {
  const panel = document.querySelector('[data-reference-filters]');
  if (!panel) return;
  const references = JSON.parse(document.getElementById('split-reference-values').textContent);
  const controls = [...panel.querySelectorAll('[data-reference-filter]')];
  const storage = 'split-reference-filters-' + panel.dataset.referenceFilters;
  let saved = {};
  try { saved = JSON.parse(sessionStorage.getItem(storage) || '{}'); } catch (_) {}
  controls.forEach(control => {
    const key = control.dataset.referenceFilter;
    const values = [...new Set(Object.values(references).map(ref => ref[key] || '—'))].sort((a,b) => a.localeCompare(b, 'ru'));
    values.forEach(value => control.add(new Option(value, value)));
    if (values.includes(saved[key])) control.value = saved[key];
  });
  function apply() {
    const selected = Object.fromEntries(controls.map(control => [control.dataset.referenceFilter, control.value]));
    const matches = new Set(Object.entries(references).filter(([,ref]) => controls.every(control => !control.value || (ref[control.dataset.referenceFilter] || '—') === control.value)).map(([gid]) => gid));
    const nodes = new Map([...document.querySelectorAll('[data-plan-node]')].map(row => [row.dataset.planNode, row]));
    const visibleNodes = new Set();
    document.querySelectorAll('[data-plan-group]').forEach(row => {
      if (!matches.has(row.dataset.planGroup)) return;
      let parent = row.dataset.planParent;
      while (nodes.has(parent)) { visibleNodes.add(parent); parent = nodes.get(parent).dataset.planParent; }
    });
    document.querySelectorAll('[data-plan-group], [data-plan-power-group], [data-stock-group], [data-stock-child], [data-grid-row], [data-plan-node]').forEach(row => {
      const gid = row.dataset.planGroup || row.dataset.planPowerGroup || row.dataset.stockGroup || row.dataset.stockChild || row.dataset.gid;
      row.toggleAttribute('data-reference-excluded', row.hasAttribute('data-plan-node') ? !visibleNodes.has(row.dataset.planNode) : !matches.has(gid));
    });
    const shown = new Set([...document.querySelectorAll('[data-plan-group], [data-stock-group], [data-grid-row]')].filter(row => !row.hasAttribute('data-reference-excluded')).map(row => row.dataset.planGroup || row.dataset.stockGroup || row.dataset.gid));
    panel.querySelector('[data-reference-filter-count]').textContent = shown.size ? 'Показано групп: ' + shown.size : 'Нет групп по выбранным условиям.';
    try { sessionStorage.setItem(storage, JSON.stringify(selected)); } catch (_) {}
    document.dispatchEvent(new Event('split-reference-filter-change'));
  }
  controls.forEach(control => control.addEventListener('change', apply));
  panel.querySelector('[data-reference-reset]').addEventListener('click', () => { controls.forEach(control => { control.value = ''; }); apply(); });
  apply();
})();
