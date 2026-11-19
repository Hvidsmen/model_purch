(() => {
  const panel = document.querySelector('[data-reference-filters]');
  if (!panel) return;
  const references = JSON.parse(document.getElementById('split-reference-values').textContent);
  const controls = [...panel.querySelectorAll('[data-reference-filter]')];
  const storage = 'split-reference-filters-' + panel.dataset.referenceFilters;
  let saved = {};
  try { saved = JSON.parse(sessionStorage.getItem(storage) || '{}'); } catch (_) {}
  const tokens = value => String(value || '—').split(',').map(part => part.trim()).filter(Boolean);
  const selectedValues = control => [...control.selectedOptions].map(option => option.value);
  const widgets = new Map();
  controls.forEach(control => {
    const key = control.dataset.referenceFilter;
    const values = [...new Set(Object.values(references).flatMap(ref => tokens(ref[key])))].sort((a,b) => a.localeCompare(b, 'ru'));
    values.forEach(value => control.add(new Option(value, value)));
    const previous = Array.isArray(saved[key]) ? saved[key] : saved[key] ? tokens(saved[key]) : [];
    [...control.options].forEach(option => { option.selected = previous.includes(option.value); });
    const label = control.getAttribute('aria-label');
    const details = document.createElement('details'); details.className = 'split-reference-multi';
    const summary = document.createElement('summary'); summary.setAttribute('aria-label',label + ': выбрать значения');
    const popup = document.createElement('div'); popup.className = 'split-reference-popup';
    const search = document.createElement('input'); search.type = 'search'; search.placeholder = 'Поиск…'; search.setAttribute('aria-label','Поиск: ' + label);
    const actions = document.createElement('div'); actions.className = 'split-reference-actions';
    const all = document.createElement('button'); all.type = 'button'; all.textContent = 'Выбрать найденные';
    const clear = document.createElement('button'); clear.type = 'button'; clear.textContent = 'Снять выбор';
    const list = document.createElement('div'); list.className = 'split-reference-options';
    const empty = document.createElement('p'); empty.textContent = 'Ничего не найдено'; empty.hidden = true;
    const choices = [...control.options].map(option => {
      const item = document.createElement('label');
      const checkbox = document.createElement('input'); checkbox.type='checkbox'; checkbox.value=option.value;
      checkbox.checked=option.selected;
      item.append(checkbox,document.createTextNode(option.text)); list.append(item);
      checkbox.addEventListener('change', () => { option.selected=checkbox.checked; control.dispatchEvent(new Event('change',{bubbles:true})); });
      return {option,item,checkbox};
    });
    function filter() {
      const query=search.value.trim().toLocaleLowerCase('ru');
      choices.forEach(choice => {choice.item.hidden=!choice.option.text.toLocaleLowerCase('ru').includes(query);});
      empty.hidden=choices.some(choice => !choice.item.hidden);
    }
    function sync() {
      const chosen=selectedValues(control);
      summary.textContent=chosen.length ? (chosen.length <= 2 ? chosen.join(', ') : 'Выбрано: '+chosen.length) : 'Все';
      summary.title=chosen.join(', ') || 'Все значения';
      choices.forEach(choice => {choice.checkbox.checked=choice.option.selected;});
    }
    search.addEventListener('input', filter);
    all.addEventListener('click', () => {choices.filter(choice => !choice.item.hidden).forEach(choice => {choice.option.selected=true;});control.dispatchEvent(new Event('change',{bubbles:true}));});
    clear.addEventListener('click', () => {[...control.options].forEach(option => {option.selected=false;});control.dispatchEvent(new Event('change',{bubbles:true}));});
    details.addEventListener('keydown', event => {if(event.key==='Escape'){details.open=false;summary.focus();}});
    details.addEventListener('toggle', () => {if(details.open){widgets.forEach(widget => {if(widget.details!==details)widget.details.open=false;});search.focus();}});
    actions.append(all,clear); popup.append(search,actions,list,empty); details.append(summary,popup);
    control.after(details); control.hidden=true;
    widgets.set(control,{details,sync,search,filter}); sync();
  });
  document.addEventListener('click',event => {widgets.forEach(widget => {if(!widget.details.contains(event.target))widget.details.open=false;});});
  function apply() {
    const selected = Object.fromEntries(controls.map(control => [control.dataset.referenceFilter, selectedValues(control)]));
    const matches = new Set(Object.entries(references).filter(([,ref]) => controls.every(control => !selectedValues(control).length || tokens(ref[control.dataset.referenceFilter]).some(value => selectedValues(control).includes(value)))).map(([gid]) => gid));
    const nodes = new Map([...document.querySelectorAll('[data-plan-node]')].map(row => [row.dataset.planNode, row]));
    const visibleNodes = new Set();
    document.querySelectorAll('[data-plan-group]').forEach(row => {
      if (!matches.has(row.dataset.planGroup)) return;
      let parent = row.dataset.planParent;
      while (nodes.has(parent)) { visibleNodes.add(parent); parent = nodes.get(parent).dataset.planParent; }
    });
    document.querySelectorAll('[data-plan-group], [data-plan-power-group], [data-stock-group], [data-stock-child], [data-grid-row], [data-plan-node]').forEach(row => {
      const gid = row.dataset.planGroup || row.dataset.planPowerGroup || row.dataset.stockGroup || row.dataset.stockChild || row.dataset.gid;
      row.toggleAttribute('data-reference-excluded', row.hasAttribute('data-plan-node') ? !visibleNodes.has(row.dataset.planNode) : !matches.has(gid));
    });
    const shown = new Set([...document.querySelectorAll('[data-plan-group], [data-stock-group], [data-grid-row]')].filter(row => !row.hasAttribute('data-reference-excluded')).map(row => row.dataset.planGroup || row.dataset.stockGroup || row.dataset.gid));
    panel.querySelector('[data-reference-filter-count]').textContent = shown.size ? 'Показано групп: ' + shown.size : 'Нет групп по выбранным условиям.';
    try { sessionStorage.setItem(storage, JSON.stringify(selected)); } catch (_) {}
    document.dispatchEvent(new Event('split-reference-filter-change'));
  }
  controls.forEach(control => control.addEventListener('change', () => {widgets.get(control).sync();apply();}));
  panel.querySelector('[data-reference-reset]').addEventListener('click', () => { controls.forEach(control => {[...control.options].forEach(option => {option.selected=false;});const widget=widgets.get(control);widget.search.value='';widget.filter();widget.sync();}); apply(); });
  apply();
})();
