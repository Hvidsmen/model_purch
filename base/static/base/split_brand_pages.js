(() => {
 const pager=document.querySelector('[data-brand-pages]');if(!pager)return;
 const picker=pager.querySelector('[data-brand-multiselect]');
 if(picker){
  const choices=[...picker.querySelectorAll('[data-brand-choice]')];
  picker.querySelector('[data-brands-all]').onclick=()=>choices.forEach(input=>input.checked=true);
  picker.querySelector('[data-brands-none]').onclick=()=>choices.forEach(input=>input.checked=false);
  picker.querySelector('[data-brand-search]').oninput=event=>choices.forEach(input=>{input.closest('label').hidden=!input.closest('label').textContent.toLocaleLowerCase('ru').includes(event.target.value.trim().toLocaleLowerCase('ru'));});
  picker.querySelector('[data-brands-apply]').onclick=()=>{
   const selected=choices.filter(input=>input.checked).map(input=>input.dataset.brandChoice);
   picker.querySelector('[data-brand-error]').hidden=selected.length>0;if(!selected.length)return;
   const url=new URL(location.href);url.searchParams.set('brand_page',selected.length===choices.length?'_all_':selected.length===1?selected[0]:JSON.stringify(selected.sort()));
   for(const key of ['page','focus','expand','group','export','filter_group'])url.searchParams.delete(key);
   const link=document.createElement('a');link.href=url.href;link.hidden=true;document.body.append(link);link.click();link.remove();
  };
  document.addEventListener('click',event=>{if(!picker.contains(event.target))picker.open=false;});
  picker.addEventListener('keydown',event=>{if(event.key==='Escape'){picker.open=false;picker.querySelector('summary').focus();}});
 }
 // Links remain ordinary links so the shared unsaved-change guard can save
 // the current page before navigating to another brand or workflow step.
 document.querySelectorAll('.split-workspace a[href]').forEach(link=>{
  const url=new URL(link.href,location.href);
  if(url.origin===location.origin&&url.pathname===location.pathname&&url.searchParams.has('tab')&&!url.searchParams.has('brand_page')&&!url.searchParams.has('export')&&!url.searchParams.has('group')&&!url.searchParams.has('sales_view')){
   url.searchParams.set('brand_page',pager.dataset.brandPages);link.href=url.pathname+url.search+url.hash;
  }
 });
})();

(() => {
 const picker=document.querySelector('[data-distribution-channel-picker]');if(!picker)return;
 const choices=[...picker.querySelectorAll('[name=filter_channel]')],caption=picker.querySelector('[data-distribution-channel-caption]');
 function update(){const count=choices.filter(input=>input.checked).length;caption.textContent=count===choices.length?'Все':count?'Выбрано: '+count:'Не выбраны';}
 choices.forEach(input=>input.addEventListener('change',update));
 picker.querySelector('[data-distribution-channels-all]').onclick=()=>{choices.forEach(input=>input.checked=true);update();};
 picker.querySelector('[data-distribution-channels-none]').onclick=()=>{choices.forEach(input=>input.checked=false);update();};
 picker.querySelector('[data-distribution-channel-search]').oninput=event=>choices.forEach(input=>input.closest('label').hidden=!input.closest('label').textContent.toLocaleLowerCase('ru').includes(event.target.value.trim().toLocaleLowerCase('ru')));
 document.addEventListener('click',event=>{if(!picker.contains(event.target))picker.open=false;});
 picker.addEventListener('keydown',event=>{if(event.key==='Escape'){picker.open=false;picker.querySelector('summary').focus();}});
 picker.closest('form').addEventListener('submit',event=>{
  event.preventDefault();const link=document.createElement('a');link.href=location.pathname+'?'+new URLSearchParams(new FormData(picker.closest('form')));link.hidden=true;document.body.append(link);link.click();link.remove();
 });
 update();
})();
