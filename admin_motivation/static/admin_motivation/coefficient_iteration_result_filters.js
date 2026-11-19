(() => {
  document.querySelectorAll('[data-result-filter]').forEach(panel=>{
    const search=panel.querySelector('[data-result-search]'),labels=[...panel.querySelectorAll('.iteration-result-options label')];
    const count=()=>{const selected=panel.querySelectorAll('input[type=checkbox]:checked').length;panel.querySelector('summary').textContent=panel.dataset.resultFilter+' · '+(selected?'выбрано '+selected:'Все');};
    panel.addEventListener('change',count);
    search.addEventListener('input',()=>{const query=search.value.trim().toLocaleLowerCase('ru');labels.forEach(label=>label.hidden=!label.textContent.toLocaleLowerCase('ru').includes(query));});
    panel.querySelector('[data-result-select-all]').addEventListener('click',()=>{labels.filter(label=>!label.hidden).forEach(label=>label.querySelector('input').checked=true);count();});
    panel.querySelector('[data-result-clear]').addEventListener('click',()=>{labels.forEach(label=>label.querySelector('input').checked=false);count();});
  });
})();
