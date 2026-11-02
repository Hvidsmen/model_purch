(() => {
 const form = document.querySelector('#native-report-filters[data-kind="deals"]');
 if (!form) return;
 const region = form.querySelector('[data-deal-filter-fields]');
 const status = form.querySelector('[data-deal-filter-status]');
 const submit = form.querySelector('button[name=run]');
 const initialFields = region.innerHTML;
 const primary = new Set(['start', 'end', 'type', 'subdivision']);
 const dependent = new Set(['channel', 'author', 'planning', 'goods', 'brand', 'partner']);
 let timer, controller, generation = 0;
 const fields = () => [...region.querySelectorAll('.manager-multi-field')];
 const sync = field => {
  const select = field.querySelector('select');
  const checks = [...field.querySelectorAll('input[type=checkbox]')];
  const selected = checks.filter(input => input.checked);
  [...select.options].forEach((option, index) => { option.selected = checks[index].checked; });
  field.querySelector('[data-multi-caption]').textContent = !selected.length || selected.length === checks.length ? 'Все' : selected.length === 1 ? selected[0].nextElementSibling.textContent : `Выбрано: ${selected.length}`;
 };
 const initialize = () => fields().forEach(field => {
  sync(field);
  field.querySelector('[data-multi-apply]').textContent = 'Готово';
 });
 const commit = (field, notify = true) => {
  if (field.dataset.dirty !== 'true') return;
  field.dataset.dirty = 'false';
  const select = field.querySelector('select');
  const checks = [...field.querySelectorAll('input[type=checkbox]')];
  if (checks.length && checks.every(input => input.checked)) [...select.options].forEach(option => { option.selected = false; });
  if (notify) select.dispatchEvent(new Event('change', {bubbles:true}));
 };
 const scheduleRefresh = name => {
  clearTimeout(timer);
  controller?.abort();
  const requestGeneration = ++generation;
  if (primary.has(name)) {
   // Branch, worker type and dates define a new set of deals. Do not carry a
   // manager or product selection from a different source scope into that set.
   fields().forEach(field => {
    const select = field.querySelector('select');
    if (!dependent.has(select.name)) return;
    field.querySelectorAll('input[type=checkbox]').forEach(input => { input.checked = false; });
    field.dataset.dirty = 'false'; sync(field);
   });
  }
  status.textContent = 'Обновляем варианты фильтров…';
  status.removeAttribute('data-error');
  form.setAttribute('aria-busy', 'true');
  submit.disabled = true;
  timer = setTimeout(async () => {
   controller = new AbortController();
   const params = new URLSearchParams(new FormData(form));
   ['run', 'page', 'export', 'execution', 'refresh_choices'].forEach(key => params.delete(key));
   params.set('_choices', '1');
   const url = new URL(form.action || location.href);
   url.search = params.toString(); url.hash = '';
   try {
    const response = await fetch(url, {credentials:'same-origin', signal:controller.signal, headers:{Accept:'application/json'}});
    if (!response.headers.get('Content-Type')?.includes('application/json')) throw new Error('Не удалось обновить списки. Проверьте подключение и вход в портал.');
    const data = await response.json();
    if (requestGeneration !== generation) return;
    if (!response.ok || data.errors || typeof data.html !== 'string') throw new Error(data.errors?.join(' ') || 'Не удалось обновить списки. Повторите выбор.');
    region.innerHTML = data.html;
    initialize();
    status.textContent = primary.has(name) ? 'Списки обновлены. Зависимый выбор сброшен; выберите нужные значения.' : 'Списки обновлены.';
   } catch (error) {
    if (error.name !== 'AbortError' && requestGeneration === generation) {
     status.textContent = error.message || 'Не удалось обновить списки. Повторите выбор.';
     status.dataset.error = 'true';
    }
   } finally {
    if (requestGeneration === generation) { form.removeAttribute('aria-busy'); submit.disabled = false; }
   }
  }, 250);
 };
 form.addEventListener('change', event => {
  const field = event.target.closest('.manager-multi-field');
  if (event.target.matches('input[type=checkbox]') && field) { field.dataset.dirty = 'true'; sync(field); return; }
  if (event.target.matches('select[name], input[type=date]') && event.target.name) scheduleRefresh(event.target.name);
 });
 form.addEventListener('input', event => {
  const field = event.target.closest('.manager-multi-field');
  if (!field || !event.target.matches('input[type=search]')) return;
  const text = event.target.value.trim().toLocaleLowerCase('ru');
  field.querySelectorAll('.manager-multi-options label').forEach(label => { label.hidden = !label.textContent.toLocaleLowerCase('ru').includes(text); });
 });
 form.addEventListener('click', event => {
  const field = event.target.closest('.manager-multi-field');
  if (!field) return;
  if (event.target.closest('[data-multi-all], [data-multi-clear]')) {
   const checked = !!event.target.closest('[data-multi-all]');
   field.querySelectorAll('input[type=checkbox]').forEach(input => { input.checked = checked; });
   field.dataset.dirty = 'true'; sync(field);
  }
  if (event.target.closest('[data-multi-apply]')) { commit(field); field.querySelector('details').open = false; }
 });
 form.addEventListener('toggle', event => {
  if (!event.target.matches('.manager-multi-filter')) return;
  const field = event.target.closest('.manager-multi-field');
  if (!event.target.open) { commit(field); return; }
  fields().forEach(other => { if (other !== field) other.querySelector('details').open = false; });
  const search = field.querySelector('input[type=search]');
  search.value = ''; field.querySelectorAll('.manager-multi-options label').forEach(label => { label.hidden = false; });
  const menu = field.querySelector('.manager-multi-menu');
  menu.style.left = '0'; menu.style.right = 'auto';
  if (menu.getBoundingClientRect().right > innerWidth - 12) { menu.style.left = 'auto'; menu.style.right = '0'; }
  search.focus();
 }, true);
 form.addEventListener('keydown', event => {
  const field = event.target.closest('.manager-multi-field');
  if (!field) return;
  if (event.key === 'Escape' || event.key === 'Enter' && event.target.matches('input[type=search]')) {
   event.preventDefault(); commit(field); field.querySelector('details').open = false; field.querySelector('summary').focus();
  }
 });
 document.addEventListener('click', event => fields().forEach(field => { if (!field.contains(event.target)) field.querySelector('details').open = false; }));
 form.addEventListener('submit', () => {
  clearTimeout(timer); controller?.abort(); ++generation;
  fields().forEach(field => { commit(field, false); field.querySelector('details').open = false; });
 });
 form.addEventListener('reset', () => {
  clearTimeout(timer); controller?.abort(); ++generation;
  queueMicrotask(() => {
   region.innerHTML = initialFields; initialize();
   form.removeAttribute('aria-busy'); submit.disabled = false; status.textContent = '';
   form.querySelector('input[type=date]').dispatchEvent(new Event('input', {bubbles:true}));
  });
 });
 initialize();
})();
