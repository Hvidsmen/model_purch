/* Monitor the independent SQL Agent preparation; never start work on GET. */
(() => {
 const root=document.querySelector('[data-prepared-sales]'); if(!root)return;
 const status=root.querySelector('[data-prepared-status]'),ready=root.querySelector('[data-prepared-ready]');
 const start=root.querySelector('[data-prepared-start]');
 const form=start?.form;
 let submitting=false,fetching=false;
 function startState(label,disabled){
  if(!start)return;
  start.disabled=disabled;start.textContent=label;
  start.setAttribute('aria-busy',disabled?'true':'false');
 }
 startState('Проверка состояния…',true);
 form?.addEventListener('submit',event=>{
  if(submitting||start.disabled){event.preventDefault();return;}
  submitting=true;
  startState('Запускаем подготовку…',true);
  status.textContent='Запускаем подготовку факта…';
 });
 const format=value=>value?new Date(value).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):'—';
 const labels={running:'Выполняется',completed:'Завершено',failed:'Ошибка',interrupted:'Прервано'};
 function renderStages(stages){
  const target=root.querySelector('[data-prepared-stages]');if(!target)return;
  target.replaceChildren();
  if(!stages?.length){target.textContent='Замеры появятся после завершения или ошибки нового запуска.';return;}
  const table=document.createElement('table');
  const columns=[['stage','Этап'],['seconds','Время, сек.'],['cpu_ms','CPU, мс'],['logical_reads','Логические чтения'],['reads','Физические чтения'],['writes','Записи'],['state','Результат']];
  const header=table.createTHead().insertRow();for(const [,label] of columns){const cell=document.createElement('th');cell.textContent=label;header.append(cell);}
  const body=table.createTBody();for(const stage of stages){const row=body.insertRow();for(const [key] of columns){row.insertCell().textContent=key==='state'?(labels[stage[key]]||stage[key]):(stage[key]??'—');}}
  const wrapper=document.createElement('div');wrapper.style.overflowX='auto';wrapper.append(table);target.append(wrapper);
  const note=document.createElement('p');note.textContent='Счётчики — разницы показаний сессии SQL Server. «—» означает, что счётчик недоступен. CPU и длительность не определяют причину ожидания.';target.append(note);
 }
 async function refresh(){
  if(document.hidden||fetching)return;
  fetching=true;
  try{
   const response=await fetch(root.dataset.monitorUrl,{cache:'no-store',headers:{Accept:'application/json'}});
   const data=await response.json();if(!response.ok)throw new Error(data.error||'Ошибка проверки статуса');
   renderStages(data.stages);
   const current=data.history?.[0];
   const seconds=current?Math.max(0,Math.floor(((current.completed_at?Date.parse(current.completed_at):Date.now())-Date.parse(current.started_at))/1000)):0;
   status.textContent=`${data.status}${current?' · '+Math.floor(seconds/60)+' мин '+seconds%60+' сек':''}`;
   if(data.agent_running&&data.live?.waiting)status.textContent+=' · '+data.live.waiting+' · '+(data.live.wait_ms||0)+' мс (текущее ожидание)';
   const running=data.running===true||data.agent_running===true||current?.state==='running';
   if(!submitting)startState(running?'Подготовка выполняется…':'Подготовить сейчас',running);
   ready.textContent=data.ready?`Готовый набор: ${data.ready.run_id} · ${format(data.ready.completed_at)} МСК · строк: ${data.ready.row_count}`:'Готового набора пока нет. Сначала выполните подготовку.';
   const history=root.querySelector('[data-prepared-history]');history.replaceChildren();
   for(const run of data.history||[]){const p=document.createElement('p');p.textContent=`${format(run.started_at)} МСК · ${labels[run.state]||run.state} · ${run.stage}${run.row_count!=null?' · строк: '+run.row_count:''}${run.error_number?' · SQL '+run.error_number:''}`;history.append(p);}
   if(!data.history?.length)history.textContent='Подготовка ещё не запускалась.';
  }catch(error){status.textContent=error.message;}finally{fetching=false;}
 }
 refresh();setInterval(refresh,10000);document.addEventListener('visibilitychange',refresh);
})();
