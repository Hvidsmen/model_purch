/* Monitor the independent SQL Agent preparation; never start work on GET. */
(() => {
 const root=document.querySelector('[data-prepared-sales]'); if(!root)return;
 const status=root.querySelector('[data-prepared-status]'),ready=root.querySelector('[data-prepared-ready]');
 const format=value=>value?new Date(value).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):'—';
 const labels={running:'Выполняется',completed:'Завершено',failed:'Ошибка',interrupted:'Прервано'};
 async function refresh(){
  if(document.hidden)return;
  try{
   const response=await fetch(root.dataset.monitorUrl,{cache:'no-store',headers:{Accept:'application/json'}});
   const data=await response.json();if(!response.ok)throw new Error(data.error||'Ошибка проверки статуса');
   const current=data.history?.[0];
   const seconds=current?Math.max(0,Math.floor(((current.completed_at?Date.parse(current.completed_at):Date.now())-Date.parse(current.started_at))/1000)):0;
   status.textContent=`${data.status}${current?' · '+Math.floor(seconds/60)+' мин '+seconds%60+' сек':''}`;
   const start=root.querySelector('[data-prepared-start]');if(start)start.disabled=data.agent_running===true;
   ready.textContent=data.ready?`Готовый набор: ${data.ready.run_id} · ${format(data.ready.completed_at)} МСК · строк: ${data.ready.row_count}`:'Готового набора пока нет. Сначала выполните подготовку.';
   const history=root.querySelector('[data-prepared-history]');history.replaceChildren();
   for(const run of data.history||[]){const p=document.createElement('p');p.textContent=`${format(run.started_at)} МСК · ${labels[run.state]||run.state} · ${run.stage}${run.row_count!=null?' · строк: '+run.row_count:''}${run.error_number?' · SQL '+run.error_number:''}`;history.append(p);}
   if(!data.history?.length)history.textContent='Подготовка ещё не запускалась.';
  }catch(error){status.textContent=error.message;}
 }
 refresh();setInterval(refresh,10000);document.addEventListener('visibilitychange',refresh);
})();
