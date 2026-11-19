(() => {
  const workspace = document.querySelector('.split-workspace');
  if (!workspace) return;
  // Preserve expansion and scroll without storing business inputs in the browser.
  const key = 'split-workspace-position-' + location.pathname + '-' + new URLSearchParams(location.search).get('tab');
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
    if (saved) {
      saved.expanded.forEach(selector => {
        const toggle = workspace.querySelector(selector);
        if (toggle && toggle.getAttribute('aria-expanded') === 'false') toggle.click();
      });
      requestAnimationFrame(() => window.scrollTo(0, saved.scroll));
      sessionStorage.removeItem(key);
    }
  } catch (_) {}
  function remember() {
    const expanded = [];
    workspace.querySelectorAll('[data-plan-group-toggle], [data-stock-toggle], [data-stock-band-toggle], [data-stock-channel-toggle]').forEach(toggle => {
      if (toggle.getAttribute('aria-expanded') !== 'true') return;
      const attribute = ['data-plan-group-toggle','data-stock-toggle','data-stock-band-toggle','data-stock-channel-toggle'].find(name => toggle.hasAttribute(name));
      expanded.push('[' + attribute + '="' + CSS.escape(toggle.getAttribute(attribute)) + '"]');
    });
    try { sessionStorage.setItem(key, JSON.stringify({expanded, scroll:window.scrollY})); } catch (_) {}
  }
  workspace.addEventListener('submit', remember);
  const plan = document.getElementById('split-plan-inputs');
  if (plan && !plan.querySelector('fieldset:disabled')) {
    const save = plan.querySelector('button[value="lines"]');
    const bar = document.createElement('div'); bar.className = 'split-plan-save-panel';
    const status = document.createElement('span'); status.setAttribute('role','status');
    const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'admin-button secondary'; cancel.textContent = 'Отменить изменения';
    save.before(bar); bar.append(status, save, cancel);
    const inputs = [...plan.querySelectorAll('input, select, textarea')].filter(input => input.type !== 'hidden');
    const original = new Map(inputs.map(input => [input, input.type === 'checkbox' ? input.checked : input.value]));
    let dirty = false, submitted = false;
    function update() {
      let count = 0;
      original.forEach((value,input) => {
        const changed = (input.type === 'checkbox' ? input.checked : input.value) !== value;
        input.classList.toggle('split-input-changed',changed);
        if (changed) count++;
      });
      dirty = !!count;
      status.textContent = count ? 'Несохранённых полей: ' + count : 'Изменения сохранены';
      cancel.disabled = !dirty;
    }
    plan.addEventListener('input', update); plan.addEventListener('change', update);
    cancel.addEventListener('click', () => {
      original.forEach((value,input) => { if (input.type === 'checkbox') input.checked=value; else input.value=value; }); update();
    });
    plan.addEventListener('submit', () => { submitted = true; });
    window.addEventListener('beforeunload', event => { if (dirty && !submitted) { event.preventDefault(); event.returnValue=''; } });
    update();
  }
  const stockForms = [...workspace.querySelectorAll('form')].filter(form => form.querySelector('[name="action"][value="stock"]'));
  if (stockForms.length && !stockForms[0].elements.namedItem('revision').disabled && workspace.querySelector('.split-stock-group-row input:not(:disabled)')) {
    const bar = document.createElement('div'); bar.className = 'split-plan-save-panel';
    const status = document.createElement('span'); status.setAttribute('role','status');
    const save = document.createElement('button'); save.type='button'; save.className='admin-button'; save.textContent='Сохранить изменения стока';
    const cancel = document.createElement('button'); cancel.type='button'; cancel.className='admin-button secondary'; cancel.textContent='Отменить изменения стока';
    bar.append(status,save,cancel); workspace.querySelector('.split-stock-table-scroll').after(bar);
    const baseline = new Map(stockForms.map(form => [form, JSON.stringify([...new FormData(form)].map(([name,value]) => {const input=form.elements.namedItem(name);return [name,input && input.type !== 'hidden' && input.defaultValue !== undefined ? input.defaultValue : value];}))]));
    const initial = new Map(stockForms.flatMap(form => [...form.elements]).filter(el => ['INPUT','SELECT','TEXTAREA'].includes(el.tagName)).map(el => [el,el.type!=='hidden' && el.defaultValue!==undefined ? el.defaultValue : el.value]));
    let changed=[], busy=false, submitted=false;
    function update() {
      changed = stockForms.filter(form => JSON.stringify([...new FormData(form)]) !== baseline.get(form));
      status.textContent = changed.length ? 'Изменено групп: '+changed.length : 'Изменения сохранены';
      save.disabled = cancel.disabled = busy || !changed.length;
      initial.forEach((value,input) => input.classList.toggle('split-input-changed',input.type!=='hidden' && input.value!==value));
    }
    workspace.addEventListener('input', update); workspace.addEventListener('change',update);
    cancel.addEventListener('click', () => { initial.forEach((value,input) => {input.value=value;}); update(); });
    save.addEventListener('click', async () => {
      if (busy || !changed.length || changed.some(form => !form.reportValidity())) return;
      const payload = changed.map(form => ({gid:form.elements.namedItem('group').value, entries:[...new FormData(form)]}));
      const first=stockForms[0];
      busy=true; update(); status.textContent='Сохранение…';
      try {
        const response=await fetch(location.href,{method:'POST',credentials:'same-origin',body:new URLSearchParams({action:'save_stock_rows',revision:first.elements.namedItem('revision').value,csrfmiddlewaretoken:first.elements.namedItem('csrfmiddlewaretoken').value,rows:JSON.stringify(payload)})});
        const data=await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || 'Не удалось сохранить изменения.');
        submitted=true; remember(); location.reload();
      } catch(error) {busy=false;update();status.textContent='Не сохранено: '+error.message;}
    });
    workspace.addEventListener('submit',()=>{submitted=true;});
    window.addEventListener('beforeunload',event=>{if(changed.length && !submitted){event.preventDefault();event.returnValue='';}});
    update();
  }
  workspace.querySelectorAll('[data-plan-inventory="closing"], .split-stock-group-row td:last-child, .split-stock-power-row td:last-child').forEach(cell => {
    const value=Number(cell.textContent.trim().replace(/\s/g,'').replace(',','.'));
    if(Number.isFinite(value) && value<0){cell.classList.add('split-deficit');cell.title='Дефицит: проверьте мощность и план поставок';}
    if(cell.textContent.trim()==='—')cell.title='Недостаточно данных. Проверьте прогноз, остатки, поступления и распределение.';
  });
  workspace.dataset.workspaceUxReady='true';
  const focus = new URLSearchParams(location.search);
  if (focus.get('focus') === '1' && focus.get('group')) {
    const gid = CSS.escape(focus.get('group'));
    const row = workspace.querySelector('[data-plan-group="'+gid+'"], [data-stock-group="'+gid+'"], [data-grid-row][data-gid="'+gid+'"]');
    if (row) {
      workspace.querySelector('[data-table-view="flat"]')?.click();
      workspace.querySelector('[data-reference-reset]')?.click();
      row.classList.add('split-focus-row'); row.scrollIntoView({block:'center'});
    }
  }
})();
