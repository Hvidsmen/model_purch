(() => {
 const csv = document.getElementById('report-page-csv');
 if (csv) csv.addEventListener('click', () => { const url = new URL(location.href); url.searchParams.set('run','1'); url.searchParams.set('export','csv'); location.href=url; });
 const detail = document.getElementById('deal-detail-toggle');
 if (detail) detail.addEventListener('change', () => document.querySelectorAll('[data-deal-detail]').forEach(cell => cell.hidden=!detail.checked));
 const dealScroll = document.getElementById('deal-scroll');
 const dealTop = document.getElementById('deal-scroll-top');
 if (dealScroll && dealTop) {
  const table = dealScroll.querySelector('table');
  const syncSize = () => {
   dealTop.hidden = table.scrollWidth <= dealScroll.clientWidth;
   dealTop.firstElementChild.style.width = `${dealScroll.scrollWidth + dealTop.clientWidth - dealScroll.clientWidth}px`;
   dealTop.scrollLeft = dealScroll.scrollLeft;
  };
  dealTop.addEventListener('scroll', () => {
   if (Math.abs(dealScroll.scrollLeft - dealTop.scrollLeft) > 1) dealScroll.scrollLeft = dealTop.scrollLeft;
  });
  dealScroll.addEventListener('scroll', () => {
   if (Math.abs(dealTop.scrollLeft - dealScroll.scrollLeft) > 1) dealTop.scrollLeft = dealScroll.scrollLeft;
  });
  const observer = new ResizeObserver(syncSize);
  observer.observe(table);
  observer.observe(dealScroll);
  syncSize();
 }
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
 document.querySelectorAll('.subdivision-summary-toggle').forEach(button => {
  button.addEventListener('click', () => {
   const expanded = button.getAttribute('aria-expanded') !== 'true';
   button.setAttribute('aria-expanded', String(expanded));
   button.querySelector('[data-subdivision-arrow]').textContent = expanded ? '▾' : '▸';
   button.closest('tbody').querySelectorAll('.subdivision-summary-type').forEach(row => row.hidden = !expanded);
  });
 });
 const root = document.getElementById('native-division-report');
 const data = document.getElementById('native-division-tree');
 if (!root || !data) return;
 const nodes = JSON.parse(data.textContent);
 const expandedColumns = {segments: false, motivation: false};
 const blocks = root.querySelector('.report-period-header').children.length-1;
 root.style.setProperty('--report-blocks',blocks);
 root.style.setProperty('--report-columns',8);
 const money = new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0});
 const percent = new Intl.NumberFormat('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2});
 function create(node) {
  const parent=document.createElement(node.children.length ? 'details' : 'div');
  parent.className=node.children.length ? 'native-division-node' : 'native-division-leaf';
  const row=node.children.length ? document.createElement('summary') : parent;
  row.classList.add('division-report-grid'); row.style.setProperty('--level',node.level);
  const label=document.createElement('span'); label.className='native-label';label.textContent=node.label;row.appendChild(label);
  node.values.forEach((block,blockIndex) => block.forEach((value,index) => {
   if (index===13 && blockIndex < blocks-1) return;
   const cell=document.createElement('span');cell.className='report-number';
   if (index===13) cell.dataset.divisionBonus='';
   const isRatio=index===7 || index===11;
   const text=value==null ? '—' : (isRatio ? percent.format(Number(value)*100)+'%' : money.format(Number(value)));
   if (index===6 || index===11) {
    const button=document.createElement('button'); button.type='button'; button.className='division-metric-toggle';
    button.dataset.divisionReveal=index===6 ? 'segments' : 'motivation';
    button.setAttribute('aria-expanded',String(expandedColumns[button.dataset.divisionReveal]));
    button.title=index===6 ? 'Показать отгрузки O0–O4' : 'Показать план и факт мотивации';
    button.textContent=text; cell.appendChild(button);
   } else cell.textContent=text;
   if (isRatio && value!=null) cell.classList.add(Number(value)>=.8 ? 'percent-good' : 'percent-low');
   if (index===8 || index===12) cell.classList.add('metric-number', index===8 ? 'motivation-potential' : 'motivation-earned');
   if (index<5) {cell.dataset.divisionSegments='';cell.hidden=!expandedColumns.segments;}
   if (index===9 || index===10) {cell.dataset.divisionMotivation='';cell.hidden=!expandedColumns.motivation;}
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
 root.addEventListener('click', event => {
  const button=event.target.closest('[data-division-reveal]');
  if (!button) return;
  event.preventDefault(); event.stopPropagation();
  const kind=button.dataset.divisionReveal;
  expandedColumns[kind]=!expandedColumns[kind];
  root.style.setProperty('--report-columns',8+(expandedColumns.segments ? 5 : 0)+(expandedColumns.motivation ? 2 : 0));
  root.querySelectorAll(kind==='segments' ? '[data-division-segments]' : '[data-division-motivation]').forEach(cell=>cell.hidden=!expandedColumns[kind]);
  root.querySelectorAll(`[data-division-reveal="${kind}"]`).forEach(control=>control.setAttribute('aria-expanded',String(expandedColumns[kind])));
 });
})();
