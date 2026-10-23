(() => {
 const form = document.getElementById('bulk-variation-form');
 if (!form) return;
 const boxes = [...document.querySelectorAll('.bulk-good')];
 const count = () => { form.querySelector('[data-bulk-count]').textContent = `Выбрано: ${boxes.filter(box => box.checked).length}`; };
 boxes.forEach(box => box.addEventListener('change', count));
 form.querySelector('[data-bulk-visible]').addEventListener('click', () => { boxes.forEach(box => { if (!box.closest('tr').hidden) box.checked = true; }); count(); });
 form.querySelector('[data-bulk-clear]').addEventListener('click', () => { boxes.forEach(box => box.checked = false); count(); });
 const dialog=document.getElementById('bulk-formula-review');
 let confirmed=false, busy=false;
 form.addEventListener('submit', async event => {
  if (document.querySelector('.editor-changed')) { event.preventDefault(); alert('Сначала сохраните или отмените правки коэффициентов. Затем примените формулу к выбранным товарам.');return; }
  if (confirmed) return;
  event.preventDefault(); if (busy) return; busy=true;
  const submit=form.querySelector('button[type=submit]');submit.disabled=true;
  const error=form.querySelector('[data-bulk-error]');error.hidden=true;
  const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),20000);
  try {
   const body=new FormData(form);body.set('preview','1');
   const response=await fetch(form.action,{method:'POST',body,credentials:'same-origin',signal:controller.signal});
   const info=await response.json();if (!response.ok) throw new Error(info.error || 'Не удалось проверить операцию.');
   dialog.querySelector('[data-review-summary]').textContent=`${info.version} · ${info.formula}. Товаров: ${info.goods_count}, записей: ${info.rows}, изменится: ${info.changes}. Ставки K0–K4 сохранятся.`;
   dialog.querySelector('[data-review-goods]').textContent=info.goods.join(', ')+(info.goods_count>info.goods.length?'…':'');
   form.querySelector('[name=review_token]').value=info.token;dialog.showModal();
  } catch (reason) {error.textContent=reason.name==='AbortError'?'Проверка заняла слишком много времени. Повторите попытку.':reason.message;error.hidden=false;}
  finally {busy=false;submit.disabled=false;clearTimeout(timeout);}
 });
 dialog.querySelector('[data-review-cancel]').addEventListener('click',()=>dialog.close());
 dialog.querySelector('[data-review-confirm]').addEventListener('click',()=>{confirmed=true;dialog.close();form.requestSubmit();});
})();
