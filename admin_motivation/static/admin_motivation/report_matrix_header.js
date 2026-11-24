/* Keep one page scrollbar; mirror only the noninteractive matrix heading. */
(() => {
 document.querySelectorAll('.nr-matrix[data-matrix-periods]').forEach(matrix=>{
  const viewport=matrix.querySelector('.nr-product-scroll'),inner=viewport?.querySelector('.nr-matrix-inner'),head=inner?.querySelector('.nr-matrix-head');
  if(!head)return;
  const floating=matrix.cloneNode(false);floating.removeAttribute('id');floating.removeAttribute('data-matrix-periods');floating.classList.add('nr-matrix-floating');floating.setAttribute('aria-hidden','true');floating.hidden=true;matrix.after(floating);
  let pending=false,signature='',copy;
  function update(){
   pending=false;
   const frame=viewport.getBoundingClientRect(),rect=inner.getBoundingClientRect(),header=head.getBoundingClientRect();
   const active=header.top<0&&rect.bottom>0&&frame.width>0&&header.height>0;
   floating.hidden=!active;if(!active)return;
   const next=matrix.style.cssText+'|'+matrix.dataset.mode+'|'+rect.width+'|'+head.innerHTML;
   if(next!==signature){
    signature=next;floating.style.cssText=matrix.style.cssText;
    if(matrix.dataset.mode)floating.dataset.mode=matrix.dataset.mode;else delete floating.dataset.mode;
    copy=inner.cloneNode(false);copy.append(head.cloneNode(true));
    copy.querySelectorAll('[id],[name]').forEach(el=>{el.removeAttribute('id');el.removeAttribute('name');});
    Object.assign(copy.style,{width:rect.width+'px',minWidth:rect.width+'px',boxSizing:'border-box'});
    floating.replaceChildren(copy);
   }
   Object.assign(floating.style,{left:frame.left+'px',top:Math.min(0,rect.bottom-header.height)+'px',width:viewport.clientWidth+'px',height:header.height+'px'});
   copy.style.transform='translateX('+(-viewport.scrollLeft)+'px)';
  }
  function schedule(){if(!pending){pending=true;requestAnimationFrame(update);}}
  window.addEventListener('scroll',schedule,{passive:true,capture:true});window.addEventListener('resize',schedule);
  matrix.addEventListener('toggle',schedule,true);
  new ResizeObserver(schedule).observe(inner);new ResizeObserver(schedule).observe(viewport);
  new MutationObserver(schedule).observe(matrix,{attributes:true,attributeFilter:['style','data-mode']});
  floating.addEventListener('wheel',event=>{const dx=event.deltaX||(event.shiftKey?event.deltaY:0);if(dx){event.preventDefault();viewport.scrollLeft+=dx;schedule();}},{passive:false});
  document.fonts?.ready.then(schedule);schedule();
 });
})();
