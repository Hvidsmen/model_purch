(() => {
 const branches = document.querySelector('.next-branches');
 const checks = [...(branches?.querySelectorAll('input[type=checkbox]') || [])];
 branches?.querySelector('[data-next-select-all]')?.addEventListener('click', () => checks.forEach(input => { input.checked = true; }));
 branches?.querySelector('[data-next-clear-all]')?.addEventListener('click', () => checks.forEach(input => { input.checked = false; }));
 branches?.querySelector('[data-next-branch-search]')?.addEventListener('input', event => {
  const text = event.target.value.trim().toLocaleLowerCase('ru');
  checks.forEach(input => { input.closest('label').hidden = !input.value.toLocaleLowerCase('ru').includes(text); });
 });
 const execution = document.querySelector('.next-filter select[name=execution]');
 execution?.addEventListener('change', () => {
  const year = document.querySelector('.next-filter input[name=year]');
  if (year) year.value = execution.selectedOptions[0]?.dataset.year || '';
  const plan = document.querySelector('.next-filter select[name=plan]');
  if (plan) plan.value = '';
 });
 const preview = document.querySelector('.next-filter select[name=preview_user]');
 preview?.addEventListener('change', () => {
  const type = document.querySelector('.next-filter select[name=type]');
  if (type) type.value = '';
  checks.forEach(input => { input.checked = false; });
  const period = document.querySelector('.next-filter select[name=period]');
  if (period && ['1', '2', '3'].includes(period.value)) period.value = 'all';
  const author = document.querySelector('.next-filter input[name=author]');
  if (author) author.value = '';
 });
 const toggle = document.getElementById('next-unallocated-toggle');
 const search = document.querySelector('[data-next-person-search]');
 const filter = () => {
  const term = (search?.value || '').trim().toLocaleLowerCase('ru');
  document.querySelectorAll('[data-next-person-row]').forEach(row => {
   row.hidden = (row.hasAttribute('data-next-unallocated') && !toggle?.checked) || !row.textContent.toLocaleLowerCase('ru').includes(term);
  });
 };
 toggle?.addEventListener('change', filter);
 search?.addEventListener('input', filter);
})();
