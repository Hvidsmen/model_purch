(() => {
 const form=document.getElementById('split-composition-form');if(!form)return;
 const dialog=form.querySelector('dialog'),status=form.querySelector('[data-composition-status]');let busy=false;
 const initialLinkCaptions=new Map([...form.querySelectorAll('[data-composition-links]')].map(button=>[button,button.textContent]));
 form.addEventListener('reset',()=>initialLinkCaptions.forEach((caption,button)=>button.textContent=caption));
 const linksDialog=document.querySelector('.split-composition-links-dialog'),patchInput=form.elements.composition_links;
 let activeLinkGroup='',linkData=null,loading=false;
 const linkSections=linksDialog.querySelector('[data-links-sections]'),linkError=linksDialog.querySelector('[data-links-error]'),linkApply=linksDialog.querySelector('[data-links-apply]');
 form.querySelectorAll('[data-composition-links]').forEach(button=>button.addEventListener('click',async()=>{
  if(loading||busy)return;loading=true;activeLinkGroup=button.dataset.compositionLinks;linkData=null;linkSections.replaceChildren();linkError.textContent='';linkApply.disabled=true;linksDialog.querySelector('[data-links-search]').value='';linksDialog.showModal();
  try{
   const data=new FormData();data.set('action','composition_links_catalog');data.set('link_group',activeLinkGroup);data.set('revision',form.elements.revision.value);data.set('csrfmiddlewaretoken',form.elements.csrfmiddlewaretoken.value);
   const response=await fetch(location.href,{method:'POST',body:data});const result=await response.json();if(!response.ok||!result.ok)throw Error(result.error||'Не удалось загрузить связи.');
   linkData=result;linksDialog.querySelector('[data-links-title]').textContent=result.name;
   const staged=JSON.parse(patchInput.value)[activeLinkGroup];
   result.sections.forEach(section=>{
    const box=document.createElement('section'),title=document.createElement('h3');title.textContent=section.label;box.append(title);
    const selected=new Set(staged?.[section.key]||section.selected);
    section.choices.forEach(choice=>{const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.dataset.linkKind=section.key;input.value=choice.id;input.checked=selected.has(choice.id);label.append(input,document.createTextNode(choice.name+(choice.active?'':' · неактивна')));box.append(label);});
    linkSections.append(box);
   });linkApply.disabled=false;
  }catch(error){linkError.textContent=error.message;}finally{loading=false;}
 }));
 linksDialog.querySelector('[data-links-search]').addEventListener('input',event=>{const text=event.target.value.trim().toLocaleLowerCase('ru');linkSections.querySelectorAll('label').forEach(label=>label.hidden=!label.textContent.toLocaleLowerCase('ru').includes(text));});
 linksDialog.querySelector('[data-links-close]').onclick=()=>linksDialog.close();
 linkApply.onclick=()=>{
  if(!linkData)return;const patches=JSON.parse(patchInput.value),values={};
  linkData.sections.forEach(section=>values[section.key]=[...linkSections.querySelectorAll('input:checked')].filter(input=>input.dataset.linkKind===section.key).map(input=>input.value));
  patches[activeLinkGroup]=values;patchInput.value=JSON.stringify(patches);
  const button=[...form.querySelectorAll('[data-composition-links]')].find(button=>button.dataset.compositionLinks===activeLinkGroup);button.textContent='Связанные: '+values.related_groups.length+' · Замещающие: '+values.replacement_groups.length+' · Не сохранено';
  linksDialog.close();update();
 };
 const snapshot=()=>JSON.stringify([...new FormData(form)].filter(([key])=>!['csrfmiddlewaretoken','revision'].includes(key)));
 let original=snapshot();
 const dirty=()=>snapshot()!==original;
 form.querySelector('[data-composition-add]').addEventListener('click',()=>dialog.showModal());
 form.querySelector('[data-composition-close]').addEventListener('click',()=>dialog.close());
 form.querySelectorAll('[data-composition-search]').forEach(input=>input.addEventListener('input',()=>{
  const list=form.querySelector('[data-composition-list="'+input.dataset.compositionSearch+'"]');
  [...list.children].forEach(row=>row.hidden=!row.textContent.toLocaleLowerCase().includes(input.value.trim().toLocaleLowerCase()));
 }));
 const channelsPicker=form.querySelector('.split-composition-channels');
 document.addEventListener('click',event=>{if(!channelsPicker.contains(event.target))channelsPicker.open=false;});
 channelsPicker.addEventListener('keydown',event=>{if(event.key==='Escape'){channelsPicker.open=false;channelsPicker.querySelector('summary').focus();}});
 function update(){form.querySelector('[data-composition-channel-count]').textContent=form.querySelectorAll('[name=channels]:checked').length;form.querySelector('[data-composition-add-count]').textContent='Выбрано групп: '+form.querySelectorAll('[name=added_groups]:checked').length;window.SplitUnsaved?.refresh();}
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
