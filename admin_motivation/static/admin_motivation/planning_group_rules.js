(() => {
  document.querySelectorAll('[data-planning-rule-form]').forEach(form => {
    const method = form.querySelector('[name="missing"]');
    const reserve = form.querySelector('[data-rule-reserves]');
    if (!method || !reserve) return;
    method.setAttribute('aria-controls', reserve.id);
    const update = () => {
      const fixed = method.value === 'fixed';
      reserve.hidden = !fixed;
      reserve.querySelectorAll('input').forEach(input => { input.disabled = !fixed; input.required = fixed; });
    };
    method.addEventListener('change', update);
    update();
  });
  const choices = [...document.querySelectorAll('[name="selected_rules"]')];
  const all = document.querySelector('[data-select-all-rules]');
  const count = document.querySelector('[data-rule-selection-count]');
  const update = () => {
    const selected = choices.filter(input => input.checked).length;
    if (count) count.textContent = `Выбрано: ${selected}`;
    if (all) { all.checked = choices.length > 0 && selected === choices.length; all.indeterminate = selected > 0 && selected < choices.length; }
  };
  choices.forEach(input => input.addEventListener('change', update));
  all?.addEventListener('change', () => { choices.forEach(input => input.checked = all.checked); update(); });
  update();
})();
