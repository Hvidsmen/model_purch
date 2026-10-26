/* Staged rate operations: preview is read-only; only the normal draft save writes data. */
(() => {
 const root = document.getElementById('bulk-multiply-controls');
 if (!root) return;
 const el = id => document.getElementById(`multiply-${id}`);
 const boxes = [...document.querySelectorAll('.bulk-good')];
 const dialog = el('review');
 let pending = [], selection = '';
 const names = {policies:'Политики',sales:'Продажи',directors:'Директор'};
 const count = () => { el('count').textContent = `Выбрано: ${boxes.filter(box => box.checked).length}`; };
 const number = (text, percentage = false) => {
  const value = text.trim().replace(',', '.').replace('−','-');
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?%?$/.test(value) || !percentage && value.endsWith('%')) throw new Error('Введите число. Пустое значение не считается нулём.');
  const result = Number(value.replace(/%$/, '')) / (percentage && value.endsWith('%') ? 100 : 1);
  if (!Number.isFinite(result)) throw new Error('Введите конечное число.');
  return result;
 };
 const percent = value => ((value*100).toFixed(10).replace(/\.?0+$/,'')||'0')+'%';
 const showError = message => { el('error').textContent = message; el('error').hidden = false; };
 const signature = () => [el('operation').value, el('mode').value, el('common').value, ...[...root.querySelectorAll('[data-multiply-segment]')].map(input=>input.value), ...boxes.filter(box => box.checked).map(box => box.value), ...root.querySelectorAll('[data-multiply-kind]:checked:not(:disabled),[data-multiply-step]:checked')].map(item => typeof item === 'string' ? item : item.dataset.multiplyKind || item.dataset.multiplyStep).join('|');
 boxes.forEach(box => box.addEventListener('change', count));
 for (const selector of ['[data-bulk-visible]', '[data-bulk-clear]']) document.querySelector(`#bulk-variation-form ${selector}`)?.addEventListener('click', count);
 for (const [id, mode] of [['visible','visible'],['filtered','filtered'],['clear','clear']]) el(id).addEventListener('click', () => {
  boxes.forEach(box => {
   const row = box.closest('tr');
   if (mode === 'clear' || mode === 'visible' && !row.hidden || mode === 'filtered' && row.dataset.matchesFilter === 'yes') {
    box.checked = mode !== 'clear'; box.dispatchEvent(new Event('change', {bubbles:true}));
   }
  }); count();
 });
 const descriptions = {
  multiply:['Множитель','1,1 — увеличить ставки на 10%. Например: 0,20% → 0,22%.','1'],
  set:['Новая ставка, %','0,20 означает ставку 0,20%. Будут заменены только выбранные виды и ступени.',''],
  relative:['Изменение, %','10 — увеличить на 10%: 0,20% → 0,22%. −10 — уменьшить на 10%.',''],
  points:['Прибавить, п.п.','0,10 — прибавить 0,10 процентного пункта: 0,20% → 0,30%. Можно указать отрицательное число.',''],
  global:['','Берутся только точные глобальные ставки той же версии, товара, вида и ступени. Если исходной ставки нет, вся операция не применяется.','']
 };
 const controls = () => {
  const global = el('operation').value === 'global', separate = el('mode').value === 'segments';
  root.querySelector('label[data-multiply-operand]').hidden = global;
  el('segments').hidden = global || !separate; el('common-label').hidden = global || separate;
 };
 el('mode').addEventListener('change', controls);
 el('operation').addEventListener('change', () => {
  const [label, description, initial] = descriptions[el('operation').value];
  el('value-label').textContent = label; el('operation-help').textContent = description;
  el('common').value = initial;
  root.querySelectorAll('[data-multiply-segment]').forEach(input => { input.value = initial; input.setAttribute('aria-label', `${label} K${input.dataset.multiplySegment}`); });
  controls();
 });
 el('preview').addEventListener('click', () => {
  el('error').hidden = true; pending = [];
  try {
   const selected = boxes.filter(box => box.checked), operation = el('operation').value;
   if (!selected.length) throw new Error('Сначала отметьте товары в первом столбце.');
   const kinds = [...root.querySelectorAll('[data-multiply-kind]:checked:not(:disabled)')].map(box => box.dataset.multiplyKind);
   const steps = new Set([...root.querySelectorAll('[data-multiply-step]:checked')].map(box => Number(box.dataset.multiplyStep)));
   if (!kinds.length) throw new Error('Выберите хотя бы один вид мотивации.');
   if (!steps.size) throw new Error('Выберите хотя бы одну ступень K0–K4.');
   const operand = index => {
    if (operation === 'global') return null;
    let text = el('mode').value === 'common' ? el('common').value : root.querySelector(`[data-multiply-segment="${index}"]`).value;
    if(['set','relative'].includes(operation))text=text.trim().replace(/%$/,'');
    const value = number(text);
    if (operation === 'multiply' && value < 0) throw new Error('Множитель должен быть неотрицательным.');
    if (operation === 'relative' && value < -100) throw new Error('Уменьшение не может превышать 100%. Для смены знака задайте точную ставку.');
    return value;
   };
   const operands = [...Array(5)].map((_, index) => steps.has(index) ? operand(index) : null);
   let skipped = 0;
   for (const box of selected) {
    const row = box.closest('tr');
    if (row.querySelector('[name^="delete_coeff="]:checked')) { skipped++; continue; }
    for (const group of kinds) for (const cell of row.querySelectorAll(`[data-coefficient-group="${group}"]`)) {
     const input = cell.querySelector('.coeff-input');
     if (!input || input.matches(':disabled')) { skipped++; continue; }
     const named = input.dataset.segment?.trim().match(/^[KКOО]([0-4])$/iu);
     const index = input.dataset.segment ? named ? Number(named[1]) : NaN : Number(cell.dataset.coefficientSegment);
     if (!Number.isInteger(index) || index < 0 || index > 4) throw new Error('Не удалось определить ступень. Обновите страницу.');
     if (!steps.has(index)) continue;
     const before = number(input.value, true), value = operands[index];
     let raw;
     if (operation === 'multiply') raw = before * value;
     else if (operation === 'relative') raw = before * (1 + value / 100);
     else if (operation === 'points') raw = before + value / 100;
     else if (operation === 'set') raw = value / 100;
     else {
      if (!input.dataset.globalValue || input.dataset.globalValue === 'None') throw new Error(`${row.dataset.brand}, ${names[group]}, K${index}: нет точной глобальной ставки. Ни одна ячейка не изменена.`);
      raw = number(input.dataset.globalValue);
     }
     if (!Number.isFinite(raw) || Math.abs(raw) > 1 + 1e-12) throw new Error(`${row.dataset.brand}, ${names[group]}, K${index}: результат выходит за диапазон −100%…100%. Ни одна ячейка не изменена.`);
     const after = Number(Math.max(-1, Math.min(1, raw)).toFixed(group === 'directors' ? 6 : 12));
     if (Math.abs(after - before) > 1e-14) pending.push({input, original:input.value, value:percent(after), before:percent(before), goods:[row.dataset.pg,row.dataset.group,row.dataset.brand].join(' · '), kind:`${names[group]} / K${index}`});
    }
   }
   if (!pending.length) throw new Error('Нет изменяемых значений. Проверьте выбранные виды, ступени и доступность ставок.');
   selection = signature();
   el('review-context').textContent = document.querySelector('#editor-toolbar .editor-context')?.textContent.trim() || '';
   const hidden = selected.filter(box => box.closest('tr').hidden).length;
   el('review-summary').textContent = `Товаров выбрано: ${selected.length}, скрыто сейчас: ${hidden}. Изменится ячеек: ${pending.length}. Пропущено недоступных ячеек или удаляемых товаров: ${skipped}. Показаны первые 100 изменений; после применения все доступны в «Изменения».`;
   el('review-rows').replaceChildren();
   for (const change of pending.slice(0,100)) {
    const row = document.createElement('tr');
    for (const text of [change.goods,change.kind,change.before,change.value]) { const cell=document.createElement('td'); cell.textContent=text;row.append(cell); }
    el('review-rows').append(row);
   }
   dialog.showModal();
  } catch (error) { pending=[]; showError(error.message); }
 });
 el('cancel').addEventListener('click', () => dialog.close());
 el('apply').addEventListener('click', () => {
  if (!pending.length) return;
  if (selection !== signature() || pending.some(change => change.input.value !== change.original || change.input.matches(':disabled'))) { dialog.close();showError('Значения или выбор изменились после проверки. Проверьте операцию заново.');return; }
  if (!window.motivationEditor) { showError('Редактор не готов. Обновите страницу; изменения не применены.');return; }
  window.motivationEditor.apply(pending, el('operation').selectedOptions[0].textContent);
  el('status').textContent = `Изменено ячеек: ${pending.length}. Правки не сохранены — проверьте таблицу и нажмите «Сохранить изменения».`;
  pending=[]; dialog.close();
 });
 el('undo').addEventListener('click', () => window.motivationEditor?.undo());
 document.addEventListener('motivation:editor-state', event => { el('undo').disabled = !event.detail.canUndo; });
 controls(); count();
})();
