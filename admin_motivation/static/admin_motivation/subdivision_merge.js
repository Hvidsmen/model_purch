(() => {
 const form = document.querySelector('.merge-form');
 if (!form) return;
 const sources = [...form.querySelectorAll('input[name=source]')];
 const policy = form.querySelector('[name=policy]');
 const priority = form.querySelector('[name=priority_source]');
 const target = form.querySelector('[name=target]');
 function update() {
  const selected = sources.filter(input => input.checked && input.value !== target.value).map(input => input.value);
  document.getElementById('merge-source-count').textContent = selected.length;
  sources.forEach(input => { input.disabled = input.value === target.value; });
  document.getElementById('merge-priority-field').hidden = policy.value !== 'source';
  priority.required = policy.value === 'source';
  [...priority.options].forEach(option => { if (option.value) option.hidden = !selected.includes(option.value); });
  if (!selected.includes(priority.value)) priority.value = selected.length === 1 ? selected[0] : '';
 }
 form.querySelectorAll('select, input[name=source]').forEach(input => input.addEventListener('change', () => {
  if (input === target) sources.forEach(source => { if (source.value === target.value) source.checked = false; });
  update();
  const review = document.getElementById('merge-review');
  if (review) {
   review.hidden = true;
   const token = review.querySelector('[name=preview_token]');
   if (token) token.value = '';
   document.getElementById('merge-review-status').textContent = 'Параметры изменились. Проверьте объединение повторно.';
  }
 }));
 document.getElementById('merge-source-search').addEventListener('input', event => {
  const query = event.target.value.trim().toLocaleLowerCase('ru');
  form.querySelectorAll('[data-merge-source-label]').forEach(label => { label.hidden = !label.textContent.toLocaleLowerCase('ru').includes(query); });
 });
 update();
})();
