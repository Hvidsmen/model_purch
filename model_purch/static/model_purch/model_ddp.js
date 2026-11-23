(() => {
 const root=document.querySelector('.model-ddp');if(!root)return;
 const forms=[...root.querySelectorAll('[data-ddp-edit]')],status=root.querySelector('[data-ddp-dirty-status]');let leaving=false;
 const snapshot=form=>JSON.stringify([...new FormData(form)].filter(([key])=>key!=='csrfmiddlewaretoken'));
 const original=new Map(forms.map(form=>[form,snapshot(form)]));
 const dirty=()=>forms.filter(form=>snapshot(form)!==original.get(form));
 root.addEventListener('input',()=>{if(status)status.textContent=dirty().length?'Есть несохранённые параметры. Сохраните их соответствующей кнопкой.':'';});
 root.addEventListener('keydown',event=>{
  if(event.key==='Enter'&&event.target.matches('input:not([type=submit]):not([type=button])')&&event.target.form?.method.toLowerCase()==='post')event.preventDefault();
 });
 root.addEventListener('submit',event=>{
  if(event.target.method.toLowerCase()!=='post')return;
  if(dirty().some(form=>form!==event.target)){event.preventDefault();if(status)status.textContent='Сначала сохраните изменённые параметры в другой форме.';status?.scrollIntoView({block:'center'});return;}
  if(event.target.matches('[data-ddp-load]')&&window.fetch&&window.ReadableStream){
   event.preventDefault();loadPrices(event.target);return;
  }
  leaving=true;
 });
 let loading=false;
 async function loadPrices(form){
  if(loading)return;loading=true;
  const panel=root.querySelector('[data-load-status]'),stage=panel.querySelector('[data-load-stage]'),timer=panel.querySelector('[data-load-time]'),count=panel.querySelector('[data-load-count]'),error=panel.querySelector('[data-load-error]'),warning=panel.querySelector('[data-load-warning]'),bar=panel.querySelector('progress');
  panel.hidden=false;stage.textContent='Подключение к DataWH…';count.textContent='';error.textContent='';warning.textContent='';bar.removeAttribute('value');
  const buttons=[...root.querySelectorAll('button')].filter(button=>!button.disabled);buttons.forEach(button=>button.disabled=true);
  const started=Date.now(),interval=setInterval(()=>timer.textContent=' Время: '+Math.floor((Date.now()-started)/1000)+' с',1000);
  const show=item=>{
   if(item.error)throw new Error(item.error);
   if(item.stage)stage.textContent=item.stage;
   if(item.strategy)count.textContent=item.strategy;
   if(item.warning)warning.textContent+=(warning.textContent?'\n':'')+item.warning;
   if(item.rows!==undefined)count.textContent=' Получено вариантов: '+item.rows;
   if(item.done){bar.max=1;bar.value=1;stage.textContent='Загружено '+item.rows+' вариантов. Обновите таблицу.';return true;}return false;
  };
  try{
   const data=new FormData(form);data.set('action','load');
   const response=await fetch(form.dataset.streamUrl,{method:'POST',body:data,headers:{'X-Requested-With':'XMLHttpRequest'},credentials:'same-origin'});
   if(!response.ok){let message='Ошибка загрузки: HTTP '+response.status;try{message=(await response.json()).error||message;}catch(_){}throw new Error(message);}
   if(!response.headers.get('content-type')?.includes('application/x-ndjson'))throw new Error('Сессия завершена или сервер не поддерживает потоковый ответ. Обновите страницу.');
   const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',done=false;
   while(true){const part=await reader.read();buffer+=decoder.decode(part.value||new Uint8Array(),{stream:!part.done});let newline;
    while((newline=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,newline);buffer=buffer.slice(newline+1);if(line.trim())done=show(JSON.parse(line))||done;}
    if(part.done)break;
   }
   if(buffer.trim())done=show(JSON.parse(buffer))||done;
   if(!done)throw new Error('Соединение прервано. Обновите страницу и проверьте состояние снимка.');
   const link=document.createElement('a');link.href=window.location.href;link.textContent='Показать загруженные цены';link.className='btn btn-primary';panel.append(link);
   // Revision changed; prevent another write using the old page.
   buttons.forEach(button=>{if(button.form?.method.toLowerCase()==='get')button.disabled=false;});
  }catch(exc){error.textContent=exc.message;stage.textContent='Загрузка не завершена';buttons.forEach(button=>button.disabled=false);}
  finally{clearInterval(interval);loading=false;}
 }
 window.addEventListener('beforeunload',event=>{if(!leaving&&dirty().length){event.preventDefault();event.returnValue='';}});
})();
