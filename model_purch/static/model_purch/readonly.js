/* Presentation only; every write is denied independently by server middleware. */
(() => {
 const root=document.querySelector('.purchases-readonly');if(!root)return;
 root.querySelectorAll('form[method="post"] input,form[method="post"] select,form[method="post"] textarea,form[method="post"] button').forEach(control=>control.disabled=true);
 root.addEventListener('submit',event=>{if(event.target.method?.toLowerCase()==='post'){event.preventDefault();event.stopImmediatePropagation();}},true);
})();
