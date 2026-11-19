(() => {
  if(window.SplitUnsaved)return;
  const controllers=[];let leaving=false,nextURL='',saving=false;
  const dock=document.createElement('div');dock.className='split-unsaved-dock';dock.hidden=true;
  const status=document.createElement('span');status.setAttribute('role','status');
  const save=document.createElement('button');save.type='button';save.className='admin-button';save.textContent='Сохранить все изменения';save.title='Ctrl+S / Cmd+S';save.setAttribute('aria-keyshortcuts','Control+S Meta+S');
  const cancel=document.createElement('button');cancel.type='button';cancel.className='admin-button secondary';cancel.textContent='Отменить все изменения';dock.append(status,save,cancel);document.body.append(dock);
  new ResizeObserver(()=>document.documentElement.style.setProperty('--split-save-dock-height',dock.hidden?'0px':dock.offsetHeight+'px')).observe(dock);
  const dialog=document.createElement('dialog');dialog.className='split-unsaved-dialog';dialog.setAttribute('aria-label','Несохранённые изменения');
  const title=document.createElement('h2');title.textContent='Сохранить изменения перед переходом?';
  const description=document.createElement('p');description.textContent='На странице есть несохранённые правки.';
  const error=document.createElement('p');error.setAttribute('role','alert');
  const yes=document.createElement('button');yes.type='button';yes.textContent='Сохранить и перейти';yes.className='admin-button';
  const no=document.createElement('button');no.type='button';no.textContent='Перейти без сохранения';no.className='admin-button secondary';
  const stay=document.createElement('button');stay.type='button';stay.textContent='Остаться на странице';stay.className='admin-button secondary';
  dialog.append(title,description,error,yes,no,stay);document.body.append(dialog);
  const dirty=()=>controllers.filter(controller=>controller.dirty());
  function refresh(){
    const changed=dirty(),busy=controllers.some(controller=>controller.busy());
    dock.hidden=!changed.length;document.body.classList.toggle('split-has-unsaved',!!changed.length);
    status.textContent=busy?'Операция выполняется…':'Есть несохранённые изменения';
    save.disabled=yes.disabled=busy||changed.some(controller=>!controller.canSave());cancel.disabled=no.disabled=busy;stay.disabled=saving;
    if(dialog.open && !busy && yes.disabled)error.textContent='Сначала исправьте ошибки ввода. Можно остаться на странице или перейти без сохранения.';
  }
  function navigate(url){leaving=true;location.assign(url||location.href);}
  async function saveAll(url){
    const changed=dirty();if(!changed.length){if(url)navigate(url);return;}
    if(changed.some(controller=>controller.busy()||!controller.canSave()))return;
    // A planning page has one editing controller, preserving a single revision.
    saving=true;refresh();
    try{await changed[0].save(url);}catch(failure){error.textContent=failure.message;status.textContent='Не сохранено: '+failure.message;}finally{saving=false;refresh();}
  }
  save.addEventListener('click',()=>saveAll(null));
  document.addEventListener('keydown',event=>{
    if(!(event.ctrlKey||event.metaKey)||event.altKey||event.shiftKey||event.isComposing||!(event.code==='KeyS'||event.key.toLowerCase()==='s'))return;
    if(!controllers.length&&!document.querySelector('.split-workspace'))return;
    event.preventDefault();
    if(event.repeat||dialog.open||saving||controllers.some(controller=>controller.busy()))return;
    const changed=dirty();
    if(!changed.length){window.SplitProgress?.start('Сохранение').finish('Нет несохранённых изменений.');return;}
    if(changed.some(controller=>!controller.canSave())){
      window.SplitProgress?.start('Сохранение').fail('Исправьте ошибки ввода перед сохранением.');
      document.querySelector('[aria-invalid="true"], .split-workspace input:invalid')?.focus();return;
    }
    saveAll(null);
  });

  cancel.addEventListener('click',()=>{dirty().forEach(controller=>controller.discard());refresh();});
  yes.addEventListener('click',()=>saveAll(nextURL));
  no.addEventListener('click',()=>{dirty().forEach(controller=>controller.discard());dialog.close();navigate(nextURL);});
  stay.addEventListener('click',()=>dialog.close());
  dialog.addEventListener('cancel',event=>{if(saving)event.preventDefault();});
  document.addEventListener('click',event=>{
    const link=event.target.closest('a[href]');
    if(!link||event.defaultPrevented||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||link.target==='_blank'||link.hasAttribute('download'))return;
    const url=new URL(link.href,location.href);
    if(url.origin!==location.origin||url.searchParams.has('export')||(url.pathname===location.pathname&&url.search===location.search&&url.hash)||!dirty().length)return;
    event.preventDefault();event.stopImmediatePropagation();nextURL=url.href;error.textContent='';refresh();dialog.showModal();stay.focus();
  },true);
  window.addEventListener('beforeunload',event=>{if(!leaving&&dirty().length){event.preventDefault();event.returnValue='';}});
  window.SplitUnsaved={register(controller){controllers.push(controller);refresh();},refresh,navigate,allow(){leaving=true;}};
})();
