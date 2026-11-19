(() => {
 const dialog=document.getElementById('split-forecast-history');if(!dialog)return;
 const find=name=>dialog.querySelector('[data-forecast-history-'+name+']'),status=find('status');
 let proposals=[],busy=false;
 const number=value=>value==null?'—':Number(value).toLocaleString('ru-RU',{maximumFractionDigits:6});
 const clear=()=>{proposals=[];find('results').replaceChildren();find('apply').disabled=true;};
 function setBusy(value){busy=value;find('progress').hidden=!value;dialog.querySelectorAll('button,input').forEach(el=>{el.disabled=value;});if(!value)find('results').querySelectorAll('input').forEach(input=>{const row=proposals.find(p=>p.token===input.dataset.forecastToken);input.disabled=!row?.token||(find('empty').checked&&row.has_current);});find('apply').disabled=value||!proposals.some(row=>row.token);}
 async function request(action,data){
  const csrf=document.querySelector('[name=csrfmiddlewaretoken]')?.value;
  const response=await fetch(location.href,{method:'POST',credentials:'same-origin',body:new URLSearchParams({action,revision:dialog.dataset.revision,csrfmiddlewaretoken:csrf,...data})});
  let result;try{result=await response.json();}catch{throw new Error('Сервер вернул неожиданный ответ (HTTP '+response.status+'). Прогноз не подтверждён.');}
  if(!response.ok||!result||!result.ok)throw new Error(result?.error||'Не удалось выполнить действие.');return result;
 }
 const groups=()=>[...find('groups').querySelectorAll('label')];
 document.querySelector('[data-forecast-history-open]').addEventListener('click',()=>{
  if(document.querySelector('.split-unsaved-dock:not([hidden])')){window.SplitProgress?.start('Прогноз').fail('Сначала сохраните изменения таблицы, затем рассчитайте прогноз.');return;}
  clear();status.textContent='Выберите группы для предпросмотра.';find('groups').replaceChildren();
  document.querySelectorAll('[data-stock-group]').forEach(row=>{
   if(row.hasAttribute('data-reference-excluded'))return;
   const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=row.dataset.stockGroup;input.checked=true;
   label.append(input,document.createTextNode(' '+row.querySelector('[data-stock-toggle]').title));find('groups').append(label);
  });find('search').value='';dialog.showModal();
 });
 find('search').addEventListener('input',()=>groups().forEach(label=>{label.hidden=!label.textContent.toLocaleLowerCase().includes(find('search').value.toLocaleLowerCase());}));
 find('all').addEventListener('click',()=>{clear();groups().filter(label=>!label.hidden).forEach(label=>{label.querySelector('input').checked=true;});});
 find('groups').addEventListener('change',clear);
 find('none').addEventListener('click',()=>{clear();groups().forEach(label=>{label.querySelector('input').checked=false;});});
 for(const name of ['year','adjustment','empty'])find(name).addEventListener('change',clear);
 find('close').addEventListener('click',()=>dialog.close());dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
 find('preview').addEventListener('click',async()=>{
  const targets=groups().filter(label=>!label.hidden&&label.querySelector('input').checked).map(label=>label.querySelector('input').value);
  if(!targets.length){status.textContent='Выберите группы.';return;}
  clear();setBusy(true);status.textContent='Получаем историю и рассчитываем прогноз…';const progress=window.SplitProgress?.start('Расчёт прогноза по истории');
  try{
   const result=await request('preview_forecast_history',{targets:JSON.stringify(targets),year:find('year').value,adjustment:find('adjustment').value});proposals=result.rows;
   status.textContent='Факт: '+result.source.from+' — '+result.source.through+'. Каналы: '+result.source.channels.join(', ')+'. Проверьте предложения.';
   const table=document.createElement('table');table.className='admin-table';const head=table.createTHead().insertRow();
   for(const title of ['Применить','Группа','Текущий прогноз, компл.','Предложено, компл.','По мощностям / пояснение']){const cell=document.createElement('th');cell.textContent=title;head.append(cell);}
   const body=table.createTBody();for(const row of proposals){
    const tr=body.insertRow(),select=document.createElement('input');select.type='checkbox';select.dataset.forecastToken=row.token||'';select.disabled=!row.token||(find('empty').checked&&row.has_current);select.checked=!select.disabled;
    tr.insertCell().append(select);tr.insertCell().textContent=row.name;tr.insertCell().textContent=number(row.current);tr.insertCell().textContent=number(row.proposed);
    tr.insertCell().textContent=row.error||(find('empty').checked&&row.has_current?'Есть введённые значения — пропущен.':row.powers.map(p=>p.power+': '+number(p.sets)+' компл. (внутр. '+number(p.indoor)+', наруж. '+number(p.outdoor)+')').join('; ')+(row.warnings?.length?' · '+row.warnings.join('; '):''));
   }find('results').append(table);progress?.finish('Предпросмотр готов');
  }catch(error){status.textContent=error.message;progress?.fail(error.message);}finally{
   setBusy(false);find('results').querySelectorAll('input').forEach(input=>{const row=proposals.find(p=>p.token===input.dataset.forecastToken);input.disabled=!row?.token||(find('empty').checked&&row.has_current);});
  }
 });
 find('apply').addEventListener('click',async()=>{
  const tokens=[...find('results').querySelectorAll('input:checked:not(:disabled)')].map(input=>input.dataset.forecastToken);
  if(!tokens.length){status.textContent='Выберите предложения для применения.';return;}
  setBusy(true);status.textContent='Сохраняем прогноз…';const progress=window.SplitProgress?.start('Сохранение прогноза');
  try{const result=await request('apply_forecast_history',{tokens:JSON.stringify(tokens),only_empty:find('empty').checked?'1':'0'});progress?.finish('Прогноз сохранён · групп: '+result.count);location.reload();}
  catch(error){status.textContent=error.message;progress?.fail(error.message);setBusy(false);}
 });
})();
