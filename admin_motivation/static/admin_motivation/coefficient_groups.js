(() => {
 const table = document.getElementById('coefficient-table');
 const toggles = [...document.querySelectorAll('[data-coefficient-group-toggle]')];
 const key = 'motivation-coefficient-groups-' + (document.getElementById('subCoeffForm') ? 'subdivision' : 'global');
 const apply = () => {
  const visible = {};
  toggles.forEach(toggle => {
   visible[toggle.dataset.coefficientGroupToggle] = toggle.checked;
   table?.querySelectorAll(`[data-coefficient-group="${toggle.dataset.coefficientGroupToggle}"]`).forEach(cell => cell.hidden = !toggle.checked);
  });
  try { localStorage.setItem(key, JSON.stringify(visible)); } catch (_) {}
 };
 let saved = {};
 try { saved = JSON.parse(localStorage.getItem(key) || '{}'); } catch (_) {}
 toggles.forEach(toggle => {
  if (typeof saved[toggle.dataset.coefficientGroupToggle] === 'boolean') toggle.checked = saved[toggle.dataset.coefficientGroupToggle];
  toggle.addEventListener('change', apply);
 });
 if (toggles.length) apply();
 const dialog = document.getElementById('copy-global-dialog');
 const open = document.getElementById('copy-global-open');
 const group = document.getElementById('copy-coefficient-group');
 const employee = document.getElementById('copy-director-select');
 const update = () => { const directors = group.value === 'directors'; document.getElementById('copy-director-employee').hidden = !directors; employee.required = directors; };
 if (dialog && open) {
  open.addEventListener('click', () => { update(); dialog.showModal(); });
  group.addEventListener('change', update);
  document.getElementById('copy-global-cancel').addEventListener('click', () => dialog.close());
 }
 // Director coefficients are not yet part of plan comparison. Save them before approval.
 document.getElementById('approval-review')?.addEventListener('click', event => {
  if ([...document.querySelectorAll('input[name^="director_coeff="]')].some(input => input.classList.contains('editor-changed'))) {
   event.preventDefault(); event.stopImmediatePropagation();
   const error = document.getElementById('editor-error');
   if (error) { error.hidden = false; error.textContent = 'Сначала сохраните изменения коэффициентов директоров, затем утвердите общий набор.'; }
  }
 }, true);
})();
