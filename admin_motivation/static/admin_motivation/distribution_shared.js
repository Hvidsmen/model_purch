/* Integer-cent budget shared across tabs, independent for each payment. */
(() => {
 const cards=[...document.querySelectorAll('[data-pool-payment]')];if(!cards.length)return;
 const cents=value=>Math.round(Number(String(value).replace(/\s/g,'').replace(',','.'))*100);
 const money=value=>(value/100).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2});
 for(const card of cards){
  const budget=cents(card.dataset.fundAmount),fixed=cents(card.dataset.fixedAllocated);
  const inputs=[...document.querySelectorAll('[data-shared-input]')].filter(i=>i.dataset.rebalancePayment===card.dataset.poolPayment);
  if(!inputs.length||!Number.isSafeInteger(budget)||!Number.isSafeInteger(fixed)||inputs.some(i=>!Number.isSafeInteger(cents(i.value))))continue;
  const members=inputs.map(input=>{
   const value=cents(input.value),minimum=cents(input.dataset.minimum),original=cents(input.dataset.original);
   const box=document.createElement('div');box.className='pd-slider';const range=document.createElement('input');
   range.type='range';range.min=String(minimum);range.max=String(Math.max(minimum,budget-fixed));range.step='1';range.value=String(Math.max(minimum,value));range.setAttribute('aria-label',input.getAttribute('aria-label'));
   const hint=document.createElement('small');input.before(box);box.append(input,range,hint);input.type='text';input.dataset.amountEditor='true';
   return {input,range,hint,value,minimum,original};
  });
  const remaining=()=>budget-fixed-members.reduce((sum,m)=>sum+m.value,0);
  const refresh=()=>{const rest=remaining();card.querySelector('[data-allocated]').textContent=money(budget-rest);card.querySelector('[data-remaining]').textContent=money(rest);card.dataset.unallocated=String(rest!==0);
   for(const m of members){m.range.value=String(Math.max(m.minimum,m.value));m.range.setAttribute('aria-valuetext',money(m.value));m.input.setCustomValidity(m.value<m.minimum?'Ниже допустимого минимума.':rest<0?'Превышен общий остаток выплаты.':'');const reduction=m.original>0?(1-m.value/m.original)*100:0;const label=m.input.closest('tr')?.querySelector('[data-reduction-label]');if(label)label.textContent=m.original>0?'Уменьшение: '+reduction.toLocaleString('ru-RU',{maximumFractionDigits:2})+'%':'';m.hint.textContent=`Минимум ${money(m.minimum)} · уменьшение ${reduction.toLocaleString('ru-RU',{maximumFractionDigits:2})}% · доступно добавить ${money(Math.max(0,rest))}`;}
  };
  for(const m of members){m.range.addEventListener('input',()=>{const ceiling=m.value+Math.max(0,remaining());m.value=Math.max(m.minimum,Math.min(Number(m.range.value),ceiling));m.input.value=(m.value/100).toFixed(2);refresh();});
   m.input.addEventListener('input',()=>{const value=cents(m.input.value);if(!/^\d+(?:[.,]\d{1,2})?$/.test(m.input.value.replace(/\s/g,''))||!Number.isSafeInteger(value)){m.input.setCustomValidity('Введите сумму с точностью до копеек.');return;}m.value=value;refresh();});}
  refresh();
 }
})();
