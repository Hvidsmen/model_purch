(() => {
 if(window.SplitLiveSave)return;
 let scope=new AbortController(),observers=[],frames=[],refreshing=false,pending=null;
 const nativeAdd=EventTarget.prototype.addEventListener;
 const managed=()=>refreshing||/\/split_(?!live_save|unsaved|progress)[^/]+\.js/.test(document.currentScript?.src||'');
 // A refreshed workspace gets fresh controllers. Dispose document listeners and
 // observers from the previous workspace before initializing the new one.
 EventTarget.prototype.addEventListener=function(type,listener,options){
  if(managed()){
   if(type==='DOMContentLoaded'&&refreshing){if(typeof listener==='function')listener.call(this,new Event(type));return;}
   options=typeof options==='boolean'?{capture:options}:{...options};
   if(!options.signal)options.signal=scope.signal;
  }
  return nativeAdd.call(this,type,listener,options);
 };
 for(const name of ['MutationObserver','ResizeObserver']){
  const Original=window[name];
  window[name]=new Proxy(Original,{construct(Target,args){const observer=Reflect.construct(Target,args);if(managed())observers.push(observer);return observer;}});
 }
 const raf=window.requestAnimationFrame.bind(window);
 window.requestAnimationFrame=callback=>{const id=raf(callback);if(managed())frames.push(id);return id;};
 function path(el,root){
  const parts=[];
  while(el&&el!==root){
   if(el.id){parts.unshift('#'+CSS.escape(el.id));break;}
   const identity=['data-group-choice','data-history-year-choice','data-split-metric-choice','data-sales-channel-choice','data-reference-filter','data-plan-group','data-distribution-row','data-stock-group','data-composition-group'].find(key=>el.hasAttribute(key));
   if(identity&&el.getAttribute(identity)){parts.unshift('['+identity+'="'+CSS.escape(el.getAttribute(identity))+'"]');break;}
   parts.unshift(el.tagName.toLowerCase()+':nth-of-type('+([...el.parentElement.children].filter(other=>other.tagName===el.tagName).indexOf(el)+1)+')');el=el.parentElement;
  }
  return parts.join(' > ');
 }
 function capture(root){
  const controls=[...root.querySelectorAll('input[type=checkbox]:not([name]),[data-sales-search],[data-sales-check-filter],[data-stock-search],[data-stock-state],[data-reference-filter],[data-grid-search],[data-grid-channel],[data-month-unit],[data-composition-search],[data-group-picker-search]')]
   .filter(el=>!el.closest('dialog,.split-floating-header')&&!el.hasAttribute('data-history-forecast-choice'))
   .map(el=>({selector:path(el,root),checked:el.type==='checkbox'?el.checked:null,value:el.value,selected:el.multiple?[...el.selectedOptions].map(o=>o.value):null}));
  return {controls,details:[...root.querySelectorAll('details')].map(el=>({selector:path(el,root),open:el.open})),
   expanded:[...root.querySelectorAll('[aria-expanded="true"]')].filter(el=>!el.closest('.split-floating-header')).map(el=>path(el,root)),
   scroll:[...root.querySelectorAll('.split-table-viewport,.split-table-top-scroll')].map(el=>({selector:path(el,root),left:el.scrollLeft})),x:scrollX,y:scrollY};
 }
 function restore(root,state){
  for(const item of state.controls){const el=root.querySelector(item.selector);if(!el)continue;
   if(item.checked!==null){el.checked=item.checked;}else if(item.selected){[...el.options].forEach(o=>o.selected=item.selected.includes(o.value));}else el.value=item.value;
   el.dispatchEvent(new Event(el.type==='search'?'input':'change',{bubbles:true}));
  }
  for(const selector of state.expanded){const el=root.querySelector(selector);if(el&&el.getAttribute('aria-expanded')==='false')el.click();}
  for(const item of state.details){const el=root.querySelector(item.selector);if(el)el.open=item.open;}
  raf(()=>{for(const item of state.scroll){const el=root.querySelector(item.selector);if(el)el.scrollLeft=item.left;}window.scrollTo(state.x,state.y);});
 }
 async function refresh(){
  if(pending)return pending;
  const old=document.querySelector('.split-workspace');if(!old)return;
  const state=capture(old);old.inert=true;
  pending=(async()=>{
   try{
    const response=await fetch(location.href,{credentials:'same-origin',cache:'no-store'});
    const doc=new DOMParser().parseFromString(await response.text(),'text/html'),fresh=doc.querySelector('.split-workspace');
    if(!response.ok||!fresh)throw Error('Не удалось получить актуальные расчёты. Проверьте соединение и вход в портал.');
    const scripts=[...doc.querySelectorAll('script[src]')].map(el=>el.src).filter(src=>/\/split_[^/]+\.js/.test(src)&&!/\/split_(live_save|unsaved|progress)\.js/.test(src));
    scope.abort();observers.forEach(observer=>observer.disconnect());frames.forEach(cancelAnimationFrame);observers=[];frames=[];scope=new AbortController();
    window.SplitUnsaved?.reset();delete window.SplitStockWorkspace;
    old.replaceWith(fresh);refreshing=true;
    for(const src of scripts){await new Promise((resolve,reject)=>{const script=document.createElement('script');script.src=src;script.onload=()=>{script.remove();resolve();};script.onerror=()=>{script.remove();reject(Error('Не удалось загрузить обработчики страницы.'));};document.head.append(script);});}
    refreshing=false;restore(fresh,state);window.SplitUnsaved?.refresh();
    document.querySelector('[data-live-save-error]')?.remove();
   }catch(error){
    refreshing=false;
    const current=document.querySelector('.split-workspace');if(current)current.inert=true;
    let notice=document.querySelector('[data-live-save-error]');if(!notice){notice=document.createElement('div');notice.dataset.liveSaveError='';notice.className='alert alert-danger';notice.setAttribute('role','alert');document.querySelector('.split-workspace')?.before(notice);}
    notice.replaceChildren(document.createTextNode('Сохранение выполнено, но обновить экран не удалось. '+error.message+' '));
    const retry=document.createElement('button');retry.type='button';retry.className='admin-button secondary';retry.textContent='Обновить расчёты';retry.onclick=()=>refresh();notice.append(retry);
   }finally{pending=null;}
  })();
  return pending;
 }
 // Forms without their own AJAX handler (history loading, notes, approval, etc.).
 document.addEventListener('submit',async event=>{
  const form=event.target;
  if(event.defaultPrevented||!form.closest('.split-workspace')||form.method.toLowerCase()!=='post')return;
  event.preventDefault();const root=form.closest('.split-workspace');
  if((form.hasAttribute('data-copy-scenario-form')||form.hasAttribute('data-archive-scenario-form'))&&document.querySelector('.split-unsaved-dock:not([hidden])')){window.SplitProgress?.start(form.hasAttribute('data-archive-scenario-form')?'Архив':'Копирование').fail('Сначала сохраните текущие правки.');return;}
  root.inert=true;
  const progress=window.SplitProgress?.start('Сохранение');
  try{
   const response=await fetch(form.getAttribute('action')||location.href,{method:'POST',credentials:'same-origin',body:new FormData(form,event.submitter)});
   const text=await response.text();
   if(!response.ok)throw Error('Сервер отклонил сохранение (HTTP '+response.status+').');
   if((response.headers.get('Content-Type')||'').includes('application/json')){const result=JSON.parse(text);if(!result.ok)throw Error(result.error||'Сохранение отклонено.');}
   else{const doc=new DOMParser().parseFromString(text,'text/html');if(!doc.querySelector('.split-workspace'))throw Error('Проверьте вход в портал.');const error=doc.querySelector('.alert-danger[role=alert]');if(error)throw Error(error.textContent);}
   progress?.finish('Изменения сохранены');if(new URL(response.url).pathname!==location.pathname){window.SplitUnsaved?.navigate(response.url);return;}await refresh();
  }catch(error){root.inert=false;window.SplitUnsaved?.resume();progress?.fail(error.message);}
 });
 window.SplitLiveSave={refresh};
})();
