(() => {
 document.querySelectorAll('[data-access-multi]').forEach(field => {
  const dropdown = field.querySelector('details');
  const checks = [...field.querySelectorAll('input[type=checkbox]')];
  const labels = [...field.querySelectorAll('[data-choice-label]')];
  const caption = field.querySelector('[data-choice-caption]');
  const search = field.querySelector('input[type=search]');
  const sync = () => {
   const selected = checks.filter(input => input.checked);
   caption.textContent = !selected.length ? 'Ничего не выбрано' : selected.length === 1 ? selected[0].closest('label').textContent.trim() : `Выбрано: ${selected.length}`;
  };
  checks.forEach(input => input.addEventListener('change', sync));
  field.querySelector('[data-choice-all]').addEventListener('click', () => { checks.forEach(input => input.checked=true); sync(); checks[0]?.dispatchEvent(new Event('change', {bubbles:true})); });
  field.querySelector('[data-choice-clear]').addEventListener('click', () => { checks.forEach(input => input.checked=false); sync(); checks[0]?.dispatchEvent(new Event('change', {bubbles:true})); });
  field.querySelector('[data-choice-close]').addEventListener('click', () => dropdown.open=false);
  search.addEventListener('input', () => { const text=search.value.trim().toLocaleLowerCase(); labels.forEach(label => label.hidden=!label.textContent.toLocaleLowerCase().includes(text)); });
  dropdown.addEventListener('toggle', () => {
   if (!dropdown.open) return;
   document.querySelectorAll('.access-multi[open]').forEach(other => { if(other!==dropdown) other.open=false; });
   search.focus();
  });
  document.addEventListener('click', event => { if(!field.contains(event.target)) dropdown.open=false; });
  dropdown.addEventListener('keydown', event => { if(event.key==='Escape') dropdown.open=false; if(event.key==='Enter' && event.target===search) {event.preventDefault();dropdown.open=false;} });
  sync();
 });
 document.getElementById('group-search')?.addEventListener('input', event => { const text=event.target.value.trim().toLocaleLowerCase(); document.querySelectorAll('[data-group-card]').forEach(card => card.hidden=!card.dataset.search.includes(text)); });
})();
