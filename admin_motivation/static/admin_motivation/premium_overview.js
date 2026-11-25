/* Change the visible motivation kind without changing the shared total or plan. */
(() => {
 document.querySelectorAll('[data-premium-overview]').forEach(root => {
  const tabs=[...root.querySelectorAll('[data-overview-tab]')];
  if(!tabs.length)return;
  const select=tab=>{
   const kind=tab.dataset.overviewTab;
   tabs.forEach(button=>{const active=button===tab;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;});
   root.querySelectorAll('[data-overview-kind]').forEach(panel=>panel.hidden=panel.dataset.overviewKind!==kind);
   let bases=0;
   root.querySelectorAll('[data-overview-bases] [data-performance-kind]').forEach(section=>{const active=section.dataset.performanceKind===kind;section.hidden=!active;if(active)bases++;});
   const missing=root.querySelector('[data-overview-no-base]');if(missing)missing.hidden=!!bases;
  };
  tabs.forEach((tab,index)=>{
   tab.addEventListener('click',()=>select(tab));
   tab.addEventListener('keydown',event=>{
    let next;
    if(event.key==='ArrowRight')next=(index+1)%tabs.length;
    if(event.key==='ArrowLeft')next=(index-1+tabs.length)%tabs.length;
    if(event.key==='Home')next=0;
    if(event.key==='End')next=tabs.length-1;
    if(next!==undefined){event.preventDefault();select(tabs[next]);tabs[next].focus();}
   });
  });
  select(tabs.find(tab=>tab.dataset.overviewTab==='Политики')||tabs[0]);
 });
})();
