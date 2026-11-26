(() => {
  const form = document.querySelector('[data-planning-rule-form]');
  if (!form) return;
  const method = form.querySelector('[name="missing"]');
  const reserve = form.querySelector('[data-rule-reserves]');
  if (!method || !reserve) return;
  method.setAttribute('aria-controls', reserve.id);
  const update = () => {
    const fixed = method.value === 'fixed';
    reserve.hidden = !fixed;
    reserve.querySelectorAll('input').forEach(input => {
      input.disabled = !fixed;
      input.required = fixed;
    });
  };
  method.addEventListener('change', update);
  update();
})();
