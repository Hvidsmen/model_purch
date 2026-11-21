(() => {
 const picker=document.querySelector('[data-group-picker]');if(!picker){delete window.SplitGroupPicker;return;}
 const choices=[...picker.querySelectorAll('[data-group-choice]')],search=picker.querySelector('[data-group-picker-search]');
 const ids=choices.map(el=>el.dataset.groupChoice),key='split-group-picker:'+location.pathname;
 let saved;try{saved=JSON.parse(sessionStorage.getItem(key)||'null');}catch{}
 if(saved&&Array.isArray(saved.known)&&Array.isArray(saved.selected)){
  const same=ids.length===saved.known.length&&ids.every(id=>saved.known.includes(id));
  const all=saved.selected.length===saved.known.length;
  const retained=saved.selected.filter(id=>ids.includes(id));
  choices.forEach(el=>el.checked=all||(!same&&!retained.length)||retained.includes(el.dataset.groupChoice));
 }
 const selected=()=>new Set(choices.filter(el=>el.checked).map(el=>el.dataset.groupChoice));
 let activeSelection=selected();
 window.SplitGroupPicker={matches:gid=>activeSelection.has(String(gid))};
 function searchOptions(){
  const q=search.value.trim().toLocaleLowerCase('ru');let found=0;
  choices.forEach(el=>{const label=el.closest('label');label.hidden=!label.textContent.toLocaleLowerCase('ru').includes(q);if(!label.hidden)found++;});
  picker.querySelector('[data-group-picker-empty]').hidden=found>0;
 }
 function apply(){
  const active=selected();activeSelection=active;
  const rows=[...document.querySelectorAll('[data-plan-group],[data-plan-power-group],[data-stock-group],[data-stock-child],[data-grid-row],[data-composition-group]')];
  rows.forEach(row=>{const gid=row.dataset.planGroup||row.dataset.planPowerGroup||row.dataset.stockGroup||row.dataset.stockChild||row.dataset.gid||row.dataset.compositionGroup;row.toggleAttribute('data-group-picker-excluded',!active.has(gid));});
  const nodes=new Map([...document.querySelectorAll('[data-plan-node]')].map(row=>[row.dataset.planNode,row]));
  const parents=new Set();rows.filter(row=>row.hasAttribute('data-plan-group')&&active.has(row.dataset.planGroup)).forEach(row=>{let parent=row.dataset.planParent;while(nodes.has(parent)){parents.add(parent);parent=nodes.get(parent).dataset.planParent;}});
  nodes.forEach((row,id)=>row.toggleAttribute('data-group-picker-excluded',!parents.has(id)));
  picker.querySelector('[data-group-picker-caption]').textContent=active.size===ids.length?'Все':active.size===1?choices.find(el=>el.checked).closest('label').textContent.trim():active.size?'Выбрано: '+active.size:'Не выбраны';
  picker.querySelector('[data-group-picker-count]').textContent='Выбрано '+active.size+' из '+ids.length+' групп';
  try{sessionStorage.setItem(key,JSON.stringify({known:ids,selected:[...active]}));}catch{}
  document.dispatchEvent(new Event('split-group-selection-change'));window.dispatchEvent(new Event('resize'));
 }
 choices.forEach(el=>el.addEventListener('change',apply));search.addEventListener('input',searchOptions);
 picker.querySelector('[data-group-picker-all]').addEventListener('click',()=>{choices.forEach(el=>el.checked=true);apply();});
 picker.querySelector('[data-group-picker-none]').addEventListener('click',()=>{choices.forEach(el=>el.checked=false);apply();});
 picker.querySelector('[data-group-picker-found]').addEventListener('click',()=>{choices.filter(el=>!el.closest('label').hidden).forEach(el=>el.checked=true);apply();});
 picker.addEventListener('keydown',event=>{if(event.key==='Escape'){picker.open=false;picker.querySelector('summary').focus();}});
 document.addEventListener('click',event=>{if(!picker.contains(event.target))picker.open=false;});
 searchOptions();apply();
})();
