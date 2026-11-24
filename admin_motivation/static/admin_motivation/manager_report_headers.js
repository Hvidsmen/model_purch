(() => {
 const styles=['display','gridTemplateColumns','gridAutoFlow','gap','alignItems','justifyContent','font','lineHeight','color','backgroundColor','padding','margin','borderTop','borderRight','borderBottom','borderLeft','boxSizing','width','height','minWidth','maxWidth','whiteSpace','textAlign','verticalAlign','textTransform','letterSpacing'];
 const panels=[...document.querySelectorAll('.manager-period-summary>.report-scroll')].map(viewport=>({viewport,body:viewport.querySelector('table'),head:viewport.querySelector('thead')}));
 const viewport=document.getElementById('manager-detail-scroll'),body=document.getElementById('manager-detail-report');
 if(viewport&&body)panels.push({viewport,body,head:body.querySelector('.manager-sticky-header')});
 panels.forEach(({viewport,body,head})=>{
  if(!body||!head)return;
  const floating=document.createElement('div');floating.className='manager-report-floating';floating.hidden=true;floating.setAttribute('aria-hidden','true');viewport.parentElement.append(floating);
  let copy,signature='',pending=false,pinned=[];
  function update(){
   pending=false;
   const frame=viewport.getBoundingClientRect(),rect=body.getBoundingClientRect(),header=head.getBoundingClientRect();
   const active=header.top<0&&rect.bottom>0&&header.height>0&&frame.width>0;
   floating.hidden=!active;if(!active)return;
   const next=rect.width+'|'+header.width+'|'+header.height+'|'+body.style.cssText+'|'+head.innerHTML;
   if(signature!==next){
    signature=next;pinned=[];
    const cloned=head.cloneNode(true),sources=[head,...head.querySelectorAll('*')],targets=[cloned,...cloned.querySelectorAll('*')];
    sources.forEach((source,index)=>{
     const target=targets[index],computed=getComputedStyle(source);
     styles.forEach(name=>target.style[name]=computed[name]);
     if(computed.position==='sticky'&&computed.left!=='auto')pinned.push({source,target});
     target.style.position='static';target.style.top='auto';target.style.left='auto';target.removeAttribute('id');target.removeAttribute('name');
    });
    if(head.tagName==='THEAD'){
     copy=body.cloneNode(false);copy.removeAttribute('id');
     const cols=document.createElement('colgroup');
     [...head.rows[0].cells].forEach(cell=>{const col=document.createElement('col');col.style.width=cell.getBoundingClientRect().width+'px';cols.append(col);});
     copy.append(cols,cloned);Object.assign(copy.style,{tableLayout:'fixed',borderCollapse:getComputedStyle(body).borderCollapse,borderSpacing:getComputedStyle(body).borderSpacing});
    }else{copy=body.cloneNode(false);copy.removeAttribute('id');copy.append(cloned);}
    Object.assign(copy.style,{width:rect.width+'px',minWidth:rect.width+'px',maxWidth:rect.width+'px',boxSizing:'border-box',margin:'0'});
    floating.replaceChildren(copy);
   }
   Object.assign(floating.style,{left:frame.left+'px',top:Math.min(0,rect.bottom-header.height)+'px',width:viewport.clientWidth+'px',height:header.height+'px'});
   copy.style.transform='translateX('+(-viewport.scrollLeft)+'px)';
   pinned.forEach(({source,target})=>{target.style.transform='none';const dx=source.getBoundingClientRect().left-target.getBoundingClientRect().left;target.style.transform='translateX('+dx+'px)';target.style.zIndex='5';target.style.position='relative';});
  }
  function schedule(){if(!pending){pending=true;requestAnimationFrame(update);}}
  window.addEventListener('scroll',schedule,{passive:true,capture:true});window.addEventListener('resize',schedule);document.addEventListener('toggle',schedule,true);document.addEventListener('change',schedule);
  new ResizeObserver(schedule).observe(body);new ResizeObserver(schedule).observe(viewport);
  new MutationObserver(schedule).observe(body,{attributes:true,attributeFilter:['class','style'],childList:true,subtree:true});
  floating.addEventListener('wheel',event=>{const dx=event.deltaX||(event.shiftKey?event.deltaY:0);if(dx){event.preventDefault();viewport.scrollLeft+=dx;schedule();}},{passive:false});
  document.fonts?.ready.then(schedule);schedule();
 });
})();
