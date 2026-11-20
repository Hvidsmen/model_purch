(() => {
 const form=document.getElementById('split-composition-form');if(!form)return;
 const dialog=form.querySelector('dialog'),status=form.querySelector('[data-composition-status]');let busy=false;
 const snapshot=()=>JSON.stringify([...new FormData(form)].filter(([key])=>!['csrfmiddlewaretoken','revision'].includes(key)));
 let original=snapshot();
 const dirty=()=>snapshot()!==original;
 form.querySelector('[data-composition-add]').addEventListener('click',()=>dialog.showModal());
 form.querySelector('[data-composition-close]').addEventListener('click',()=>dialog.close());
 form.querySelectorAll('[data-composition-search]').forEach(input=>input.addEventListener('input',()=>{
  const list=form.querySelector('[data-composition-list="'+input.dataset.compositionSearch+'"]');
  [...list.children].forEach(row=>row.hidden=!row.textContent.toLocaleLowerCase().includes(input.value.trim().toLocaleLowerCase()));
 }));
 function update(){form.querySelector('[data-composition-add-count]').textContent='Выбрано групп: '+form.querySelectorAll('[name=added_groups]:checked').length;window.SplitUnsaved?.refresh();}
 form.addEventListener('change',update);
 async function save(url,operation='save'){
  if(busy)return;busy=true;window.SplitUnsaved?.refresh();const progress=window.SplitProgress?.start('Сохранение состава плана');
  try{
   const data=new FormData(form);data.set('composition_operation',operation);
   const response=await fetch(location.href,{method:'POST',body:data,headers:{'X-Requested-With':'XMLHttpRequest'}});
   const text=await response.text();let result;try{result=JSON.parse(text);}catch{throw new Error('Сервер не вернул результат сохранения. Проверьте соединение и вход в портал.');}
   if(!response.ok||!result.ok)throw new Error(result.error||'Не удалось сохранить состав.');
   original=snapshot();progress?.finish('Состав сохранён.');window.SplitUnsaved?.navigate(url||location.href);
  }catch(error){status.textContent=error.message;progress?.fail(error.message);throw error;}
  finally{busy=false;window.SplitUnsaved?.refresh();}
 }
 form.addEventListener('submit',event=>{event.preventDefault();const operation=event.submitter?.value||'save';dialog.close();save(null,operation).catch(()=>{});});
 window.SplitUnsaved?.register({dirty,busy:()=>busy,canSave:()=>form.checkValidity(),save,discard(){form.reset();original=snapshot();update();}});
 update();
})();
