(() => {
  const table = document.querySelector('.split-plan-matrix');
  if (!table) return;
  table.addEventListener('keydown', event => {
    if (!event.target.matches('[data-plan-group-toggle], [data-plan-opening-toggle]') || !['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    event.target.click();
  });
  table.addEventListener('click', event => {
    const opening = event.target.closest('[data-plan-opening-toggle]');
    if (opening && table.contains(opening)) {
      const expanded = opening.getAttribute('aria-expanded') !== 'true';
      opening.setAttribute('aria-expanded', String(expanded));
      opening.querySelector('[data-plan-opening-arrow]').textContent = expanded ? '▾' : '▸';
      table.querySelectorAll('[data-plan-opening-component]').forEach(cell => { cell.hidden = !expanded; });
      const band = table.querySelector('[data-history-band="all"]');
      band.dataset.historyBaseSpan = expanded ? '8' : '5';
      band.colSpan += expanded ? 3 : -3;
      table.querySelectorAll('[data-plan-empty]').forEach(cell => { cell.colSpan += expanded ? 3 : -3; });
      return;
    }
    const button = event.target.closest('[data-plan-group-toggle]');
    if (!button || !table.contains(button)) return;
    const expanded = button.getAttribute('aria-expanded') !== 'true';
    button.setAttribute('aria-expanded', String(expanded));
    button.querySelector('[data-plan-group-arrow]').textContent = expanded ? '▾' : '▸';
    table.querySelectorAll('[data-plan-power-group]').forEach(row => {
      if (row.dataset.planPowerGroup === button.dataset.planGroupToggle) row.hidden = !expanded;
    });
  });
})();
