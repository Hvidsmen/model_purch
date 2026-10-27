(() => {
 const form=document.getElementById('portal-user-form');
 if(!form)return;
 const snapshot=()=>JSON.stringify([...new FormData(form)].filter(([key])=>key!=='csrfmiddlewaretoken'));
 const original=snapshot();let submitting=false;
 const refresh=()=>{const dirty=(snapshot()!==original||form.dataset.restored==='yes');const note=document.getElementById('access-pending-note');if(note)note.hidden=!dirty;form.dataset.dirty=dirty?'yes':'no';};
 form.addEventListener('input',refresh);form.addEventListener('change',refresh);
 form.addEventListener('submit',()=>{submitting=true;});
 window.addEventListener('beforeunload',event=>{if(!submitting&&(snapshot()!==original||form.dataset.restored==='yes')){event.preventDefault();event.returnValue='';}});
})();
