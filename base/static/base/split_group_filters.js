(() => {
 const menus = [...document.querySelectorAll('[data-split-filter]')];
 menus.forEach(menu => {
  const checks = [...menu.querySelectorAll('input[type=checkbox]')];
  const update = () => {
   const selected = checks.filter(input => input.checked);
   menu.querySelector('[data-caption]').textContent = selected.length ? `Выбрано: ${selected.length}` : menu.dataset.empty;
  };
  checks.forEach(input => input.addEventListener('change', update));
  menu.querySelector('[data-all]').addEventListener('click', () => { checks.forEach(input => { input.checked = true; }); update(); });
  menu.querySelector('[data-clear]').addEventListener('click', () => { checks.forEach(input => { input.checked = false; }); update(); });
  menu.querySelector('[data-search]').addEventListener('input', event => {
   const term = event.target.value.trim().toLocaleLowerCase('ru');
   checks.forEach(input => { input.closest('label').hidden = !input.closest('label').textContent.toLocaleLowerCase('ru').includes(term); });
  });
  menu.addEventListener('keydown', event => {
   if (event.key === 'Escape') { menu.open = false; menu.querySelector('summary').focus(); }
  });
 });
 document.addEventListener('click', event => { menus.forEach(menu => { if (!menu.contains(event.target)) menu.open = false; }); });
})();
