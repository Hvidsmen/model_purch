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
    const floating=document.createElement('div');floating.className='split-floating-header';floating.hidden=true;
    (table.closest('[data-stock-workspace]')||table.closest('.split-workspace'))?.append(floating);
    let signature='',controls=[];
    function fixedHeader(){
      const head=table.tHead,rect=viewport.getBoundingClientRect(),body=table.getBoundingClientRect(),h=head.getBoundingClientRect();
      const childActive=[...table.querySelectorAll('table')].some(inner=>!inner.closest('.split-floating-header')&&inner.tHead&&inner.tHead.getBoundingClientRect().height>4&&inner.tHead.getBoundingClientRect().top<0&&inner.getBoundingClientRect().bottom>0);
      const active=!childActive&&!table.closest('dialog')&&h.height>4&&h.top<0&&body.bottom>0&&rect.width>0;
      floating.hidden=!active;if(!active)return;
      floating.style.left=rect.left+'px';floating.style.top=Math.min(0,body.bottom-h.height)+'px';
      floating.style.width=viewport.clientWidth+'px';floating.style.height=h.height+'px';
      const widths=[],occupied=[];
      [...head.rows].forEach((row,r)=>{
        occupied[r]??=[];let col=0;
        [...row.cells].filter(cell=>getComputedStyle(cell).display!=='none').forEach(cell=>{
          while(occupied[r][col])col++;
          const span=cell.colSpan,w=cell.getBoundingClientRect().width/span;
          for(let i=0;i<span;i++)if(cell.colSpan===1||widths[col+i]==null)widths[col+i]=w;
          for(let j=0;j<cell.rowSpan;j++){occupied[r+j]??=[];for(let i=0;i<span;i++)occupied[r+j][col+i]=true;}
          col+=span;
        });
      });
      const state=[...head.querySelectorAll('input,select')].map(el=>[el.value,el.checked,el.indeterminate]);
      const next=head.outerHTML+'|'+widths.map(w=>w.toFixed(2)).join(',')+'|'+JSON.stringify(state);
      if(next!==signature){
        signature=next;const clone=table.cloneNode(false);clone.removeAttribute('id');
        [...clone.attributes].filter(a=>a.name.startsWith('data-')).forEach(a=>clone.removeAttribute(a.name));
        const columns=document.createElement('colgroup');widths.forEach(w=>{const col=document.createElement('col');col.style.width=w+'px';columns.append(col);});
        const copy=head.cloneNode(true);controls=[...head.querySelectorAll('button,[role=button],summary,a,input,select')];
        const copiedControls=[...copy.querySelectorAll('button,[role=button],summary,a,input,select')];
        [copy,...copy.querySelectorAll('*')].forEach(el=>{[...el.attributes].filter(a=>a.name==='id'||a.name==='name'||a.name.startsWith('data-')&&!['data-split-metric-off','data-sales-channel-filtered'].includes(a.name)).forEach(a=>el.removeAttribute(a.name));});
        copiedControls.forEach((el,i)=>{el.dataset.floatingControl=String(i);if('checked' in el){el.checked=controls[i].checked;el.indeterminate=controls[i].indeterminate;}if('value' in el)el.value=controls[i].value;});
        [...copy.rows].forEach((row,i)=>row.style.height=head.rows[i].getBoundingClientRect().height+'px');
        clone.style.width=widths.reduce((a,b)=>a+b,0)+'px';clone.style.minWidth=clone.style.width;clone.style.tableLayout='fixed';
        clone.append(columns,copy);floating.replaceChildren(clone);
      }
      floating.scrollLeft=viewport.scrollLeft;
    }
    floating.addEventListener('click',event=>{const control=event.target.closest('[data-floating-control]');if(control){if(!control.matches('input[type=checkbox],input[type=radio]'))event.preventDefault();controls[Number(control.dataset.floatingControl)]?.click();sync();}});
    floating.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)&&event.target.matches('[data-floating-control]')){event.preventDefault();controls[Number(event.target.dataset.floatingControl)]?.click();sync();}});
    floating.addEventListener('wheel',event=>{const dx=event.deltaX||(event.shiftKey?event.deltaY:0);if(dx){event.preventDefault();viewport.scrollLeft+=dx;sync();}},{passive:false});
    window.addEventListener('scroll',sync,{passive:true});window.addEventListener('resize',sync);
    viewport.addEventListener('scroll',sync,{passive:true});table.addEventListener('change',sync);
    let pending=false;
    function sync(){
      if(pending)return;pending=true;
      requestAnimationFrame(()=>{
        pending=false;track.style.width=viewport.scrollWidth+'px';bar.style.width=viewport.clientWidth+'px';
        bar.hidden=viewport.scrollWidth<=viewport.clientWidth+1;bar.scrollLeft=viewport.scrollLeft;fixedHeader();
      });
    }
    new ResizeObserver(sync).observe(table);new ResizeObserver(sync).observe(viewport);
    new MutationObserver(sync).observe(table,{subtree:true,childList:true,attributes:true,attributeFilter:['hidden','colspan','style','aria-expanded','class']});
    sync();
  });
})();
