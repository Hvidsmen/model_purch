/* Keep the results header visible while the dialog itself scrolls. */
(() => {
 const wrapper=document.querySelector('.cd-person-results'),table=wrapper?.querySelector('table'),head=table?.tHead;if(!head)return;
 const floating=document.createElement('div');floating.className='cd-results-floating';floating.hidden=true;floating.setAttribute('aria-hidden','true');document.body.append(floating);
 let dirty=true,copy,pending=false;
 function rebuild(){const clone=head.cloneNode(true),sources=[...head.querySelectorAll('th')],targets=[...clone.querySelectorAll('th')];
  sources.forEach((source,i)=>{const rect=source.getBoundingClientRect();Object.assign(targets[i].style,{width:rect.width+'px',minWidth:rect.width+'px',maxWidth:rect.width+'px',height:rect.height+'px'});});
  copy=document.createElement('table');copy.style.cssText=`table-layout:fixed;min-width:${table.getBoundingClientRect().width}px;width:${table.getBoundingClientRect().width}px;margin:0`;
  copy.append(clone);floating.replaceChildren(copy);dirty=false;
 }
 function update(){pending=false;const bounds=table.getBoundingClientRect(),frame=wrapper.getBoundingClientRect(),height=head.getBoundingClientRect().height;
  floating.hidden=!(bounds.top<0&&bounds.bottom>0&&height>0&&frame.width>0);if(floating.hidden)return;if(dirty)rebuild();
  Object.assign(floating.style,{left:frame.left+'px',top:Math.min(0,bounds.bottom-height)+'px',width:wrapper.clientWidth+'px',height:height+'px'});copy.style.transform=`translateX(${-wrapper.scrollLeft}px)`;
 }
 function schedule(rebuild=false){dirty=dirty||rebuild;if(!pending){pending=true;requestAnimationFrame(update);}}
 document.addEventListener('scroll',()=>schedule(),true);window.addEventListener('resize',()=>schedule(true));
 const resize=new ResizeObserver(()=>schedule(true));resize.observe(table);resize.observe(wrapper);
 new MutationObserver(()=>schedule(true)).observe(table,{childList:true,subtree:true});document.fonts?.ready.then(()=>schedule(true));schedule(true);
})();
