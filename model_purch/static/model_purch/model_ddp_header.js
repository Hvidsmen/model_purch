(() => {
 const root=document.querySelector('.model-ddp'),viewport=root?.querySelector('.ddp-table-scroll'),table=viewport?.querySelector('.ddp-table'),head=table?.tHead;
 if(!head)return;
 const floating=document.createElement('div');floating.className='ddp-floating-header';floating.hidden=true;floating.setAttribute('aria-hidden','true');root.append(floating);
 let signature='',copyTable,pending=false;
 function update(){
  pending=false;
  const frame=viewport.getBoundingClientRect(),rect=table.getBoundingClientRect(),header=head.getBoundingClientRect();
  const panel=root.querySelector('[data-filter-panel]'),panelRect=panel?.getBoundingClientRect();
  const offset=panelRect&&panelRect.top<=1?Math.max(0,panelRect.bottom):0;
  const active=header.top<offset&&rect.bottom>offset&&frame.width>0&&header.height>0;
  floating.hidden=!active;if(!active)return;
  const widths=[...head.rows[0].cells].map(cell=>cell.getBoundingClientRect().width);
  const next=JSON.stringify(widths)+head.innerHTML;
  if(next!==signature){
   signature=next;copyTable=table.cloneNode(false);copyTable.removeAttribute('id');
   const cols=document.createElement('colgroup');
   widths.forEach(width=>{const col=document.createElement('col');col.style.width=width+'px';cols.append(col);});
   const copy=head.cloneNode(true);
   copy.querySelectorAll('[id],[name]').forEach(element=>{element.removeAttribute('id');element.removeAttribute('name');});
   copyTable.append(cols,copy);
   Object.assign(copyTable.style,{width:rect.width+'px',minWidth:rect.width+'px',tableLayout:'fixed'});
   [...copy.querySelectorAll('th')].forEach((cell,index)=>{
    const original=head.rows[0].cells[index],style=getComputedStyle(original);
    for(const name of ['padding','font','lineHeight','textAlign','verticalAlign','backgroundColor','color','border'])cell.style[name]=style[name];
    cell.style.minWidth='0';cell.style.height=header.height+'px';
   });
   floating.replaceChildren(copyTable);
  }
  Object.assign(floating.style,{left:frame.left+'px',top:Math.min(offset,rect.bottom-header.height)+'px',width:viewport.clientWidth+'px',height:header.height+'px'});
  copyTable.style.transform='translateX('+(-viewport.scrollLeft)+'px)';
 }
 function schedule(){if(!pending){pending=true;requestAnimationFrame(update);}}
 window.addEventListener('scroll',schedule,{passive:true,capture:true});
 window.addEventListener('resize',schedule);
 floating.addEventListener('wheel',event=>{const dx=event.deltaX||(event.shiftKey?event.deltaY:0);if(dx){event.preventDefault();viewport.scrollLeft+=dx;schedule();}},{passive:false});
 const panel=root.querySelector('[data-filter-panel]');if(panel)new ResizeObserver(schedule).observe(panel);
 new ResizeObserver(schedule).observe(table);new ResizeObserver(schedule).observe(viewport);
 document.fonts?.ready.then(schedule);schedule();
})();
