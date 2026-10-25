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
 const toggle = document.getElementById('next-unallocated-toggle');
 toggle?.addEventListener('change', () => {
  document.querySelectorAll('[data-next-unallocated]').forEach(row => { row.hidden = !toggle.checked; });
 });
})();
