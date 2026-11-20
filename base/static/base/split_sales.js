(() => {
 const workspace=document.querySelector('.split-workspace');if(!workspace)return;
 const channelFilter=document.querySelector('[data-sales-channel-filter]');
 if(channelFilter){
  const choices=[...channelFilter.querySelectorAll('[data-sales-channel-choice]')];
  const storage='split-sales-channels:'+location.pathname,known=new Set(choices.map(input=>input.dataset.salesChannelChoice));
  try{const saved=JSON.parse(sessionStorage.getItem(storage)||'null');if(saved&&Array.isArray(saved.selected)&&Array.isArray(saved.known)){choices.forEach(input=>{input.checked=!saved.known.includes(input.dataset.salesChannelChoice)||saved.selected.includes(input.dataset.salesChannelChoice);});}}catch{}
  function applyChannels(save=false){
   const selected=new Set(choices.filter(input=>input.checked).map(input=>input.dataset.salesChannelChoice));
   workspace.querySelectorAll('[data-sales-channel],[data-plan-channel]').forEach(cell=>{const channel=cell.dataset.salesChannel||cell.dataset.planChannel;cell.toggleAttribute('data-sales-channel-filtered',known.has(channel)&&!selected.has(channel));});
   const caption=channelFilter.querySelector('[data-sales-channel-caption]');caption.textContent=selected.size===choices.length?'Все':selected.size?'Выбрано: '+selected.size:'Не выбраны';
   if(save){try{sessionStorage.setItem(storage,JSON.stringify({known:[...known],selected:[...selected]}));}catch{}}
   window.dispatchEvent(new Event('resize'));
  }
  choices.forEach(input=>input.addEventListener('change',()=>applyChannels(true)));
  channelFilter.querySelector('[data-sales-channels-all]').addEventListener('click',()=>{choices.forEach(input=>input.checked=true);applyChannels(true);});
  channelFilter.querySelector('[data-sales-channels-none]').addEventListener('click',()=>{choices.forEach(input=>input.checked=false);applyChannels(true);});
  channelFilter.querySelector('[data-sales-channel-search]').addEventListener('input',event=>{const text=event.target.value.trim().toLocaleLowerCase('ru');choices.forEach(input=>input.closest('label').hidden=!input.closest('label').textContent.toLocaleLowerCase('ru').includes(text));});
  document.addEventListener('click',event=>{if(!channelFilter.contains(event.target))channelFilter.open=false;});
  channelFilter.addEventListener('keydown',event=>{if(event.key==='Escape'){channelFilter.open=false;channelFilter.querySelector('summary').focus();}});
  applyChannels();
 }
 const dialog=document.querySelector('.split-sales-adjust-dialog');
 const revision=()=>document.querySelector('input[name=revision]')?.value||workspace.dataset.revision;
 const csrf=()=>document.querySelector('input[name=csrfmiddlewaretoken]')?.value;
 async function request(action,data){
  data.set('action',action);data.set('revision',revision());data.set('csrfmiddlewaretoken',csrf());
  const response=await fetch(location.href,{method:'POST',body:data});let result;
  try{result=JSON.parse(await response.text());}catch{throw Error('Сервер не вернул результат. Проверьте соединение и вход в портал.');}
  if(!response.ok||!result.ok)throw Error(result.error||'Не удалось выполнить действие.');return result;
 }
 document.querySelectorAll('[data-sales-expand]').forEach(button=>button.addEventListener('click',()=>{
  const open=button.getAttribute('aria-expanded')!=='true';button.setAttribute('aria-expanded',String(open));
  document.querySelectorAll('[data-sales-channel="'+button.dataset.salesExpand+'"]').forEach(row=>row.hidden=!open);
 }));
 if(dialog){
  const form=dialog.querySelector('form'),preview=dialog.querySelector('[data-sales-adjust-preview]'),error=dialog.querySelector('[role=alert]'),apply=dialog.querySelector('[data-sales-adjust-apply]');let token='',busy=false,initial={},generation=0;
  const reset=()=>{generation++;token='';preview.hidden=true;apply.hidden=true;error.textContent='';};
  const format=value=>value==null?'—':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:6}).format(Number(value));
  document.querySelectorAll('[data-brand-adjust]').forEach(button=>button.addEventListener('click',()=>{
   form.reset();reset();form.elements.brand.value=button.dataset.brandAdjust;form.elements.channel.value=button.dataset.channel;
   initial={quantity:button.dataset.currentQuantity,markup:button.dataset.currentMarkup};form.elements.quantity.value=initial.quantity;form.elements.markup.value=initial.markup;
   dialog.querySelector('h2').textContent=button.dataset.brandName+' · '+button.dataset.channelName;
   dialog.showModal();
  }));
  form.addEventListener('input',reset);form.addEventListener('change',reset);
  form.addEventListener('submit',async event=>{
   event.preventDefault();if(busy)return;busy=true;reset();const run=generation;const progress=window.SplitProgress?.start('Предпросмотр корректировки');
   form.querySelector('button').disabled=true;
   try{
    const data=new FormData(form);for(const key of ['quantity','markup'])if(data.get(key)===''||Number(data.get(key))===Number(initial[key])&&!(key==='quantity'&&data.get('method')==='equal'))data.set(key,'');
    const result=await request('brand_preview',data);if(run!==generation){progress?.finish('Параметры изменены. Проверьте изменения заново.');return;}token=result.token;preview.replaceChildren();
    const table=document.createElement('table');table.className='admin-table';
    const addRow=values=>{const row=table.insertRow();values.forEach(value=>{row.insertCell().textContent=value;});};
    addRow(['Показатель','Было','Станет','Изменение']);
    [['qty','План · компл.'],['revenue','Выручка · USD'],['markup','Наценка ×'],['cost','Себестоимость · USD'],['closing','Остаток на конец · весь бренд']].forEach(([key,label])=>addRow([label,format(result.before[key]),format(result.after[key]),result.before[key]==null||result.after[key]==null?'—':format(Number(result.after[key])-Number(result.before[key]))]));
    const description=document.createElement('p');description.textContent='Изменится групп: '+result.count+'. Закреплено строк: объём — '+result.locked_qty+', наценка — '+result.locked_markup+'. Распределение: '+(result.method==='equal'?'поровну':'по сохранённым пропорциям')+'.';
    preview.append(table,description);if(result.after.closing!=null&&Number(result.after.closing)<0){const warning=document.createElement('p');warning.className='alert alert-warning';warning.textContent='После корректировки возникнет дефицит товара: '+format(-Number(result.after.closing))+' компл. Проверьте поставки.';preview.append(warning);}const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='Изменения по группам';details.append(summary);
    result.changed.forEach(row=>{const p=document.createElement('p');p.textContent=row.name+': объём '+format(row.before_qty)+' → '+format(row.after_qty)+'; наценка '+format(row.before_markup)+' → '+format(row.after_markup);details.append(p);});preview.append(details);preview.hidden=false;apply.hidden=!result.count;progress?.finish('Предпросмотр готов. Проверьте и подтвердите изменения.');
   }catch(failure){error.textContent=failure.message;progress?.fail(failure.message);}finally{busy=false;form.querySelector('button').disabled=false;}
  });
  apply.addEventListener('click',async()=>{
   if(!token||busy)return;busy=true;apply.disabled=true;const progress=window.SplitProgress?.start('Применение корректировки');
   try{const data=new FormData();data.set('token',token);data.set('confirm','on');await request('brand_apply',data);progress?.finish('Корректировка сохранена.');window.SplitUnsaved.navigate(location.href);}
   catch(failure){error.textContent=failure.message;progress?.fail(failure.message);}finally{busy=false;apply.disabled=false;}
  });
  dialog.querySelector('[data-sales-adjust-cancel]').addEventListener('click',()=>{if(!busy)dialog.close();});dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
 }
 document.addEventListener('DOMContentLoaded',()=>{
  const target=document.querySelector('[data-sales-view-target]'),controls=document.querySelector('[data-table-view-controls]');if(target&&controls)target.append(controls);

  document.querySelector('[data-sales-history]')?.addEventListener('submit',event=>{
   if(document.querySelector('.split-unsaved-dock:not([hidden])')){event.preventDefault();window.SplitProgress?.start('Загрузка факта').fail('Сначала сохраните изменения плана.');}
   else window.SplitProgress?.start('Загрузка исторического факта');
  });
 });
})();
