(() => {
  const workspace = document.querySelector('.split-workspace');
  if (!workspace) return;
  workspace.querySelector('[data-split-save-next]')?.addEventListener('click',event=>{
    const url=new URL(event.currentTarget.dataset.splitSaveNext,location.href);
    const brand=new URLSearchParams(location.search).get('brand_page');
    if(brand)url.searchParams.set('brand_page',brand);
    window.SplitUnsaved?.saveAndNavigate(url.href);
  });
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
  async function readSaveResponse(response){
    const text=await response.text();let data;
    try{data=JSON.parse(text);}catch{
      if(response.redirected)throw new Error('Сеанс входа мог истечь. Войдите в портал в другой вкладке и повторите сохранение. Введённые значения остаются на странице.');
      throw new Error('Сервер вернул неожиданный ответ (HTTP '+response.status+'). Изменения не подтверждены; введённые значения остаются на странице.');
    }
    if(!response.ok||!data||data.ok!==true)throw new Error(data?.error||'Не удалось сохранить изменения.');
    return data;
  }
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
    const inputs = [...plan.querySelectorAll('input, select, textarea')].filter(input => input.type !== 'hidden' || input.name.startsWith('ddp_proposal_'));
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
      window.SplitUnsaved?.refresh();
    }
    plan.addEventListener('input', update); plan.addEventListener('change', update);
    cancel.addEventListener('click', () => {
      original.forEach((value,input) => { if (input.type === 'checkbox') input.checked=value; else input.value=value; }); update();
    });
    plan.addEventListener('submit', event => {
      const action=event.submitter?.value||'';
      if((action==='lines'||action==='calculate_supplies'||action.startsWith('group_plan:')) && window.SplitUnsaved){event.preventDefault();savePlan(null,action.startsWith('group_plan:')?action.slice('group_plan:'.length):'',action==='calculate_supplies').catch(()=>{});}
      else{submitted=true;window.SplitUnsaved?.allow();}
    });
    let busy=false;
    async function savePlan(next=null,forceGroup='',calculate=false){
        if(busy||!plan.reportValidity())return;
        const fields=new FormData(plan);
        const payload=new URLSearchParams({action:'save_plan_rows',revision:fields.get('revision'),csrfmiddlewaretoken:fields.get('csrfmiddlewaretoken'),plan_payload:JSON.stringify([...fields])});
        const targets=inputs.filter(input=>input.type==='number'&&input.name.startsWith('group_plan_')&&input.value!==original.get(input)).map(input=>input.name.slice('group_plan_'.length));
        if(forceGroup)targets.push(forceGroup);
        payload.set('pending_group_plans',JSON.stringify([...new Set(targets)]));
        if(calculate)payload.set('calculate_supplies','1');
        const progress=window.SplitProgress?.start('Сохранение изменений плана');busy=true;window.SplitUnsaved.refresh();
        try{
          const response=await fetch(location.href,{method:'POST',credentials:'same-origin',body:payload});await readSaveResponse(response);
          progress?.finish('Изменения сохранены');submitted=true;remember();window.SplitUnsaved.navigate(next);
        }catch(error){progress?.fail(error.message);throw error;}finally{busy=false;window.SplitUnsaved.refresh();}
    }
    let previewTimer, previewSequence=0;
    const balanceStatus = plan.querySelector('[data-plan-balance-status]');
    const format = value => value === null ? '—' : new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(Number(value));
    async function previewBalance() {
      const sequence=++previewSequence;
      if(busy || !plan.checkValidity())return;
      const fields=new FormData(plan);
      const targets=inputs.filter(input=>input.type==='number' && input.name.startsWith('group_plan_') && input.value!==original.get(input)).map(input=>input.name.slice(11));
      const payload=new URLSearchParams({action:'preview_sales_balance',revision:fields.get('revision'),csrfmiddlewaretoken:fields.get('csrfmiddlewaretoken'),plan_payload:JSON.stringify([...fields]),pending_group_plans:JSON.stringify(targets)});
      try {
        const response=await fetch(location.href,{method:'POST',credentials:'same-origin',body:payload});
        const data=await readSaveResponse(response);
        if(sequence!==previewSequence)return;
        Object.entries(data.rows).forEach(([gid,row])=>{
          const tr=plan.querySelector('[data-plan-group="'+CSS.escape(gid)+'"]');if(!tr)return;
          const cell=tr.querySelector('[data-plan-inventory="closing"]');
          if(cell){cell.firstChild.textContent=format(row.closing);const changedSales=[...tr.querySelectorAll('input[name^="q_"],input[name^="group_plan_"]')].some(input=>input.value!==original.get(input));
            const needsReview=changedSales && tr.querySelector('[data-buy-source="calculated"]');
            cell.querySelector('[data-plan-balance-note]').textContent='Цель: '+format(row.target)+' · '+row.status+(needsReview?' · Продажи изменены: проверьте расчёт закупок.':'');}
        });
        if(balanceStatus)balanceStatus.textContent=dirty ? 'Предварительный баланс по текущему вводу. Изменения не сохранены; итоги и месяцы обновятся после сохранения.' : '';
      }catch(error){if(sequence===previewSequence && balanceStatus)balanceStatus.textContent=error.message;}
    }
    function scheduleBalance(){++previewSequence;clearTimeout(previewTimer);previewTimer=setTimeout(previewBalance,450);}
    plan.addEventListener('input',scheduleBalance);plan.addEventListener('change',scheduleBalance);
    cancel.addEventListener('click',scheduleBalance);
    previewBalance();
    window.SplitUnsaved?.register({dirty:()=>dirty&&!submitted,busy:()=>busy,canSave:()=>plan.checkValidity(),save:savePlan,discard(){original.forEach((value,input)=>{if(input.type==='checkbox')input.checked=value;else input.value=value;});update();}});
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
      window.SplitUnsaved?.refresh();
    }
    workspace.addEventListener('input', update); workspace.addEventListener('change',update);
    cancel.addEventListener('click', () => { initial.forEach((value,input) => {input.value=value;}); update(); });
    async function saveStock(next=null) {
      if (busy || !changed.length || changed.some(form => !form.reportValidity())) return;
      const payload = changed.map(form => ({gid:form.elements.namedItem('group').value, entries:[...new FormData(form)]}));
      const first=stockForms[0];
      const progress=window.SplitProgress?.start('Сохранение стока и поставок · групп: '+changed.length);
      busy=true; update(); status.textContent='Сохранение…';
      try {
        const response=await fetch(location.href,{method:'POST',credentials:'same-origin',body:new URLSearchParams({action:'save_stock_rows',revision:first.elements.namedItem('revision').value,csrfmiddlewaretoken:first.elements.namedItem('csrfmiddlewaretoken').value,rows:JSON.stringify(payload)})});
        await readSaveResponse(response);
        progress?.finish('Изменения сохранены');
        submitted=true; remember(); window.SplitUnsaved ? window.SplitUnsaved.navigate(next) : location.reload();
      } catch(error) {busy=false;update();status.textContent='Не сохранено: '+error.message;progress?.fail(error.message);throw error;}
    }
    save.addEventListener('click',()=>saveStock().catch(()=>{}));
    workspace.addEventListener('submit',()=>{submitted=true;window.SplitUnsaved?.allow();});
    window.SplitUnsaved?.register({dirty:()=>!!changed.length&&!submitted,busy:()=>busy,canSave:()=>changed.every(form=>form.checkValidity()),save:saveStock,discard(){initial.forEach((value,input)=>{input.value=value;});update();}});
    update();
  }
  workspace.querySelectorAll('[data-plan-inventory="closing"], .split-stock-group-row td:last-child, .split-stock-power-row td:last-child').forEach(cell => {
    const value=Number(cell.textContent.trim().replace(/\s/g,'').replace(',','.'));
    if(Number.isFinite(value) && value<0){cell.classList.add('split-deficit');cell.title='Дефицит: проверьте мощность и план поставок';}
    if(cell.textContent.trim()==='—')cell.title='Недостаточно данных. Проверьте прогноз, остатки, поступления и распределение.';
  });
  workspace.dataset.workspaceUxReady='true';
  function revealStock(gid,power) {
    const group=workspace.querySelector('[data-stock-group="'+CSS.escape(gid)+'"]');
    if(!group)return false;
    if(group.hasAttribute('data-reference-excluded'))workspace.querySelector('[data-reference-reset]')?.click();
    document.dispatchEvent(new CustomEvent('split-reveal-group',{detail:{gid}}));
    const toggle=group.querySelector('[data-stock-toggle]');
    if(toggle?.getAttribute('aria-expanded')==='false')toggle.click();
    const row=power ? workspace.querySelector('[data-stock-child="'+CSS.escape(gid)+'"][data-stock-power="'+CSS.escape(power)+'"]') : group;
    if(!row)return false;
    workspace.querySelectorAll('.split-focus-row').forEach(node=>node.classList.remove('split-focus-row'));
    row.classList.add('split-focus-row');
    const target=row.matches('.split-stock-power-row') ? row.lastElementChild : row;
    target.setAttribute('tabindex','-1');target.focus({preventScroll:true});target.scrollIntoView({block:'center',inline:'center'});
    return true;
  }
  workspace.addEventListener('click',event=>{
    const link=event.target.closest('[data-stock-issue-group]');
    if(!link||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
    if(revealStock(link.dataset.stockIssueGroup,link.dataset.stockIssuePower))event.preventDefault();
  });
  const focus = new URLSearchParams(location.search);
  if (focus.get('focus') === '1' && focus.get('group')) {
    const gid=focus.get('group');
    if(!revealStock(gid,focus.get('power'))) {
      const row=workspace.querySelector('[data-plan-group="'+CSS.escape(gid)+'"], [data-grid-row][data-gid="'+CSS.escape(gid)+'"]');
      if(row){
        workspace.querySelector('[data-table-view="flat"]')?.click();workspace.querySelector('[data-reference-reset]')?.click();
        row.classList.add('split-focus-row');
        const config=focus.get('power') ? row.querySelector('[data-power-config="'+CSS.escape(focus.get('power'))+'"]') : null;
        if(config){config.open=true;config.scrollIntoView({block:'center',inline:'center'});config.querySelector('input,select')?.focus({preventScroll:true});}
        else row.scrollIntoView({block:'center'});
      }
    }
  }
})();
