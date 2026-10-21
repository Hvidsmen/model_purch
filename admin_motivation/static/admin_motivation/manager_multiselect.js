(() => {
 const fields = document.querySelectorAll('.manager-multi-field');
 fields.forEach(field => {
  const select = field.querySelector('select');
  const dropdown = field.querySelector('details');
  const menu = field.querySelector('.manager-multi-menu');
  const caption = field.querySelector('[data-multi-caption]');
  const inputs = [...field.querySelectorAll('input[type=checkbox]')];
  const labels = [...field.querySelectorAll('.manager-multi-options label')];
  const search = field.querySelector('input[type=search]');
  let dirty = false;
  const sync = () => {
   const checked = inputs.filter(input => input.checked);
   caption.textContent = !checked.length || checked.length === inputs.length ? 'Все' : checked.length === 1 ? checked[0].closest('label').textContent.trim() : `Выбрано: ${checked.length}`;
   [...select.options].forEach((option, index) => option.selected = inputs[index].checked);
  };
  const commit = () => {
   if (!dirty) return;
   dirty = false;
   // An empty optional list means all, so avoid sending thousands of values.
   if (inputs.length && inputs.every(input => input.checked)) [...select.options].forEach(option => option.selected = false);
   select.dispatchEvent(new Event('change', {bubbles: true}));
  };
  inputs.forEach(input => input.addEventListener('change', () => { dirty = true; sync(); }));
  field.querySelector('[data-multi-all]').addEventListener('click', () => { inputs.forEach(input => input.checked = true); dirty = true; sync(); });
  field.querySelector('[data-multi-clear]').addEventListener('click', () => { inputs.forEach(input => input.checked = false); dirty = true; sync(); });
  field.querySelector('[data-multi-apply]').addEventListener('click', () => { dropdown.open = false; commit(); });
  search.addEventListener('input', () => {
   const text = search.value.trim().toLocaleLowerCase();
   labels.forEach(label => label.hidden = !label.textContent.toLocaleLowerCase().includes(text));
  });
  dropdown.addEventListener('toggle', () => {
   if (!dropdown.open) { commit(); return; }
   fields.forEach(other => { if (other !== field) other.querySelector('details').open = false; });
   search.value = ''; labels.forEach(label => label.hidden = false);
   menu.style.left = '0'; menu.style.right = 'auto';
   if (menu.getBoundingClientRect().right > innerWidth - 12) { menu.style.left = 'auto'; menu.style.right = '0'; }
   search.focus();
  });
  dropdown.addEventListener('keydown', event => {
   if (event.key === 'Escape') { dropdown.open = false; field.querySelector('summary').focus(); }
   if (event.key === 'Enter' && event.target === search) { event.preventDefault(); dropdown.open = false; commit(); }
  });
  document.addEventListener('click', event => { if (!field.contains(event.target)) dropdown.open = false; });
  select.form?.addEventListener('submit', () => {
   if (dirty && dropdown.dataset.field === 'subdivision' && select.form.elements.author?.tagName === 'SELECT') [...select.form.elements.author.options].forEach(option => option.selected = false);
   dirty = false;
   dropdown.open = false;
   if (inputs.length && inputs.every(input => input.checked)) [...select.options].forEach(option => option.selected = false);
  });
  sync();
 });
})();
