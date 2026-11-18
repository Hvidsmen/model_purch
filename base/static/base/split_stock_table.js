(() => {
  const table = document.getElementById('split-stock-table');
  if (!table) return;
  const children = Array.from(table.querySelectorAll('[data-stock-child]'));
  const states = Array.from(document.querySelectorAll('[data-forecast-state]'));
  const controls = attribute => Array.from(table.querySelectorAll(`[${attribute}]`));
  const find = (attribute, id) => controls(attribute).find(node => node.getAttribute(attribute) === id);
  function choose(id, mode) {
    const state = states.find(node => node.dataset.forecastState === id);
    if (!state) return;
    state.value = mode;
    find('data-forecast-choice', id).value = mode === 'powers' ? 'powers' : 'group';
    find('data-forecast-status', id).textContent = mode === 'powers'
      ? 'Прогноз задан по мощностям; общий итог — их сумма. Сохраните группу для пересчёта.'
      : mode === 'monthly' ? 'Используется сумма месячных значений. Сохраните группу для пересчёта.'
      : 'Общий прогноз распределяется по долям мощностей. Сохраните группу для пересчёта.';
    if (mode === 'monthly') state.form.elements.namedItem('forecast_monthly_confirm').value = '1';
  }
  function sumIntoGroup(id, attribute) {
    let total = 0n;
    const inputs = controls(attribute).filter(node => node.getAttribute(attribute) === id);
    let known = inputs.length > 0;
    for (const input of inputs) {
      if (!input.value || input.value === '.' || !/^\d*(?:\.\d{0,6})?$/.test(input.value)) { known = false; break; }
      const [whole, fraction = ''] = input.value.split('.');
      total += BigInt(whole || '0') * 1000000n + BigInt(fraction.padEnd(6, '0'));
    }
    const value = known ? `${total / 1000000n}.${String(total % 1000000n).padStart(6, '0')}`.replace(/\.?0+$/, '') : '';
    find('data-forecast-group', id).value = value;
  }
  table.addEventListener('input', event => {
    const target = event.target;
    if (target.hasAttribute('data-forecast-group')) choose(target.dataset.forecastGroup, 'group');
    if (target.hasAttribute('data-forecast-power')) { choose(target.dataset.forecastPower, 'powers'); sumIntoGroup(target.dataset.forecastPower, 'data-forecast-power'); }
    if (target.hasAttribute('data-forecast-month')) { choose(target.dataset.forecastMonth, 'monthly'); sumIntoGroup(target.dataset.forecastMonth, 'data-forecast-month'); }
  });
  table.addEventListener('change', event => {
    if (!event.target.hasAttribute('data-forecast-choice')) return;
    const id = event.target.dataset.forecastChoice;
    choose(id, event.target.value);
    if (event.target.value === 'powers') sumIntoGroup(id, 'data-forecast-power');
  });
  table.addEventListener('click', event => {
    const channelToggle = event.target.closest('[data-stock-channel-toggle]');
    if (channelToggle && table.contains(channelToggle)) {
      const expanded = channelToggle.getAttribute('aria-expanded') !== 'true';
      channelToggle.setAttribute('aria-expanded', String(expanded));
      channelToggle.querySelector('[data-channel-arrow]').textContent = expanded ? '▾' : '▸';
      table.querySelectorAll('[data-stock-channel-column]').forEach(cell => { cell.hidden = !expanded; });
      table.querySelectorAll('[data-stock-fullspan]').forEach(cell => {
        cell.colSpan = expanded ? Number(cell.dataset.stockFullspan) : 13;
      });
      return;
    }
    const button = event.target.closest('[data-stock-toggle]');
    if (!button || !table.contains(button)) return;
    const expanded = button.getAttribute('aria-expanded') !== 'true';
    button.setAttribute('aria-expanded', String(expanded));
    button.querySelector('.split-stock-arrow').textContent = expanded ? '▾' : '▸';
    children.forEach(row => { if (row.dataset.stockChild === button.dataset.stockToggle) row.hidden = !expanded; });
  });
})();
