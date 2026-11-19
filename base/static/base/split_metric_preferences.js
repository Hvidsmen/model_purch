(() => {
 const panel=document.querySelector('[data-split-metric-preferences]');if(!panel)return;
 const workspace=panel.closest('.split-workspace'),choices=[...panel.querySelectorAll('[data-split-metric-choice]')];
 const key=panel.dataset.storageKey,off=new Set();
 try{const stored=JSON.parse(localStorage.getItem(key)||'[]');if(Array.isArray(stored))for(const name of stored)if(choices.some(c=>c.dataset.splitMetricChoice===name))off.add(name);}catch{}
 function apply(announce=false,notify=false){
  workspace.toggleAttribute('data-split-metrics-compact',off.size>0);
  choices.forEach(c=>{c.checked=!off.has(c.dataset.splitMetricChoice);});
  workspace.querySelectorAll('[data-split-metric]').forEach(node=>node.toggleAttribute('data-split-metric-off',off.has(node.dataset.splitMetric)));
  workspace.querySelectorAll('[data-split-metric-any]').forEach(node=>node.toggleAttribute('data-split-metric-off',node.dataset.splitMetricAny.split(' ').every(name=>off.has(name))));
  workspace.querySelectorAll('[data-split-metric-span]').forEach(node=>{
   const count=node.dataset.splitMetricSpan.split(' ').filter(name=>!off.has(name)).length;
   if(node.colSpan!==Math.max(1,count))node.colSpan=Math.max(1,count);node.toggleAttribute('data-split-metric-off',!count);
  });
  const table=workspace.querySelector('.split-plan-matrix');
  if(table){
   const headers=[...table.tHead.rows[1].cells];
   const visible=node=>!node.hidden&&!node.hasAttribute('data-split-metric-off');
   table.querySelectorAll('[data-history-band]').forEach(band=>{
    let width=0;
    if(band.dataset.historyBand==='all'){
     width=headers.filter(h=>visible(h)&&(h.classList.contains('split-matrix-total')||
      h.hasAttribute('data-plan-opening-component')||h.dataset.planMetricBand==='all'||
      (h.hasAttribute('data-history-year-column')&&(h.dataset.historyBandKey||h.dataset.historyMonthColumn?.slice(0,-5))==='all'))).length;
    }else{
     width=headers.filter(h=>visible(h)&&(h.dataset.planMetricBand===band.dataset.historyBand ||
      (h.hasAttribute('data-history-year-column')&&(h.dataset.historyBandKey||h.dataset.historyMonthColumn?.slice(0,-5))===band.dataset.historyBand))).length;
    }
    if(band.colSpan!==Math.max(1,width))band.colSpan=Math.max(1,width);
    band.toggleAttribute('data-split-metric-off',!width);
   });
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
