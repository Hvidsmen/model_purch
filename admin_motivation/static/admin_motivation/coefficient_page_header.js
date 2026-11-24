(() => {
 const table=document.getElementById('coefficient-table');
 if(!table||!table.tHead)return;
 const viewport=table.closest('.table-wrapper');if(!viewport)return;
 viewport.classList.add('coefficient-page-scroll');
 const head=table.tHead;
 const floating=document.createElement('div');floating.className='coefficient-page-floating';floating.hidden=true;floating.setAttribute('aria-hidden','true');viewport.after(floating);
 const properties=['display','boxSizing','font','lineHeight','color','backgroundColor','padding','borderTop','borderRight','borderBottom','borderLeft','whiteSpace','textAlign','verticalAlign','textTransform','letterSpacing'];
 let copy,pinned=[],dirty=true,pending=false;
 function rebuild(rect){
  pinned=[];copy=table.cloneNode(false);copy.removeAttribute('id');
  const clone=head.cloneNode(true),sources=[head,...head.querySelectorAll('*')],targets=[clone,...clone.querySelectorAll('*')];
  sources.forEach((source,i)=>{
   const target=targets[i],css=getComputedStyle(source),bounds=source.getBoundingClientRect();
   properties.forEach(p=>target.style[p]=css[p]);
   target.removeAttribute('id');target.removeAttribute('name');
   if(css.position==='sticky'&&css.left!=='auto')pinned.push({source,target});
   Object.assign(target.style,{position:'static',top:'auto',left:'auto'});
   if(source.tagName==='TH')Object.assign(target.style,{width:bounds.width+'px',height:bounds.height+'px'});
  });
  const columns=document.createElement('colgroup');
  // Row-spanning classification columns precede the leaf K0-K4 columns.
  [...head.rows].forEach((row,index)=>[...row.cells].forEach(cell=>{
   if(getComputedStyle(cell).display==='none'||!(cell.rowSpan>1||index===head.rows.length-1))return;
   const col=document.createElement('col');col.style.width=cell.getBoundingClientRect().width+'px';columns.append(col);
  }));
  copy.append(columns,clone);
  Object.assign(copy.style,{width:rect.width+'px',minWidth:rect.width+'px',maxWidth:rect.width+'px',tableLayout:'fixed',margin:'0',borderCollapse:getComputedStyle(table).borderCollapse,borderSpacing:getComputedStyle(table).borderSpacing});
  floating.replaceChildren(copy);dirty=false;
 }
 function update(){
  pending=false;
  const frame=viewport.getBoundingClientRect(),rect=table.getBoundingClientRect(),header=head.getBoundingClientRect();
  const active=header.top<0&&rect.bottom>0&&header.height>0&&frame.width>0;
  floating.hidden=!active;if(!active)return;
  if(dirty)rebuild(rect);
  Object.assign(floating.style,{left:frame.left+'px',top:Math.min(0,rect.bottom-header.height)+'px',width:viewport.clientWidth+'px',height:header.height+'px'});
  copy.style.transform='translateX('+(-viewport.scrollLeft)+'px)';
  pinned.forEach(({source,target})=>{
   target.style.transform='none';target.style.position='relative';target.style.zIndex='5';
   target.style.transform='translateX('+(source.getBoundingClientRect().left-target.getBoundingClientRect().left)+'px)';
  });
 }
 function schedule(rebuild=false){dirty=dirty||rebuild;if(!pending){pending=true;requestAnimationFrame(update);}}
 window.addEventListener('scroll',()=>schedule(),{capture:true,passive:true});
 window.addEventListener('resize',()=>schedule(true));
 document.addEventListener('change',()=>schedule(true));document.addEventListener('toggle',()=>schedule(true),true);
 const observer=new ResizeObserver(()=>schedule(true));observer.observe(table);observer.observe(viewport);
 new MutationObserver(()=>schedule(true)).observe(table,{subtree:true,childList:true,attributes:true,attributeFilter:['style','class','colspan','hidden']});
 floating.addEventListener('wheel',event=>{const dx=event.deltaX||(event.shiftKey?event.deltaY:0);if(dx){event.preventDefault();viewport.scrollLeft+=dx;schedule();}},{passive:false});
 document.fonts?.ready.then(()=>schedule(true));schedule(true);
})();
