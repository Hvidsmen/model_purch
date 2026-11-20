(() => {
 const dialog=document.getElementById('split-forecast-history');if(!dialog)return;
 const find=name=>dialog.querySelector('[data-forecast-history-'+name+']'),status=find('status');
 let proposals=[],busy=false;
 const number=value=>value==null?'—':Number(value).toLocaleString('ru-RU',{maximumFractionDigits:6});
 const clear=()=>{proposals=[];find('results').replaceChildren();find('apply').disabled=true;find('apply').hidden=true;};
 function setBusy(value){busy=value;find('progress').hidden=!value;dialog.querySelectorAll('button,input,select').forEach(el=>{el.disabled=value;});if(!value)find('results').querySelectorAll('input').forEach(input=>{const row=proposals.find(p=>p.token===input.dataset.forecastToken);input.disabled=!row?.token||(find('empty').checked&&row.has_current&&!row.needs_stock_repair);});find('apply').disabled=value||!proposals.some(row=>row.token&&(!find('empty').checked||!row.has_current||row.needs_stock_repair));}
 async function request(action,data){
  const csrf=document.querySelector('[name=csrfmiddlewaretoken]')?.value;
  const response=await fetch(location.href,{method:'POST',credentials:'same-origin',body:new URLSearchParams({action,revision:dialog.dataset.revision,csrfmiddlewaretoken:csrf,...data})});
  let result;try{result=await response.json();}catch{throw new Error('Сервер вернул неожиданный ответ (HTTP '+response.status+'). Прогноз не подтверждён.');}
  if(!response.ok||!result||!result.ok)throw new Error(result?.error||'Не удалось выполнить действие.');return result;
 }
 const resultKey='split-forecast-result-'+location.pathname;
 function report(result,target){
  const box=document.createElement('div');box.setAttribute('role','status');
  const summary=document.createElement('p');summary.textContent='Сохранено прогнозов: '+result.count+'. Пропущено групп: '+(result.skipped?.length||0)+'.';box.append(summary);
  if(result.repairs?.length){const note=document.createElement('p');note.textContent='Ограничены прежние автоматические прогнозы: '+result.repairs.map(row=>row.name+': '+row.reason).join(' ');box.append(note);}
  if(result.skipped?.length){const details=document.createElement('details'),caption=document.createElement('summary'),list=document.createElement('ul');caption.textContent='Пропущенные группы и причины';details.append(caption,list);result.skipped.forEach(row=>{const item=document.createElement('li');item.textContent=row.name+': '+row.reason;list.append(item);});box.append(details);}
  target.replaceChildren(box);
 }
 try{const result=JSON.parse(sessionStorage.getItem(resultKey)||'null');if(result){const target=document.createElement('div');document.querySelector('[data-stock-workspace]')?.before(target);report(result,target);sessionStorage.removeItem(resultKey);}}catch(_){}
 function saved(result){
  if(!result.count){report(result,find('results'));status.textContent='Новые прогнозы не сохранены. Существующие значения оставлены без изменений.';return;}
  try{sessionStorage.setItem(resultKey,JSON.stringify(result));}catch(_){}
  window.SplitUnsaved?window.SplitUnsaved.navigate(location.href):location.reload();
 }
 const groups=()=>[...find('groups').querySelectorAll('label')];
 async function open(method){
  if(busy)return;
  find('method').value=method;
  if(document.querySelector('.split-unsaved-dock:not([hidden])')){window.SplitProgress?.start('Прогноз').fail('Сначала сохраните изменения таблицы, затем рассчитайте прогноз.');return;}
  clear();status.textContent='Выберите группы и нажмите «Рассчитать и сохранить». Предпросмотр необязателен.';find('groups').replaceChildren();
  document.querySelectorAll('[data-stock-group]').forEach(row=>{
   if(row.hasAttribute('data-reference-excluded'))return;
   const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=row.dataset.stockGroup;input.checked=true;
   label.append(input,document.createTextNode(' '+row.querySelector('[data-stock-toggle]').title));find('groups').append(label);
  });find('search').value='';dialog.showModal();await methodChanged();
 }
 document.querySelector('[data-forecast-history-open]').addEventListener('click',()=>open('history'));
 document.querySelector('[data-forecast-plan-open]')?.addEventListener('click',()=>open('plan_execution'));
 let versionsLoaded=false;
 async function methodChanged(){
  clear();dialog.querySelectorAll('[data-forecast-method-section]').forEach(section=>section.hidden=section.dataset.forecastMethodSection!==find('method').value);
  if(find('method').value!=='plan_execution'||versionsLoaded)return;
  setBusy(true);status.textContent='Загружаем версии регистра планов продаж…';
  try{const result=await request('forecast_plan_versions',{});find('plan-version').replaceChildren(new Option('Выберите версию',''));result.versions.forEach(value=>find('plan-version').append(new Option(value,value)));versionsLoaded=true;status.textContent=result.versions.length?'Выберите версию и начало периода выполнения.':'В регистре не найдены версии за период для каналов сценария.';}
  catch(error){status.textContent=error.message;}finally{setBusy(false);}
 }
 find('method').addEventListener('change',methodChanged);
 function parameters(){return {method:find('method').value,plan_version:find('plan-version').value,elapsed_from:find('elapsed-from').value,year:find('year').value,adjustment:find('adjustment').value};}
 find('search').addEventListener('input',()=>groups().forEach(label=>{label.hidden=!label.textContent.toLocaleLowerCase().includes(find('search').value.toLocaleLowerCase());}));
 find('all').addEventListener('click',()=>{clear();groups().filter(label=>!label.hidden).forEach(label=>{label.querySelector('input').checked=true;});});
 find('groups').addEventListener('change',clear);
 find('none').addEventListener('click',()=>{clear();groups().forEach(label=>{label.querySelector('input').checked=false;});});
 for(const name of ['year','adjustment','empty','plan-version','elapsed-from'])find(name).addEventListener('change',clear);
 find('close').addEventListener('click',()=>dialog.close());dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
 find('save').addEventListener('click',async()=>{
  if(busy)return;
  const targets=groups().filter(label=>!label.hidden&&label.querySelector('input').checked).map(label=>label.querySelector('input').value);
  if(!targets.length){status.textContent='Выберите группы.';return;}
  clear();setBusy(true);status.textContent='Расчёт и сохранение…';
  try{
   const result=await request('calculate_save_forecast_history',{targets:JSON.stringify(targets),...parameters(),only_empty:find('empty').checked?'1':'0'});
   saved(result);
  }catch(error){status.textContent=error.message;}finally{setBusy(false);}
 });
 find('preview').addEventListener('click',async()=>{
  const targets=groups().filter(label=>!label.hidden&&label.querySelector('input').checked).map(label=>label.querySelector('input').value);
  if(!targets.length){status.textContent='Выберите группы.';return;}
  clear();setBusy(true);status.textContent='Получаем историю и рассчитываем прогноз…';const progress=window.SplitProgress?.start('Расчёт прогноза по истории');
  try{
   const result=await request('preview_forecast_history',{targets:JSON.stringify(targets),...parameters()});proposals=result.rows;
   status.textContent=(result.source.plan_version?'Версия: '+result.source.plan_version+'. ':'')+'Факт: '+result.source.from+' — '+result.source.through+'. '+(result.source.calculation||'')+'. Каналы: '+result.source.channels.join(', ')+'. Проверьте предложения.';
   const table=document.createElement('table');table.className='admin-table';const head=table.createTHead().insertRow();
   for(const title of ['Применить','Группа','Текущий прогноз, компл.','Предложено, компл.','По мощностям / пояснение']){const cell=document.createElement('th');cell.textContent=title;head.append(cell);}
   const body=table.createTBody();for(const row of proposals){
    const tr=body.insertRow(),select=document.createElement('input');select.type='checkbox';select.dataset.forecastToken=row.token||'';select.disabled=!row.token||(find('empty').checked&&row.has_current&&!row.needs_stock_repair);select.checked=!select.disabled;
    tr.insertCell().append(select);tr.insertCell().textContent=row.name;tr.insertCell().textContent=number(row.current);tr.insertCell().textContent=number(row.proposed);
    tr.insertCell().textContent=row.error||(find('empty').checked&&row.has_current&&!row.needs_stock_repair?'Есть введённые значения — пропущен.':(row.needs_stock_repair?'Исправление ранее сохранённого автоматического прогноза с дефицитом; ':'')+(row.explanation?row.explanation+'; ':'')+row.powers.map(p=>p.power+': '+number(p.sets)+' компл. (внутр. '+number(p.indoor)+', наруж. '+number(p.outdoor)+')').join('; ')+(row.warnings?.length?' · '+row.warnings.join('; '):''));
   }find('results').append(table);find('apply').hidden=!proposals.some(row=>row.token&&(!find('empty').checked||!row.has_current||row.needs_stock_repair));progress?.finish('Предпросмотр готов');
  }catch(error){status.textContent=error.message;progress?.fail(error.message);}finally{
   setBusy(false);find('results').querySelectorAll('input').forEach(input=>{const row=proposals.find(p=>p.token===input.dataset.forecastToken);input.disabled=!row?.token||(find('empty').checked&&row.has_current&&!row.needs_stock_repair);});
  }
 });
 find('apply').addEventListener('click',async()=>{
  const tokens=[...find('results').querySelectorAll('input:checked:not(:disabled)')].map(input=>input.dataset.forecastToken);
  if(!tokens.length){status.textContent='Выберите предложения для применения.';return;}
  if(busy)return;
  setBusy(true);status.textContent='Сохраняем выбранные прогнозы…';const progress=window.SplitProgress?.start('Сохранение прогноза');
  try{
   const result=await request('apply_forecast_history',{tokens:JSON.stringify(tokens),only_empty:find('empty').checked?'1':'0'});
   result.skipped=proposals.filter(row=>!row.token||(find('empty').checked&&row.has_current&&!row.needs_stock_repair)).map(row=>({gid:row.gid,name:row.name,reason:row.error||'Прогноз уже заполнен — сохранён без изменений.'}));
   progress?.finish('Прогноз сохранён · групп: '+result.count);saved(result);}
  catch(error){status.textContent=error.message;progress?.fail(error.message);setBusy(false);}
 });
})();
