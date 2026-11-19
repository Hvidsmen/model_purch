(() => {
  document.querySelectorAll('#portal-main table').forEach(table => {
    if(!table.tHead)return;
    let viewport=table.parentElement;
    if(!viewport.matches('.admin-table-scroll, .split-month-table-wrap, .split-stock-table-scroll, .split-table-viewport') || [...viewport.children].filter(child=>child.tagName==='TABLE').length>1){
      viewport=document.createElement('div');table.before(viewport);viewport.append(table);
    }
    if(viewport.dataset.tableScrollReady)return;
    viewport.dataset.tableScrollReady='true';viewport.classList.add('split-table-viewport');
    const bar=document.createElement('div');bar.className='split-table-top-scroll';bar.tabIndex=0;
    bar.setAttribute('role','region');bar.setAttribute('aria-label','Горизонтальная прокрутка таблицы');
    const track=document.createElement('div');bar.append(track);viewport.before(bar);
    bar.addEventListener('scroll',()=>{if(viewport.scrollLeft!==bar.scrollLeft)viewport.scrollLeft=bar.scrollLeft;});
    viewport.addEventListener('scroll',()=>{if(bar.scrollLeft!==viewport.scrollLeft)bar.scrollLeft=viewport.scrollLeft;});
    let pending=false;
    function sync(){
      if(pending)return;pending=true;
      requestAnimationFrame(()=>{
        pending=false;track.style.width=viewport.scrollWidth+'px';bar.style.width=viewport.clientWidth+'px';
        bar.hidden=viewport.scrollWidth<=viewport.clientWidth+1;bar.scrollLeft=viewport.scrollLeft;
      });
    }
    new ResizeObserver(sync).observe(table);new ResizeObserver(sync).observe(viewport);
    new MutationObserver(sync).observe(table,{subtree:true,childList:true,attributes:true,attributeFilter:['hidden','colspan','style']});
    sync();
  });
})();
