(() => {
  document.querySelectorAll('[data-motivation-types]').forEach(field => {
    const checks = [...field.querySelectorAll('input[name="type"]')];
    const caption = field.querySelector('[data-types-caption]');
    const update = () => {
      const selected = checks.filter(input => input.checked);
      caption.textContent = !selected.length || selected.length === checks.length
        ? 'Все доступные' : selected.map(input => input.value).join(', ');
    };
    checks.forEach(input => input.addEventListener('change', update));
    field.querySelector('[data-types-all]').addEventListener('click', () => {
      checks.forEach(input => { input.checked = true; });
      update();
    });
    field.querySelector('[data-types-clear]').addEventListener('click', () => {
      checks.forEach(input => { input.checked = false; });
      update();
    });
    document.addEventListener('click', event => {
      if (!field.contains(event.target)) field.open = false;
    });
    field.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        field.open = false;
        field.querySelector('summary').focus();
      }
    });
    update();
  });
})();
