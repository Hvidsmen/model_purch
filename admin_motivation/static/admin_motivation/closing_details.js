(() => {
 const form=document.getElementById('closing-detail-form');
 if(!form)return;
 const money=value=>value==null?'—':Number(value).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2});
 const factor=value=>value==null?'—':Number(value).toLocaleString('ru-RU',{maximumFractionDigits:4});
 const status=document.querySelector('[data-preview-status]');
 let timer,controller,generation=0;
 const cell=(row,value)=>{const td=document.createElement('td');td.textContent=value;row.append(td);};
 const update=data=>{
  document.querySelectorAll('[data-total]').forEach(el=>{const key=el.dataset.total;el.textContent=key==='coefficient'?factor(data[key]):(key==='percent'||key.endsWith('_fact_percent'))?(data[key]==null?'—':money(data[key])+'%'):money(data[key]);});
  const body=document.querySelector('[data-breakdown]');body.replaceChildren();
  [...data.kinds,...data.payments].forEach(item=>{const tr=document.createElement('tr');[item.kind||item.label,money(item.plan),money(item.fact),item.before_fact_percent==null?'—':money(item.before_fact_percent)+'%',item.after_fact_percent==null?'—':money(item.after_fact_percent)+'%',money(item.before),money(item.after),money(item.change),factor(item.coefficient)].forEach(v=>cell(tr,v));body.append(tr);});
  const people=document.querySelector('[data-people-results]');people.replaceChildren();
  const labels={quarter:'Квартальная премия',annual:'Годовая премия',catchup:'Доплата за кварталы'};
  data.rows.forEach(item=>{const tr=document.createElement('tr');cell(tr,item.kind);const name=document.createElement('td');
   if(item.can_edit_person){const button=document.createElement('button');button.type='button';button.className='cd-person-link';button.dataset.editPerson='';button.dataset.kind=item.kind;button.dataset.author=item.author;button.dataset.factor=item.factor;button.textContent=item.author_label;name.append(button);}else name.textContent=item.author_label;
   name.append(document.createTextNode(' · '+labels[item.payment]));tr.append(name);[money(item.plan),money(item.fact),item.before_fact_percent==null?'—':money(item.before_fact_percent)+'%',item.after_fact_percent==null?'—':money(item.after_fact_percent)+'%',money(item.before),factor(item.factor),money(item.after)].forEach(v=>cell(tr,v));people.append(tr);});
 };
 const preview=()=>{
  clearTimeout(timer);controller?.abort();const current=++generation;
  status.textContent='Пересчитываем предварительный итог…';
  timer=setTimeout(async()=>{controller=new AbortController();const data=new FormData(form);data.set('action','preview');
   try{const response=await fetch(location.href,{method:'POST',body:data,signal:controller.signal,credentials:'same-origin'});const result=await response.json();if(current!==generation)return;if(!response.ok)throw Error(result.error||'Проверьте введённые значения.');update(result.comparison);const warning=document.querySelector('[data-metric-error]');warning.textContent=result.metric_error||'';warning.hidden=!result.metric_error;status.textContent='Предварительный итог. Изменения ещё не сохранены.';}
   catch(error){if(error.name!=='AbortError'&&current===generation)status.textContent=error.message;}
  },350);
 };
 form.addEventListener('input',preview);form.addEventListener('change',preview);
 form.addEventListener('submit',()=>{clearTimeout(timer);controller?.abort();++generation;});
 document.querySelectorAll('[data-add-person]').forEach(button=>button.addEventListener('click',()=>{const count=form.querySelector('[name=people-TOTAL_FORMS]'),index=Number(count.value);if(index>=1000)return;
  const template=document.getElementById('closing-empty-person'),fragment=template.content.cloneNode(true);fragment.querySelectorAll('[name],[id],[for]').forEach(el=>{for(const attr of ['name','id','for'])if(el.hasAttribute(attr))el.setAttribute(attr,el.getAttribute(attr).replaceAll('__prefix__',String(index)));});
  fragment.querySelector('[name$="-kind"]').value=button.dataset.addPerson;button.closest('[data-person-card]').querySelector('[data-person-rows]').append(fragment);count.value=index+1;
 }));
 document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>{if(parent!==window)parent.postMessage({closingClose:true},location.origin);else history.back();}));
})();
