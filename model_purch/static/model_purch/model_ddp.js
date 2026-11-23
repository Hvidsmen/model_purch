(() => {
 const root=document.querySelector('.model-ddp');if(!root)return;
 const diagnosticsKey='model-ddp-load:'+root.dataset.calculationId;
 try{const stored=sessionStorage.getItem(diagnosticsKey),saved=root.querySelector('[data-saved-load-diagnostics]');if(stored&&saved){saved.hidden=false;saved.querySelector('p').textContent=stored;}}catch(_){}
 const initialStep=root.dataset.workflowStep;
 const setStep=(name,updateHistory=false)=>{
  if(name==='review')name='calculate';
  if(!['load','calculate'].includes(name))return;
  root.dataset.workflowStep=name;
  root.querySelectorAll('[data-step-panel]').forEach(panel=>panel.hidden=!panel.dataset.stepPanel.split(' ').includes(name));
  root.querySelectorAll('[data-calculate-only]').forEach(el=>el.hidden=name!=='calculate');
  root.querySelectorAll('[data-current-step]').forEach(el=>el.value=name);
  root.querySelectorAll('.ddp-workflow [data-step-link]').forEach(link=>{if(link.dataset.stepLink===name)link.setAttribute('aria-current','step');else link.removeAttribute('aria-current');});
  if(updateHistory){const url=new URL(window.location.href);url.searchParams.set('step',name);history.pushState({},'',url);}
  window.dispatchEvent(new Event('resize'));
 };
 setStep(root.dataset.workflowStep);
 root.addEventListener('click',event=>{const link=event.target.closest('[data-step-link]');if(!link||event.ctrlKey||event.metaKey||event.shiftKey)return;event.preventDefault();if(loading)return;setStep(link.dataset.stepLink,true);root.querySelector('.ddp-workflow')?.scrollIntoView({block:'start'});});
 window.addEventListener('popstate',()=>setStep(new URL(window.location.href).searchParams.get('step')||initialStep));
 const forms=[...root.querySelectorAll('[data-ddp-edit]')],status=root.querySelector('[data-ddp-dirty-status]');let leaving=false;
 const snapshot=form=>JSON.stringify([...new FormData(form)].filter(([key])=>key!=='csrfmiddlewaretoken'));
 const original=new Map(forms.map(form=>[form,snapshot(form)]));
 const dirty=()=>forms.filter(form=>snapshot(form)!==original.get(form)||root.dataset.rejectedEdits==='both'||root.dataset.rejectedEdits==='rows'&&form.matches('[data-ddp-rows]'));
 const savedStale=root.dataset.calculationStale==='1',note=root.querySelector('[data-recalculation-note]'),noteText=note?.textContent||'';
 function showDirty(){
  const changed=dirty().length>0,stale=savedStale||changed;
  if(status)status.textContent=changed?'Есть несохранённые параметры. «Рассчитать весь набор» сохранит их и выполнит расчёт.':'';
  root.classList.toggle('ddp-is-stale',stale);
  const state=root.querySelector('[data-calculation-status]');if(state)state.textContent=Number(root.dataset.priceCount)===0?'Сначала загрузите цены':changed?'Изменены параметры — требуется пересчёт':stale?'Требуется пересчёт':state.dataset.savedStatus;
  if(note){note.hidden=!changed&&(!savedStale||!root.dataset.lastCalculated);note.textContent=changed?'Параметры изменены. Старые результаты требуют пересчёта.':noteText;}
 }
 root.querySelector('[data-copy-source]')?.addEventListener('change',event=>{root.querySelectorAll('[data-copy-pane]').forEach(pane=>pane.hidden=pane.dataset.copyPane!==event.target.value);});
 root.addEventListener('input',showDirty);root.addEventListener('change',showDirty);showDirty();
 root.addEventListener('change',event=>{if(event.target.closest('[data-model-copy-options]'))root.querySelector('[data-model-copy-preview]')?.remove();});
 root.addEventListener('keydown',event=>{
  if(event.key==='Enter'&&event.target.matches('input:not([type=submit]):not([type=button])')&&event.target.form?.method.toLowerCase()==='post')event.preventDefault();
 });
 root.addEventListener('submit',event=>{
  if(event.target.method.toLowerCase()!=='post')return;
  const workflow=event.target.matches('[data-workflow-calculate]');
  if(!workflow&&dirty().some(form=>form!==event.target)){event.preventDefault();if(status)status.textContent='Сначала сохраните изменённые параметры в другой форме.';status?.scrollIntoView({block:'center'});return;}
  if(event.target.matches('[data-ddp-load]')&&window.fetch&&window.ReadableStream){
   event.preventDefault();loadPrices(event.target);return;
  }
  if(workflow){
   event.target.querySelector('[data-workflow-rows]')?.remove();
   const rows=root.querySelector('[data-ddp-rows]');
   if(rows){const hidden=document.createElement('div');hidden.dataset.workflowRows='';hidden.hidden=true;
    for(const [name,value] of new FormData(rows)){if(['csrfmiddlewaretoken','revision','action'].includes(name))continue;const input=document.createElement('input');input.type='hidden';input.name=name;input.value=value;hidden.append(input);}
    event.target.append(hidden);
   }
  }
  leaving=true;
 });
 let loading=false;
 async function loadPrices(form){
  if(loading)return;loading=true;
  const panel=root.querySelector('[data-load-status]'),stage=panel.querySelector('[data-load-stage]'),timer=panel.querySelector('[data-load-time]'),count=panel.querySelector('[data-load-count]'),error=panel.querySelector('[data-load-error]'),warning=panel.querySelector('[data-load-warning]'),bar=panel.querySelector('progress'),technical=panel.querySelector('[data-load-technical-stage]');
  panel.hidden=false;stage.textContent='Подключение к DataWH…';count.textContent='';error.textContent='';warning.textContent='';bar.removeAttribute('value');
  const buttons=[...root.querySelectorAll('button')].filter(button=>!button.disabled);buttons.forEach(button=>button.disabled=true);
  const started=Date.now(),interval=setInterval(()=>timer.textContent=' Время: '+Math.floor((Date.now()-started)/1000)+' с',1000);
  const show=item=>{
   if(item.error)throw new Error(item.error);
   if(item.stage){if(technical)technical.textContent=item.stage;stage.textContent=item.stage.includes('индекс')?'Подготовка загрузки…':item.stage.includes('сохранение')?'Сохраняем загруженные цены…':item.stage.includes('чтение результата')?'Получаем цены…':item.stage.includes('схем')||item.stage.includes('подготовка')?'Проверяем источник цен…':item.stage.includes('поставщик')?'Сопоставляем поставщиков…':'Загружаем цены…';}
   if(item.strategy&&technical)technical.textContent+=' · '+item.strategy;
   if(item.warning)warning.textContent+=(warning.textContent?'\n':'')+item.warning;
   if(item.rows!==undefined)count.textContent=' Получено вариантов: '+item.rows;
   if(item.done){bar.max=1;bar.value=1;stage.textContent='Загружено '+item.rows+' вариантов. Открываем цены…';return true;}return false;
  };
  try{
   const data=new FormData(form);data.set('action','load');
   const response=await fetch(form.dataset.streamUrl+window.location.search,{method:'POST',body:data,headers:{'X-Requested-With':'XMLHttpRequest'},credentials:'same-origin'});
   if(!response.ok){let message='Ошибка загрузки: HTTP '+response.status;try{message=(await response.json()).error||message;}catch(_){}throw new Error(message);}
   if(!response.headers.get('content-type')?.includes('application/x-ndjson'))throw new Error('Сессия завершена или сервер не поддерживает потоковый ответ. Обновите страницу.');
   const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',done=false;
   while(true){const part=await reader.read();buffer+=decoder.decode(part.value||new Uint8Array(),{stream:!part.done});let newline;
    while((newline=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,newline);buffer=buffer.slice(newline+1);if(line.trim())done=show(JSON.parse(line))||done;}
    if(part.done)break;
   }
   if(buffer.trim())done=show(JSON.parse(buffer))||done;
   if(!done)throw new Error('Соединение прервано. Обновите страницу и проверьте состояние снимка.');
   try{sessionStorage.setItem(diagnosticsKey,[technical?.textContent,warning.textContent].filter(Boolean).join('\n'));}catch(_){}
   leaving=true;
   window.location.assign(root.dataset.reviewUrl);
  }catch(exc){error.textContent='Не удалось загрузить цены. Подробности — в диагностике ниже.';if(technical)technical.textContent=exc.message;stage.textContent='Загрузка не завершена';buttons.forEach(button=>button.disabled=false);}
  finally{clearInterval(interval);loading=false;}
 }
 window.addEventListener('beforeunload',event=>{if(!leaving&&dirty().length){event.preventDefault();event.returnValue='';}});
})();
