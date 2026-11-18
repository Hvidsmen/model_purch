(() => {
  const table = document.querySelector('.split-plan-matrix');
  if (!table) return;
  table.addEventListener('keydown', event => {
    if (!event.target.matches('[data-plan-group-toggle]') || !['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    event.target.click();
  });
  table.addEventListener('click', event => {
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
