(() => {
  const dialog = document.getElementById('curator-copy-global');
  if (!dialog || dialog.dataset.ready) return;
  dialog.dataset.ready = '1';
  // Keep the modal out of any parent stacking/scroll containers.
  document.body.append(dialog);
  let previousOverflow;
  document.querySelectorAll('[data-curator-copy-open]').forEach(button => {
    button.addEventListener('click', () => {
      previousOverflow = document.documentElement.style.overflow;
      document.documentElement.style.overflow = 'hidden';
      dialog.showModal();
      dialog.querySelector('[data-copy-error]').hidden = true;
      dialog.querySelector('[data-copy-search]')?.focus({preventScroll: true});
    });
  });
  dialog.querySelectorAll('[data-curator-copy-close]').forEach(button => button.addEventListener('click', () => dialog.close()));
  dialog.addEventListener('close', () => { document.documentElement.style.overflow = previousOverflow || ''; });
  dialog.addEventListener('click', event => {
    const bounds = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close();
  });
  dialog.querySelectorAll('[data-copy-picker]').forEach(picker => {
    const options = [...picker.querySelectorAll('.cc-option')];
    const search = picker.querySelector('[data-copy-search]');
    const count = picker.querySelector('[data-copy-count]');
    const refresh = () => {
      const n = picker.querySelectorAll('input[type=checkbox]:checked').length;
      count.textContent = n ? `Выбрано: ${n}` : picker.dataset.copyPicker === 'goods_ids' ? 'Все матрицы' : 'Выбрано: 0';
    };
    search.addEventListener('input', () => {
      const words = search.value.toLocaleLowerCase('ru').trim().split(/\s+/).filter(Boolean);
      options.forEach(option => { option.hidden = !words.every(word => option.textContent.toLocaleLowerCase('ru').includes(word)); });
      picker.querySelector('[data-copy-empty]').hidden = !options.length || options.some(option => !option.hidden);
    });
    picker.querySelector('[data-copy-select]').addEventListener('click', () => { options.filter(option => !option.hidden).forEach(option => { option.querySelector('input').checked = true; }); refresh(); });
    picker.querySelector('[data-copy-clear]').addEventListener('click', () => { options.forEach(option => { option.querySelector('input').checked = false; }); refresh(); });
    picker.addEventListener('change', refresh);
    refresh();
  });
  dialog.querySelector('form').addEventListener('submit', event => {
    const recipients = dialog.querySelectorAll('input[name=targets]');
    const selected = [...recipients].filter(input => input.type === 'hidden' || input.checked);
    if (!selected.length || selected.length > 200) {
      event.preventDefault();
      const error = dialog.querySelector('[data-copy-error]');
      error.textContent = selected.length ? 'Выберите не более 200 получателей.' : 'Выберите хотя бы одного куратора и подразделение.';
      error.hidden = false;
      error.scrollIntoView({block: 'nearest'});
    }
  });
})();
