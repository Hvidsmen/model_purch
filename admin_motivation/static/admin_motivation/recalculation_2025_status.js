/* Refresh only the pinned run's GET page; never resubmit/start a calculation. */
(() => {
 const timers=[...document.querySelectorAll('[data-elapsed-seconds]')];
 const loadedAt=performance.now();
 const format=seconds=>{
  const hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60),rest=seconds%60;
  return (hours?`${hours} ч `:'')+`${minutes} мин ${rest} сек`;
 };
 const updateTimers=()=>timers.forEach(node=>{
  const initial=Number(node.dataset.elapsedSeconds);
  if(!Number.isFinite(initial))return;
  const seconds=initial+(node.dataset.elapsedTicking==='true'?Math.floor((performance.now()-loadedAt)/1000):0);
  node.textContent=format(seconds);
 });
 updateTimers();if(timers.some(node=>node.dataset.elapsedTicking==='true'))setInterval(updateTimers,1000);
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
