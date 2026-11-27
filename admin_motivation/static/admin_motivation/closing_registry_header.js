/* The page owns vertical scrolling; the full two-row header follows it. */
(() => {
 const wrapper=document.getElementById('closing-coefficients'),table=wrapper?.querySelector('table'),head=table?.tHead;if(!head)return;
 const floating=document.createElement('div');floating.className='pw-grid pw-registry-header';floating.hidden=true;floating.setAttribute('aria-hidden','true');document.body.append(floating);
 let dirty=true,copy,pending=false;
 function rebuild(){
  const bounds=table.getBoundingClientRect(),edges=new Set([0,bounds.width]);
  head.querySelectorAll('th').forEach(cell=>{const r=cell.getBoundingClientRect();edges.add(Math.round((r.left-bounds.left)*100)/100);edges.add(Math.round((r.right-bounds.left)*100)/100);});
  const positions=[...edges].sort((a,b)=>a-b).filter((x,i,all)=>i===0||x-all[i-1]>1),columns=document.createElement('colgroup');
  for(let i=1;i<positions.length;i++){const col=document.createElement('col');col.style.width=(positions[i]-positions[i-1])+'px';columns.append(col);}
  copy=document.createElement('table');copy.style.cssText=`table-layout:fixed;width:${bounds.width}px;min-width:${bounds.width}px;margin:0`;
  const clone=head.cloneNode(true);clone.querySelectorAll('[id]').forEach(el=>el.removeAttribute('id'));
  [...clone.rows].forEach((row,i)=>row.style.height=head.rows[i].getBoundingClientRect().height+'px');
  copy.append(columns,clone);floating.replaceChildren(copy);dirty=false;
 }
 function topOffset(){const bar=document.querySelector('.portal-topbar');if(!bar)return 0;const style=getComputedStyle(bar),rect=bar.getBoundingClientRect();return ['fixed','sticky'].includes(style.position)&&rect.top<=1?Math.max(0,rect.bottom):0;}
 function update(){pending=false;const rect=table.getBoundingClientRect(),frame=wrapper.getBoundingClientRect(),height=head.getBoundingClientRect().height,top=topOffset();
  floating.hidden=!(rect.top<top&&rect.bottom>top&&frame.width>0&&height>0);if(floating.hidden)return;if(dirty)rebuild();
  Object.assign(floating.style,{left:frame.left+'px',top:Math.min(top,rect.bottom-height)+'px',width:wrapper.clientWidth+'px',height:height+'px'});copy.style.transform=`translateX(${-wrapper.scrollLeft}px)`;
 }
 function schedule(rebuild=false){dirty=dirty||rebuild;if(!pending){pending=true;requestAnimationFrame(update);}}
 document.addEventListener('scroll',()=>schedule(),true);window.addEventListener('resize',()=>schedule(true));
 const resize=new ResizeObserver(()=>schedule(true));resize.observe(table);resize.observe(wrapper);const bar=document.querySelector('.portal-topbar');if(bar)resize.observe(bar);
 document.fonts?.ready.then(()=>schedule(true));schedule(true);
})();
