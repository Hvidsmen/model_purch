(() => {
 const table=document.querySelector('.split-plan-matrix'),source=document.getElementById('split-plan-months-data');if(!table||!source)return;
 const data=JSON.parse(source.textContent);if(!data)return;
 const format=(value,places=6)=>value==null?'—':Number(value).toLocaleString('ru-RU',{maximumFractionDigits:places});
 function metrics(cell,values){
  for(const [key,label,metric] of [['qty','Компл.','quantity'],['revenue','Выручка, $','revenue'],['markup','Наценка ×','markup']]){
   const block=document.createElement('div');block.dataset.splitMetric=metric;block.className='split-plan-metric';const small=document.createElement('small');small.textContent=label;const value=document.createElement('span');value.textContent=format(values?.[key],key==='qty'?0:6);block.append(small,value);cell.append(block);
  }
 }
 function valuesFor(row){
  if(row.dataset.planGroup)return data.groups[row.dataset.planGroup]?.bands;
  if(row.dataset.planPowerGroup)return data.groups[row.dataset.planPowerGroup]?.powers[row.dataset.planPowerKey];
  if(row.dataset.planNode)return data.nodes[row.dataset.planNode];
  return row.dataset.planSummary==='all'?data.all:data.page;
 }
 function decorate(cell,band,month){
  cell.hidden=true;cell.dataset.planMonthBand=band;cell.dataset.salesChannel=band;cell.dataset.splitMetricAny='quantity revenue markup';cell.dataset.planMonth=month;
  const heading=[...table.querySelectorAll('[data-plan-channel]')].find(node=>node.dataset.planChannel===band);
  if(heading?.hasAttribute('data-sales-channel-filtered'))cell.setAttribute('data-sales-channel-filtered','');
 }
 table.querySelectorAll('thead [data-plan-metric-band]').forEach(header=>{
  const band=header.dataset.planMetricBand,button=document.createElement('span');button.setAttribute('role','button');button.tabIndex=0;button.dataset.planMonthToggle=band;button.className='split-plan-month-toggle';button.setAttribute('aria-expanded','false');const title=header.textContent.trim();button.textContent='▸ '+title;header.replaceChildren(button);
  let built=false;const build=()=>{if(built)return;built=true;
  let last=header;for(const month of data.months){const th=document.createElement('th');decorate(th,band,month);th.dataset.planMetricBand=band;th.textContent=month.slice(5)+'.'+month.slice(0,4);last.after(th);last=th;}
  for(const row of table.tBodies[0].rows){
   const target=band==='all'?row.querySelector('td.split-plan-group-total,td[data-subtotal-total],td.split-matrix-total[data-split-metric-any]'):[...row.querySelectorAll('td[data-sales-channel]')].find(cell=>cell.dataset.salesChannel===band&&!cell.classList.contains('split-fact-column')&&!cell.dataset.planMonth);
   if(!target)continue;
   let after=target;const values=valuesFor(row)?.[band];
   for(const month of data.months){const td=document.createElement('td');decorate(td,band,month);td.className='split-plan-month-cell';metrics(td,values?.[month]);after.after(td);after=td;}
  }
  };
  button.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();button.click();}});
  button.addEventListener('click',()=>{
   const open=button.getAttribute('aria-expanded')!=='true';if(open)build();button.setAttribute('aria-expanded',String(open));button.textContent=(open?'▾ ':'▸ ')+title;
   table.querySelectorAll('[data-plan-month-band]').forEach(cell=>{if(cell.dataset.planMonthBand===band)cell.hidden=!open;});
   window.dispatchEvent(new Event('resize'));
  });
 });
})();
