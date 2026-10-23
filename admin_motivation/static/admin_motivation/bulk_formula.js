(() => {
 const form = document.getElementById('bulk-variation-form');
 if (!form) return;
 const boxes = [...document.querySelectorAll('.bulk-good')];
 const count = () => { form.querySelector('[data-bulk-count]').textContent = `Выбрано: ${boxes.filter(box => box.checked).length}`; };
 boxes.forEach(box => box.addEventListener('change', count));
 form.querySelector('[data-bulk-visible]').addEventListener('click', () => { boxes.forEach(box => { if (!box.closest('tr').hidden) box.checked = true; }); count(); });
 form.querySelector('[data-bulk-clear]').addEventListener('click', () => { boxes.forEach(box => box.checked = false); count(); });
 form.addEventListener('submit', event => {
  const dirty = document.querySelector('.editor-changed');
  if (dirty) { event.preventDefault(); alert('Сначала сохраните или отмените правки коэффициентов. Затем примените формулу к выбранным товарам.'); }
 });
})();
