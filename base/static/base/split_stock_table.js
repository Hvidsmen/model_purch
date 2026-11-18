(() => {
  const table = document.getElementById('split-stock-table');
  if (!table) return;
  const children = Array.from(table.querySelectorAll('[data-stock-child]'));
  table.addEventListener('click', event => {
    const button = event.target.closest('[data-stock-toggle]');
    if (!button || !table.contains(button)) return;
    const expanded = button.getAttribute('aria-expanded') !== 'true';
    button.setAttribute('aria-expanded', String(expanded));
    button.querySelector('.split-stock-arrow').textContent = expanded ? '▾' : '▸';
    children.forEach(row => { if (row.dataset.stockChild === button.dataset.stockToggle) row.hidden = !expanded; });
  });
})();
