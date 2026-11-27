(() => {
 const root=document.querySelector('[data-deal-partners]');if(!root)return;
 async function load(group,url){
  const body=group.querySelector('[data-partner-content]');if(group.dataset.busy==='true')return;
  group.dataset.busy='true';body.setAttribute('aria-busy','true');
  try{
   const response=await fetch(url,{credentials:'same-origin',headers:{Accept:'application/json'}});
   if(!response.headers.get('Content-Type')?.includes('application/json'))throw Error('Не удалось загрузить сделки. Проверьте вход в портал.');
   const result=await response.json();if(!response.ok||typeof result.html!=='string')throw Error(result.errors?.join(' ')||'Не удалось загрузить сделки.');
   body.innerHTML=result.html;group.dataset.loaded='true';
   const show=document.getElementById('deal-detail-toggle')?.checked||false;
   body.querySelectorAll('[data-deal-detail]').forEach(cell=>cell.hidden=!show);
  }catch(error){
   body.replaceChildren();const message=document.createElement('p');message.setAttribute('role','alert');message.textContent=error.message;
   const retry=document.createElement('button');retry.type='button';retry.textContent='Повторить';retry.addEventListener('click',()=>load(group,url));body.append(message,retry);
  }finally{group.dataset.busy='false';body.removeAttribute('aria-busy');}
 }
 root.addEventListener('toggle',event=>{const group=event.target;if(group.matches('[data-partner-url]')&&group.open&&group.dataset.loaded!=='true')load(group,group.dataset.partnerUrl);},true);
 root.addEventListener('click',event=>{const link=event.target.closest('[data-partner-page]');if(!link)return;event.preventDefault();load(link.closest('[data-partner-url]'),link.href);});
})();
