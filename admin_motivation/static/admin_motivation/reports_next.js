(() => {
 const branches = document.querySelector('.next-branches:not(.next-periods)');
 const checks = [...(branches?.querySelectorAll('input[type=checkbox]') || [])];
 branches?.querySelector('[data-next-select-all]')?.addEventListener('click', () => checks.forEach(input => { input.checked = true; }));
 branches?.querySelector('[data-next-clear-all]')?.addEventListener('click', () => checks.forEach(input => { input.checked = false; }));
 branches?.querySelector('[data-next-branch-search]')?.addEventListener('input', event => {
  const text = event.target.value.trim().toLocaleLowerCase('ru');
  checks.forEach(input => { input.closest('label').hidden = !input.value.toLocaleLowerCase('ru').includes(text); });
 });
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
