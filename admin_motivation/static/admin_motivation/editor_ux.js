(() => {
 const table=document.getElementById('coefficient-table');
 if (!table) return;
 // Keep the real controls; moving them preserves edits and form ownership.
 const panel=document.createElement('dialog');panel.className='motivation-side-panel';
 const heading=document.createElement('h2'), close=document.createElement('button');
 close.type='button';close.className='btn btn-outline-primary';close.textContent='Закрыть настройки';
 const body=document.createElement('div');panel.append(heading,close,body);document.body.append(panel);
 let current=null, anchor=null;
 table.addEventListener('click',event=>{
  const summary=event.target.closest('.brand-settings > summary');if (!summary) return;
  event.preventDefault(); current=summary.parentElement;
  anchor=document.createElement('span');current.before(anchor);
  for (const control of current.querySelectorAll('input,select,button')) {
   if (control.form) {control.setAttribute('form',control.form.id);control.disabled=control.matches(':disabled');}
  }
  heading.textContent=`Настройки товара · ${current.closest('tr').dataset.brand}`;
  body.append(current);current.open=true;panel.showModal();
 });
 close.addEventListener('click',()=>panel.close());
 panel.addEventListener('close',()=>{if(current){current.open=false;anchor.replaceWith(current);current.querySelector('summary').focus();current=null;}});
 // Group headers keep large matrices readable and can collapse each classification.
 const rows=[...table.querySelectorAll('.coefficient-row')];let lastKey=null,header=null;
 for (const row of rows) {
  const key=`${row.dataset.pg}\u0000${row.dataset.group}`;
  if(key!==lastKey){
   header=document.createElement('tr');header.className='coefficient-group-heading';
   const cell=document.createElement('td');cell.colSpan=[...row.cells].reduce((sum,td)=>sum+td.colSpan,0);
   const button=document.createElement('button');button.type='button';button.setAttribute('aria-expanded','true');
   button.textContent=`▾ ${row.dataset.pg} · ${row.dataset.group}`;
   header.dataset.groupKey=key;cell.append(button);header.append(cell);row.before(header);lastKey=key;

  }
  row.dataset.uxGroup=key;
 }
 for(const group of table.querySelectorAll('.coefficient-group-heading')) {
  const button=group.querySelector('button');const members=rows.filter(row=>row.dataset.uxGroup===group.dataset.groupKey);
  const toggle=button;
  toggle.addEventListener('click',()=>{
   const collapsed=toggle.getAttribute('aria-expanded')==='true';toggle.setAttribute('aria-expanded',String(!collapsed));
   toggle.textContent=(collapsed?'▸':'▾')+toggle.textContent.slice(1);
   members.forEach(row=>row.dataset.groupCollapsed=collapsed?'yes':'');
   document.getElementById('coefficient-search').dispatchEvent(new Event('input'));
  });
 }
 const legend=document.createElement('details');legend.className='coefficient-legend';
 legend.innerHTML='<summary>Как читать таблицу и ставки</summary><p>0% — выплата по этой ставке равна нулю. Недоступное поле — ставки нет или версия открыта для просмотра. K0–K4 — ставки сегментов мотивации; пороги скидок O0–O4 задаются в «Сегментах скидок». Жёлтым отмечены несохранённые правки. Формула выбирает, по какому объёму проверяется выполнение плана.</p>';
 table.closest('.table-wrapper').before(legend);
 const help=select=>{
  const box=document.createElement('p');box.className='formula-explanation';box.setAttribute('aria-live','polite');select.after(box);
  const update=()=>{
   const label=select.selectedOptions[0]?.textContent || '';
   box.textContent=label.includes('Глобально')?'Пример: план подразделения выполнен на 85% по всем товарам и авторам выбранного вида работника. Выплата рассчитывается за личный объём сотрудника.':label.includes('Подразделение')?'Выполнение плана проверяется на уровне подразделения по правилам выбранной формулы; премия начисляется за личный объём.':`Выполнение плана проверяется в разрезе «${label}». Выплата рассчитывается за личный объём сотрудника.`;
  };select.addEventListener('change',update);update();
 };
 document.querySelectorAll('.select-var-calc,#bulk-variation-form select[name=variation]').forEach(help);
 const manager=document.getElementById('manager-details');
 if(manager){const example=document.createElement('p');example.className='formula-explanation';example.textContent='Итоговая мотивация = рассчитанная сумма × поправка выбранного квартала или года. Например: 10 000 × 90% = 9 000. Поправка применяется ко всем видам работников, включая директоров.';manager.querySelector('form').prepend(example);}
})();
