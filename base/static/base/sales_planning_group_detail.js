(() => {
  const card = document.querySelector('.planning-group-card');
  const dialog = card?.querySelector('[data-group-link-dialog]');
  if (!dialog || typeof dialog.showModal !== 'function') return;
  const metadata = JSON.parse(document.getElementById('group-link-metadata').textContent);
  const sections = new Map();
  const title = dialog.querySelector('[data-link-dialog-title]');
  const search = dialog.querySelector('[data-link-search]');
  const filters = Array.from(dialog.querySelectorAll('[data-link-filter]'));
  const list = dialog.querySelector('[data-link-options]');
  const count = dialog.querySelector('[data-link-picker-count]');
  let active = null, pending = new Set(), visible = [];
  function selected(section) {
    return section.options.filter(option => option.selected);
  }
  function renderSelected(section) {
    const items = selected(section);
    section.list.replaceChildren();
    for (const option of items) {
      const item = document.createElement('li');
      const name = document.createElement('span');
      name.textContent = option.textContent + (metadata[option.value]?.active === false ? ' · неактивна' : '');
      const remove = document.createElement('button');
      remove.type = 'button'; remove.textContent = '×';
      remove.setAttribute('aria-label', 'Убрать ' + option.textContent);
      remove.addEventListener('click', () => { option.selected = false; renderSelected(section); });
      item.append(name, remove); section.list.append(item);
    }
    section.count.textContent = String(items.length);
    section.empty.hidden = items.length > 0;
  }
  for (const element of card.querySelectorAll('[data-link-section]')) {
    const key = element.dataset.linkSection;
    const select = element.querySelector('select[name="' + key + '"]');
    const section = {key, select, options:Array.from(select.options).filter(option => option.value),
      list:element.querySelector('[data-link-selected]'), empty:element.querySelector('[data-link-empty]'),
      count:element.querySelector('[data-link-count]'), button:element.querySelector('[data-link-add]')};
    sections.set(key, section);
    element.querySelector('[data-link-fallback]').hidden = true;
    element.querySelector('[data-link-enhanced]').hidden = false;
    renderSelected(section);
  }
  function updateCount() {
    count.textContent = 'Найдено: ' + visible.length + ' · Выбрано: ' + pending.size;
  }
  function renderOptions() {
    if (!active) return;
    const query = search.value.trim().toLocaleLowerCase();
    visible = active.options.filter(option => (!query || option.textContent.toLocaleLowerCase().includes(query)) &&
      filters.every(filter => !filter.value || (metadata[option.value]?.[filter.dataset.linkFilter] || []).includes(filter.value)));
    list.replaceChildren();
    for (const option of visible) {
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'checkbox'; input.value = option.value; input.checked = pending.has(option.value);
      const text = document.createElement('span');
      text.textContent = option.textContent + (metadata[option.value]?.active === false ? ' · неактивна' : '');
      input.addEventListener('change', () => {
        if (input.checked) pending.add(option.value); else pending.delete(option.value);
        updateCount();
      });
      label.append(input, text); list.append(label);
    }
    if (!visible.length) {
      const empty = document.createElement('p'); empty.textContent = 'Нет групп по выбранным условиям.'; list.append(empty);
    }
    updateCount();
  }
  card.addEventListener('click', event => {
    const button = event.target.closest('[data-link-add]');
    if (!button) return;
    active = sections.get(button.dataset.linkAdd);
    pending = new Set(selected(active).map(option => option.value));
    title.textContent = button.dataset.linkAdd === 'related_groups' ? 'Добавить связанные группы' : 'Добавить замещающие группы';
    search.value = ''; filters.forEach(filter => { filter.value = ''; });
    renderOptions(); dialog.showModal(); search.focus();
  });
  search.addEventListener('input', renderOptions);
  filters.forEach(filter => filter.addEventListener('change', renderOptions));
  dialog.querySelector('[data-link-select-visible]').addEventListener('click', () => {
    visible.forEach(option => pending.add(option.value)); renderOptions();
  });
  dialog.querySelector('[data-link-clear-visible]').addEventListener('click', () => {
    visible.forEach(option => pending.delete(option.value)); renderOptions();
  });
  dialog.querySelector('[data-link-apply]').addEventListener('click', () => {
    if (!active) return;
    active.options.forEach(option => { option.selected = pending.has(option.value); });
    active.select.dispatchEvent(new Event('change', {bubbles:true}));
    renderSelected(active); dialog.close();
  });
  dialog.querySelectorAll('[data-link-cancel]').forEach(button => button.addEventListener('click', () => dialog.close()));
  dialog.addEventListener('close', () => { active = null; pending = new Set(); });
})();
