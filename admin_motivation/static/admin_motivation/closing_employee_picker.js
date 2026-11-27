/* Searchable single selection; the native select remains the submitted value. */
(() => {
 const form=document.getElementById('closing-detail-form'),data=document.getElementById('closing-author-options');if(!form||!data)return;
 const authors=JSON.parse(data.textContent),popup=document.createElement('div');popup.className='cd-employee-popup';popup.hidden=true;
 const search=document.createElement('input');search.type='search';search.placeholder='Найти сотрудника';search.setAttribute('aria-label','Найти сотрудника');
 const list=document.createElement('div');list.setAttribute('role','listbox');list.id='closing-employee-list';popup.append(search,list);document.body.append(popup);
 let active=null,serial=0;
 function close(focus=false){popup.hidden=true;if(active){active.button.setAttribute('aria-expanded','false');if(focus)active.button.focus();}active=null;}
 function render(){
  list.replaceChildren();const term=search.value.trim().toLocaleLowerCase('ru');
  for(const option of active.select.options){if(!option.value||!option.textContent.toLocaleLowerCase('ru').includes(term))continue;
   const button=document.createElement('button');button.type='button';button.tabIndex=-1;button.setAttribute('role','option');button.setAttribute('aria-selected',String(option.selected));button.textContent=option.textContent;
   button.addEventListener('click',()=>{const picker=active;picker.select.value=option.value;picker.label();close(true);picker.select.dispatchEvent(new Event('change',{bubbles:true}));});list.append(button);
  }
  if(!list.children.length){const text=document.createElement('p');text.textContent='Сотрудники не найдены';list.append(text);}
 }
 function open(picker){if(picker.button.matches(':disabled'))return;close();active=picker;picker.button.setAttribute('aria-expanded','true');search.value='';render();popup.hidden=false;
  position();search.focus({preventScroll:true});
 }
 function position(){if(!active)return;const box=active.button.getBoundingClientRect(),width=Math.min(Math.max(280,box.width),innerWidth-16);popup.style.width=width+'px';popup.style.left=Math.max(8,Math.min(box.left,innerWidth-width-8))+'px';
  const height=popup.getBoundingClientRect().height;popup.style.top=Math.max(8,box.bottom+height+8<=innerHeight?box.bottom+4:box.top-height-4)+'px';
 }
 function initialize(){form.querySelectorAll('[data-employee-select]').forEach(select=>{
  if(select.dataset.enhanced)return;select.dataset.enhanced='true';const row=select.closest('tr'),kind=row.querySelector('[name$="-kind"]');
  const wrapper=document.createElement('div');wrapper.className='cd-employee-picker';select.before(wrapper);wrapper.append(select);
  const button=document.createElement('button');button.type='button';button.setAttribute('role','combobox');button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-expanded','false');button.setAttribute('aria-controls',list.id);button.setAttribute('aria-label','Выбрать сотрудника');button.id='closing-employee-'+(++serial);wrapper.append(button);
  select.hidden=true;select.required=false;
  const picker={select,button,label:()=>{button.textContent=select.selectedOptions[0]?.textContent||'Выберите сотрудника';}};
  function options(clear=false){const value=clear?'':select.value;select.replaceChildren(new Option('Выберите сотрудника',''));for(const name of authors[kind.value]||[])select.add(new Option(name,name));select.value=[...select.options].some(o=>o.value===value)?value:'';picker.label();}
  options();kind.addEventListener('change',()=>{if(active===picker)close();options(true);});select.addEventListener('change',picker.label);
  button.addEventListener('click',()=>active===picker?close():open(picker));button.addEventListener('keydown',event=>{if(event.key==='ArrowDown'){event.preventDefault();open(picker);}});
 });}
 search.addEventListener('input',render);
 popup.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close(true);return;}const items=[...list.querySelectorAll('[role=option]')];if(!items.length)return;
  const index=items.indexOf(document.activeElement);if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();const next=index<0?(event.key==='ArrowDown'?0:items.length-1):Math.max(0,Math.min(items.length-1,index+(event.key==='ArrowDown'?1:-1)));items[next].focus();}
 });
 document.addEventListener('click',event=>{if(active&&!popup.contains(event.target)&&!active.button.contains(event.target))close();});
 window.addEventListener('resize',position);document.addEventListener('scroll',event=>{if(active&&!popup.contains(event.target))position();},true);
 new MutationObserver(initialize).observe(document.getElementById('closing-people'),{childList:true});
 form.addEventListener('submit',event=>{for(const select of form.querySelectorAll('[data-employee-select]')){const row=select.closest('tr');if(!select.value&&row.querySelector('[name$="-coefficient"]').value&&!row.querySelector('[name$="-DELETE"]').checked){event.preventDefault();row.querySelector('[role=combobox]').click();document.querySelector('[data-preview-status]').textContent='Выберите сотрудника для персонального коэффициента.';break;}}});
 document.querySelector('[data-people-results]').addEventListener('click',event=>{
  const button=event.target.closest('[data-edit-person]');if(!button||button.matches(':disabled'))return;
  const kind=button.dataset.kind,author=button.dataset.author;
  const rows=()=>[...document.querySelectorAll('#closing-people>tr')];
  let row=rows().find(row=>row.querySelector('[name$="-kind"]').value===kind&&row.querySelector('[name$="-author"]').value===author);
  if(!row)row=rows().find(row=>!row.querySelector('[name$="-author"]').value&&!row.querySelector('[name$="-coefficient"]').value);
  if(!row){const count=rows().length;document.querySelector('[data-add-person]').click();if(rows().length===count){document.querySelector('[data-preview-status]').textContent='Достигнут предел персональных коэффициентов.';return;}row=rows().at(-1);}
  initialize();close();const kindField=row.querySelector('[name$="-kind"]'),authorField=row.querySelector('[name$="-author"]'),input=row.querySelector('[name$="-coefficient"]');
  if(kindField.value!==kind){kindField.value=kind;kindField.dispatchEvent(new Event('change',{bubbles:true}));}
  authorField.value=author;authorField.dispatchEvent(new Event('change',{bubbles:true}));row.querySelector('[name$="-DELETE"]').checked=false;
  if(!input.value)input.value=button.dataset.factor;
  row.scrollIntoView({block:'center'});input.focus({preventScroll:true});input.select();
  document.querySelector('[data-preview-status]').textContent='Измените коэффициент для '+author+'. Для применения нажмите «Сохранить коэффициенты».';
 });
 initialize();
})();
