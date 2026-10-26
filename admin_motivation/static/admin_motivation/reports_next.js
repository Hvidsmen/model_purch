(() => {
 const branches = document.querySelector('.next-branches:not(.next-periods):not(.next-types)');
 const checks = [...(branches?.querySelectorAll('input[type=checkbox]') || [])];
 branches?.querySelector('[data-next-select-all]')?.addEventListener('click', () => checks.forEach(input => { input.checked = true; }));
 branches?.querySelector('[data-next-clear-all]')?.addEventListener('click', () => checks.forEach(input => { input.checked = false; }));
 branches?.querySelector('[data-next-branch-search]')?.addEventListener('input', event => {
  const text = event.target.value.trim().toLocaleLowerCase('ru');
  checks.forEach(input => { input.closest('label').hidden = !input.value.toLocaleLowerCase('ru').includes(text); });
 });
 const updateBranches = () => {
  const selected = checks.filter(input => input.checked);
  const caption = branches?.querySelector('[data-branch-caption]');
  if (caption) caption.textContent = !selected.length || selected.length === checks.length ? 'Все доступные подразделения' : selected.length <= 2 ? selected.map(input => input.value).join(', ') : `Подразделений: ${selected.length}`;
 };
 checks.forEach(input => input.addEventListener('change', updateBranches));
 branches?.querySelector('[data-next-select-all]')?.addEventListener('click', updateBranches);
 branches?.querySelector('[data-next-clear-all]')?.addEventListener('click', updateBranches);
 const types = document.querySelector('.next-types');
 const typeChecks = [...(types?.querySelectorAll('input[name=type]') || [])];
 const updateTypes = () => {
  const selected = typeChecks.filter(input => input.checked);
  const caption = types?.querySelector('[data-type-caption]');
  if (caption) caption.textContent = !selected.length || selected.length === typeChecks.length ? 'Все доступные виды' : selected.map(input => input.value).join(', ');
 };
 types?.querySelector('[data-type-all]')?.addEventListener('click', () => { typeChecks.forEach(input => { input.checked = true; }); updateTypes(); });
 types?.querySelector('[data-type-clear]')?.addEventListener('click', () => { typeChecks.forEach(input => { input.checked = false; }); updateTypes(); });
 typeChecks.forEach(input => input.addEventListener('change', updateTypes));
 const periods = document.querySelector('.next-periods');
 const periodChecks = [...(periods?.querySelectorAll('input[name=period]') || [])];
 const updatePeriods = () => {
  const selected = periodChecks.filter(input => input.checked);
  const caption = periods?.querySelector('[data-next-period-caption]');
  if (caption) caption.textContent = !selected.length || selected.length === periodChecks.length ? 'Все доступные периоды' : selected.map(input => input.dataset.caption).join(', ');
 };
 periods?.querySelector('[data-period-all]')?.addEventListener('click', () => { periodChecks.forEach(input => { input.checked = true; }); updatePeriods(); });
 periods?.querySelector('[data-period-clear]')?.addEventListener('click', () => { periodChecks.forEach(input => { input.checked = false; }); updatePeriods(); });
 periodChecks.forEach(input => input.addEventListener('change', updatePeriods));
 document.addEventListener('click', event => {
  document.querySelectorAll('details.next-branches[open]').forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
 });
 document.querySelectorAll('details.next-branches').forEach(menu => menu.addEventListener('keydown', event => {
  if (event.key === 'Escape') { menu.open = false; menu.querySelector('summary').focus(); }
 }));
 const matrixDetails = document.querySelector('[data-matrix-details]');
 matrixDetails?.addEventListener('change', () => {
  document.querySelectorAll('.nr-matrix[data-matrix-periods]').forEach(matrix => {
   const count = Number(matrix.dataset.matrixPeriods);
   matrix.dataset.mode = matrixDetails.checked ? 'detail' : 'basic';
   matrix.style.setProperty('--nr-matrix-metrics', count * (matrixDetails.checked ? 4 : 2) + (matrixDetails.checked ? 6 : 4));
  });
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
  typeChecks.forEach(input => { input.checked = false; });
  updateTypes();
  checks.forEach(input => { input.checked = false; });
  updateBranches();
  periodChecks.forEach(input => { if (['1', '2', '3'].includes(input.value)) input.checked = false; });
  updatePeriods();
  const author = document.querySelector('.next-filter input[name=author]');
  if (author) author.value = '';
 });
 const openDeep = () => {
  if (location.hash === '#next-deep-calculation') {
   const deep = document.getElementById('next-deep-calculation');
   if (deep) { deep.open = true; requestAnimationFrame(() => deep.scrollIntoView()); }
  }
 };
 window.addEventListener('hashchange', openDeep);
 document.addEventListener('DOMContentLoaded', () => {
  openDeep();
  if (location.hash === '#next-deep-calculation') {
   const extra = document.getElementById('manager-detail-extra');
   if (extra && !extra.checked) { extra.checked = true; extra.dispatchEvent(new Event('change')); }
   const segments = document.querySelector('[data-manager-reveal=segments]');
   if (segments && segments.getAttribute('aria-expanded') !== 'true') segments.click();
  }
 });
 openDeep();
 const toggle = document.getElementById('next-unallocated-toggle');

 const filter = () => {
  document.querySelectorAll('[data-next-person-row]').forEach(row => {
   row.hidden = (row.hasAttribute('data-next-unallocated') && !toggle?.checked);
  });
 };
 toggle?.addEventListener('change', filter);
})();
