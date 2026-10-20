(() => {
 const csv = document.getElementById('report-page-csv');
 if (csv) csv.addEventListener('click', () => { const url = new URL(location.href); url.searchParams.set('run','1'); url.searchParams.set('export','csv'); location.href=url; });
 const detail = document.getElementById('deal-detail-toggle');
 if (detail) detail.addEventListener('change', () => document.querySelectorAll('[data-deal-detail]').forEach(cell => cell.hidden=!detail.checked));
 const form = document.getElementById('native-report-filters');
 if (['manager', 'manager-detail'].includes(form?.dataset.kind)) {
  ['type', 'subdivision'].forEach(name => form.elements[name]?.addEventListener('change', () => {
   [...form.elements.author.options].forEach(option => option.selected = false);
   form.requestSubmit(document.getElementById('report-filter-refresh'));
  }));
 }
 if (form?.dataset.kind === 'manager-detail') {
  form.elements.currency?.addEventListener('change', () => {
   if (document.getElementById('manager-detail-panel')?.open) {
    let flag = form.querySelector('input[name=details]');
    if (!flag) { flag = document.createElement('input'); flag.type = 'hidden'; flag.name = 'details'; form.appendChild(flag); }
    flag.value = '1';
   }
   form.requestSubmit(form.elements.type.value ? form.querySelector('button[name=run]') : document.getElementById('report-filter-refresh'));
  });
 }
 const root = document.getElementById('native-division-report');
 const data = document.getElementById('native-division-tree');
 if (!root || !data) return;
 const nodes = JSON.parse(data.textContent);
 const periodToggle = document.getElementById('native-report-period-detail');
 const blocks = root.querySelector('.report-period-header').children.length-1;
 root.style.setProperty('--report-blocks',blocks);
 root.style.setProperty('--report-columns',7);
 const money = new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0});
 const percent = new Intl.NumberFormat('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2});
 function create(node) {
  const parent=document.createElement(node.children.length ? 'details' : 'div');
  parent.className=node.children.length ? 'native-division-node' : 'native-division-leaf';
  const row=node.children.length ? document.createElement('summary') : parent;
  row.classList.add('division-report-grid'); row.style.setProperty('--level',node.level);
  const label=document.createElement('span'); label.className='native-label';label.textContent=node.label;row.appendChild(label);
  node.values.forEach(block => block.forEach((value,index) => {
   const cell=document.createElement('span');cell.className='report-number';
   const isRatio=index===7 || index===11;
   cell.textContent=value==null ? '—' : (isRatio ? percent.format(Number(value)*100)+'%' : money.format(Number(value)));
   if (isRatio && value!=null) cell.classList.add(Number(value)>=.8 ? 'percent-good' : 'percent-low');
   if (index===8 || index===12) cell.classList.add('metric-number');
   if (index<8) {cell.dataset.periodDetail='';cell.hidden=!periodToggle.checked;}
   row.appendChild(cell);
  }));
  if (node.children.length) {
   parent.appendChild(row); const children=document.createElement('div');parent.appendChild(children);
   let loaded=false;
   const load=()=>{if(loaded)return;loaded=true;node.children.forEach(child=>children.appendChild(create(child)));};
   parent.addEventListener('toggle',()=>{if(parent.open)load();});
   parent._load=load;
  }
  return parent;
 }
 const target=document.getElementById('native-division-nodes');
 nodes.forEach(node=>target.appendChild(create(node)));
 document.getElementById('native-report-expand').addEventListener('click',()=>{
  const expand=element=>{element._load?.();element.open=true;[...element.children].forEach(child=>child.querySelectorAll(':scope > details').forEach(expand));};
  target.querySelectorAll(':scope > details').forEach(expand);
 });
 document.getElementById('native-report-collapse').addEventListener('click',()=>target.querySelectorAll('details').forEach(element=>element.open=false));
 periodToggle.addEventListener('change',()=>{
  root.style.setProperty('--report-columns',periodToggle.checked ? 15 : 7);
  root.querySelectorAll('[data-period-detail]').forEach(cell=>cell.hidden=!periodToggle.checked);
 });
})();
