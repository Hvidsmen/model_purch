(() => {
 const panel=document.querySelector('[data-split-metric-preferences]');if(!panel)return;
 const workspace=panel.closest('.split-workspace'),choices=[...panel.querySelectorAll('[data-split-metric-choice]')];
 const key=panel.dataset.storageKey,off=new Set();
 try{const stored=JSON.parse(localStorage.getItem(key)||'[]');if(Array.isArray(stored))for(const name of stored)if(choices.some(c=>c.dataset.splitMetricChoice===name))off.add(name);}catch{}
 function apply(announce=false,notify=false){
  choices.forEach(c=>{c.checked=!off.has(c.dataset.splitMetricChoice);});
  workspace.querySelectorAll('[data-split-metric]').forEach(node=>node.toggleAttribute('data-split-metric-off',off.has(node.dataset.splitMetric)));
  workspace.querySelectorAll('[data-split-metric-span]').forEach(node=>{
   const count=node.dataset.splitMetricSpan.split(' ').filter(name=>!off.has(name)).length;
   if(node.colSpan!==Math.max(1,count))node.colSpan=Math.max(1,count);node.toggleAttribute('data-split-metric-off',!count);
  });
  const table=workspace.querySelector('.split-plan-matrix');
  if(table){
   // Inventory columns belong to the all-channel band; preserve its expanded
   // opening-stock state and independently controlled historical columns.
   const band=table.querySelector('[data-history-band="all"]');
   if(band){
    const headers=[...table.tHead.rows[1].cells],last=headers.findIndex(h=>h.dataset.splitMetric==='stock');
    const width=headers.slice(last).filter(h=>!h.hidden&&!h.hasAttribute('data-split-metric-off')).length;
    if(last>=0&&band.colSpan!==Math.max(1,width))band.colSpan=Math.max(1,width);
   }
  }
  if(notify)document.dispatchEvent(new CustomEvent('split-metric-visibility-change'));
  window.dispatchEvent(new Event('resize'));
  if(announce)panel.querySelector('[data-split-metrics-status]').textContent=off.size?'Скрыто показателей: '+off.size+'.':'Все показатели показаны.';
 }
 choices.forEach(c=>c.addEventListener('change',()=>{
  if(c.checked)off.delete(c.dataset.splitMetricChoice);else off.add(c.dataset.splitMetricChoice);
  try{localStorage.setItem(key,JSON.stringify([...off]));}catch{}
  apply(true,true);
 }));
 panel.querySelector('[data-split-metrics-reset]').addEventListener('click',()=>{off.clear();try{localStorage.removeItem(key);}catch{}apply(true,true);});
 let pending=false;const schedule=()=>{if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;apply();});};
 // Expansion and history controls continue owning `hidden`; visibility only
 // owns its CSS attribute, so revealing a group cannot undo a preference.
 const observer=new MutationObserver(schedule);
 observer.observe(workspace,{subtree:true,childList:true,attributes:true,attributeFilter:['hidden','colspan']});
 apply(false,true);
})();
