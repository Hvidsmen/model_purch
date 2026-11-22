(() => {
 const root=document.querySelector('.model-ddp');if(!root)return;
 const forms=[...root.querySelectorAll('[data-ddp-edit]')],status=root.querySelector('[data-ddp-dirty-status]');let leaving=false;
 const snapshot=form=>JSON.stringify([...new FormData(form)].filter(([key])=>key!=='csrfmiddlewaretoken'));
 const original=new Map(forms.map(form=>[form,snapshot(form)]));
 const dirty=()=>forms.filter(form=>snapshot(form)!==original.get(form));
 root.addEventListener('input',()=>{if(status)status.textContent=dirty().length?'Есть несохранённые параметры. Сохраните их соответствующей кнопкой.':'';});
 root.addEventListener('keydown',event=>{
  if(event.key==='Enter'&&event.target.matches('input:not([type=submit]):not([type=button])')&&event.target.form?.method.toLowerCase()==='post')event.preventDefault();
 });
 root.addEventListener('submit',event=>{
  if(event.target.method.toLowerCase()!=='post')return;
  if(dirty().some(form=>form!==event.target)){event.preventDefault();if(status)status.textContent='Сначала сохраните изменённые параметры в другой форме.';status?.scrollIntoView({block:'center'});return;}
  leaving=true;
 });
 window.addEventListener('beforeunload',event=>{if(!leaving&&dirty().length){event.preventDefault();event.returnValue='';}});
})();
