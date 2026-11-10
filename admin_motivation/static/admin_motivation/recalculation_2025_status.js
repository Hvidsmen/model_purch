/* Refresh only the pinned run's GET page; never resubmit/start a calculation. */
(() => {
 const root=document.querySelector('[data-recalculation-state]');
 if(!root||!['running','queued','unknown'].includes(root.dataset.recalculationState))return;
 let dirty=Boolean(document.querySelector('form .errorlist'));
 document.querySelectorAll('form').forEach(form=>{
  form.addEventListener('input',()=>{dirty=true;});
  form.addEventListener('change',()=>{dirty=true;});
 });
 setInterval(()=>{
  if(document.hidden||dirty||['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName))return;
  const url=new URL(location.href);url.searchParams.set('job',root.dataset.recalculationJob);
  location.replace(url.toString());
 },10000);
})();
