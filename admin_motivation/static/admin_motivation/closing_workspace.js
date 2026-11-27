(() => {
 const root=document.querySelector('[data-closing-comparisons]');if(!root)return;
 const dialog=document.getElementById('closing-detail-dialog'),frame=dialog.querySelector('iframe');
 const currency=document.getElementById('closing-currency');
 const money=value=>value==null?'—':Number(value).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2});
 const coefficient=value=>value==null?'—':Number(value).toLocaleString('ru-RU',{maximumFractionDigits:4});
 currency.value=new URL(location.href).searchParams.get('closing_currency')==='RUB'?'RUB':'USD';
 const status=document.querySelector('[data-closing-status]');
 let controller;
 async function refresh(){controller?.abort();controller=new AbortController();const url=new URL(root.dataset.closingComparisons,location.href);url.searchParams.set('year',root.dataset.year);url.searchParams.set('period',root.dataset.period);url.searchParams.set('currency',currency.value);status.textContent='Загружаем суммы к выплате…';
  try{const response=await fetch(url,{signal:controller.signal,credentials:'same-origin'});const data=await response.json();if(!response.ok)throw Error(data.error||'Не удалось загрузить суммы.');document.querySelectorAll('[data-impact-branch]').forEach(row=>{const values=data.rows[row.dataset.impactBranch];if(!values)return;row.querySelectorAll('[data-impact-value]').forEach(el=>{const key=el.dataset.impactValue;el.textContent=key==='coefficient'?coefficient(values[key]):key==='percent'?(values[key]==null?'—':money(values[key])+'%'):money(values[key]);});});status.textContent='Последний расчёт · '+data.currency+' · до распределения директором';}
  catch(error){if(error.name!=='AbortError')status.textContent=error.message;}
 }
 document.querySelectorAll('[data-closing-details]').forEach(link=>link.addEventListener('click',event=>{event.preventDefault();const url=new URL(link.href);url.searchParams.set('currency',currency.value);frame.src=url;dialog.dataset.branch=link.dataset.closingDetails;dialog.showModal();}));
 dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close();});
 dialog.addEventListener('close',()=>{frame.src='about:blank';});
 window.addEventListener('message',event=>{if(event.origin!==location.origin||event.source!==frame.contentWindow)return;if(event.data?.closingClose)dialog.close();if(event.data?.closingSaved){const pending={};document.querySelectorAll('.pw-grid input[name^="coefficient_"]').forEach(input=>{if(input.value!==input.dataset.initialValue&&input.name!=='coefficient_'+dialog.dataset.branch)pending[input.name]=input.value;});sessionStorage.setItem('closing-unsaved:'+location.pathname+location.search,JSON.stringify(pending));location.reload();}});
 try{const key='closing-unsaved:'+location.pathname+location.search;const pending=JSON.parse(sessionStorage.getItem(key)||'{}');sessionStorage.removeItem(key);Object.entries(pending).forEach(([name,value])=>{const input=document.getElementsByName(name)[0];if(input&&!input.disabled){input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));}});}catch{}
 currency.addEventListener('change',()=>{const url=new URL(location.href);url.searchParams.set('closing_currency',currency.value);history.replaceState(null,'',url);refresh();});document.querySelector('[data-closing-refresh]')?.addEventListener('click',refresh);refresh();
})();
