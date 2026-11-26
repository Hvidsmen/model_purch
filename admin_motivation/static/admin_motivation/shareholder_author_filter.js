(() => {
 const payload = document.getElementById('shareholder-author-subdivisions');
 const form = document.getElementById('native-report-filters');
 if (!payload || !form) return;
 const affiliations = JSON.parse(payload.textContent);
 const branches = [...form.querySelectorAll('input[name=subdivision]')];
 const select = form.querySelector('select[name=author]');
 const checks = [...form.querySelectorAll('input[name=author]')];
 const update = () => {
  const selected = new Set(branches.filter(input => input.checked).map(input => input.value));
  const allowed = author => !selected.size || (affiliations[author] || []).some(branch => selected.has(branch));
  if (select) {
   const previous = select.value;
   const options = [new Option('Выберите сотрудника', '')];
   Object.keys(affiliations).filter(allowed).sort((a,b) => a.localeCompare(b,'ru')).forEach(author => options.push(new Option(author,author)));
   select.replaceChildren(...options);
   select.value = allowed(previous) ? previous : '';
   if (select.value !== previous) select.dispatchEvent(new Event('change', {bubbles:true}));
  }
  checks.forEach(input => {
   input.disabled = !allowed(input.value);
   const menu = input.closest('.next-products');
   const search = menu?.querySelector('[data-product-search]')?.value.trim().toLocaleLowerCase('ru') || '';
   input.closest('label').hidden = input.disabled || !input.value.toLocaleLowerCase('ru').includes(search);
   if (input.disabled && input.checked) {
    input.checked = false;
    input.dispatchEvent(new Event('change', {bubbles:true}));
   }
  });
 };
 branches.forEach(input => input.addEventListener('change', update));
 form.querySelectorAll('[data-next-select-all],[data-next-clear-all]').forEach(button => button.addEventListener('click', () => setTimeout(update, 0)));
 update();
})();
