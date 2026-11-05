(() => {
 document.querySelectorAll('.next-products').forEach(menu => {
  const checks = [...menu.querySelectorAll('input[type=checkbox]')];
  const update = () => {
   const values = checks.filter(input => input.checked).map(input => input.value);
   menu.querySelector('[data-product-caption]').textContent = !values.length ? 'Все доступные' : values.length <= 2 ? values.join(', ') : `Выбрано: ${values.length}`;
  };
  checks.forEach(input => input.addEventListener('change', update));
  menu.querySelector('[data-product-clear]').addEventListener('click', () => {
   checks.forEach(input => { input.checked = false; }); update();
   menu.dispatchEvent(new Event('change', {bubbles: true}));
  });
  menu.querySelector('[data-product-search]').addEventListener('input', event => {
   const text = event.target.value.trim().toLocaleLowerCase('ru');
   checks.forEach(input => { input.closest('label').hidden = !input.value.toLocaleLowerCase('ru').includes(text); });
  });
  menu.addEventListener('keydown', event => {
   if (event.key === 'Escape') { menu.open = false; menu.querySelector('summary').focus(); }
  });
 });
 document.addEventListener('click', event => {
  document.querySelectorAll('.next-products[open]').forEach(menu => {
   if (!menu.contains(event.target)) menu.open = false;
  });
 });
 const scenario = document.querySelector('select[name=scenario]');
 scenario?.addEventListener('change', () => {
  scenario.form.querySelectorAll('input[name=period]').forEach(input => {
   if (['4', '5'].includes(input.value)) {
    input.disabled = scenario.value === 'separate';
    if (input.disabled) input.checked = false;
   }
  });
 });
})();
