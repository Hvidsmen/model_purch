(() => {
  const root=document.querySelector('[data-stock-workspace]');if(!root)return;
  const form=root.querySelector('[data-stock-edit]'),table=root.querySelector('.split-stock-workspace-table'),step=root.dataset.step;
  const inputs=[...root.querySelectorAll('[data-stock-field]')],rows=[...root.querySelectorAll('[data-stock-group]')];
  const initial=JSON.parse(document.getElementById('split-stock-workspace-data').textContent),baseline=new Map(inputs.map(input=>[input,input.defaultValue]));
  const pageIds=rows.map(row=>row.dataset.stockGroup),manual={},automatic=new Map();let staged=[],stagedForecasts=[],forecastOnlyEmpty=true,report=initial,busy=false,submitted=false,generation=0,timer;
  const find=name=>root.querySelector('[data-stock-'+name+']'),message=find('message'),save=find('save'),reset=find('reset');
  const numeric=value=>value===null||value===undefined?'—':Number(value).toLocaleString('ru-RU',{maximumFractionDigits:6});
  const rowFor=gid=>rows.find(row=>row.dataset.stockGroup===gid);
  const childFor=gid=>[...root.querySelectorAll('[data-stock-child]')].find(row=>row.dataset.stockChild===gid);
  const inputFor=(gid,field)=>inputs.find(input=>input.dataset.gid===gid&&input.dataset.stockField===field);
  const dirty=()=>Object.keys(manual).some(gid=>Object.keys(manual[gid]).length)||staged.length>0||stagedForecasts.length>0;
  const readOnly=root.dataset.approved==='1';
  const positionKey='split-stock-position:'+location.pathname+':'+step+':'+(document.querySelector('[data-brand-pages]')?.dataset.brandPages||'_all_');
  function remember(){try{sessionStorage.setItem(positionKey,JSON.stringify({groups:rows.filter(row=>row.querySelector('[data-stock-toggle]').getAttribute('aria-expanded')==='true').map(row=>row.dataset.stockGroup),scroll:window.scrollY}));}catch{}}
  function note(text,error=false){message.textContent=text;message.setAttribute('role',error?'alert':'status');}
  async function request(action,data={}){
    const response=await fetch(location.href,{method:'POST',credentials:'same-origin',body:new URLSearchParams({action,revision:root.dataset.revision,csrfmiddlewaretoken:form.querySelector('[name=csrfmiddlewaretoken]').value,...data})});
    let result;try{result=await response.json();}catch{throw Error('Не получен ответ сервера (HTTP '+response.status+'). Ввод остаётся на странице; проверьте вход в портал.');}
    if(!response.ok||!result.ok)throw Error(result.error||'Не удалось выполнить действие.');return result;
  }
  function payload(){return {changes:JSON.stringify(manual),proposal_tokens:JSON.stringify(staged.map(row=>row.token)),forecast_tokens:JSON.stringify(stagedForecasts.map(row=>row.token)),forecast_only_empty:forecastOnlyEmpty?'1':'0',page_groups:JSON.stringify(pageIds)};}
  function spans(){const count=[...table.tHead.rows[0].cells].filter(cell=>getComputedStyle(cell).display!=='none').length;root.querySelectorAll('[data-stock-detail-span],[data-stock-nested-span]').forEach(cell=>cell.colSpan=count);root.querySelectorAll('[data-stock-summary-label]').forEach(cell=>cell.colSpan=root.hasAttribute('data-show-characteristics')?5:1);}
  function updateSave(){
    const changed=new Set([...Object.keys(manual).filter(gid=>Object.keys(manual[gid]).length),...staged.map(row=>row.gid),...stagedForecasts.map(row=>row.gid)]);
    find('save-status').textContent=busy?'Сохранение…':changed.size?'Изменено '+changed.size+' групп · предварительный расчёт':'Изменения сохранены';
    save.disabled=reset.disabled=readOnly||busy||!changed.size;
    rows.forEach(row=>row.querySelector('[data-stock-row-changed]').hidden=!changed.has(row.dataset.stockGroup));
    inputs.forEach(input=>input.classList.toggle('split-stock-input-changed',input.value!==baseline.get(input)&&!input.dataset.stockDerived));
    window.SplitUnsaved?.refresh();
  }
  function setValue(cell,value){cell.textContent=numeric(value);cell.toggleAttribute('data-negative',value!==null&&Number(value)<0);}
  function paint(data){
    report=data;
    rows.forEach(row=>{
      const gid=row.dataset.stockGroup,values=data.rows[gid];if(!values)return;
      row.querySelectorAll('[data-stock-result]').forEach(cell=>setValue(cell,values[cell.dataset.stockResult]));
      const state=values.statuses[step],button=row.querySelector('[data-stock-row-status]');button.textContent=state.message;button.dataset.state=state.code;row.dataset.stockState=state.code;
      const source=row.querySelector('[data-stock-source]');if(source){const origin=values.target_source;source.textContent=values.target_percent===null?'Не задан':origin?.kind==='purchase'?'Модель закупок · '+origin.label:origin?.kind==='manual'?'Вручную':'Не задан';}
      const child=childFor(gid);child?.querySelectorAll('[data-stock-power]').forEach(power=>{
        const p=values.powers[power.dataset.stockPower];if(!p)return;
        power.querySelectorAll('[data-stock-power-result]').forEach(cell=>setValue(cell,p[cell.dataset.stockPowerResult]));
        const field=power.dataset.stockPower+':forecast_before',input=inputFor(gid,field);
        if(input&&manual[gid]?.[field]===undefined){input.value=p.forecast??'';input.dataset.stockDerived='1';}
      });
      const forecast=inputFor(gid,'forecast_group');if(forecast&&manual[gid]?.forecast_group===undefined){forecast.value=values.forecast??'';forecast.dataset.stockDerived='1';}
      if(forecast?.nextElementSibling){const source=values.forecast_source;forecast.nextElementSibling.textContent=source?.label||(source?.from?'По истории '+source.from+' — '+source.through:values.forecast!==null?'Сохранённый прогноз':'Не задан');}
    });
    root.querySelectorAll('[data-stock-summary]').forEach(row=>{
      const totals=row.dataset.stockSummary==='all'?data.totals:(data.page_totals||data.totals),counts=row.dataset.stockSummary==='all'?data.counts:(data.page_counts||data.counts);
      row.querySelectorAll('[data-stock-total]').forEach(cell=>setValue(cell,totals[cell.dataset.stockTotal]));
      const unknown=counts[step].missing;row.querySelector('[data-stock-summary-note]').textContent=unknown?'Не рассчитан: данных не хватает у '+unknown+' групп':dirty()?'Предварительный итог · не сохранено':'По сохранённым данным';
    });
    const counts=(data.page_counts||data.counts)[step];root.querySelectorAll('[data-stock-count]').forEach(node=>node.textContent=counts[node.dataset.stockCount]);
    filter();updateSave();
  }
  async function preview(){
    const own=++generation;if(!form.checkValidity())return;
    try{const result=await request('preview_stock_workspace',payload());if(own===generation&&!submitted){paint(result);note(dirty()?'Предварительный расчёт. Сохраните изменения.':'');return result;}}
    catch(error){if(own===generation)note(error.message,true);}
  }
  function edit(input){
    const gid=input.dataset.gid,field=input.dataset.stockField;manual[gid]??={};delete input.dataset.stockDerived;
    if(field==='forecast_group'){
      for(const key of Object.keys(manual[gid]))if(key.endsWith(':forecast_before')||key.startsWith('month:'))delete manual[gid][key];
    }else if(field.endsWith(':forecast_before')){
      delete manual[gid].forecast_group;
      for(const key of Object.keys(manual[gid]))if(key.startsWith('month:'))delete manual[gid][key];
      inputs.filter(other=>other.dataset.gid===gid&&other.dataset.stockField.endsWith(':forecast_before')).forEach(other=>{manual[gid][other.dataset.stockField]=other.value;delete other.dataset.stockDerived;});
    }
    if(field.startsWith('month:')){
      delete manual[gid].forecast_group;
      for(const key of Object.keys(manual[gid]))if(key.endsWith(':forecast_before'))delete manual[gid][key];
    }
    const imported=automatic.get(gid+':'+field);
    if(imported!==undefined&&input.value===imported)delete manual[gid][field];
    else if(input.value===baseline.get(input)&&!field.endsWith(':forecast_before')&&imported===undefined)delete manual[gid][field];
    else manual[gid][field]=input.value;
    if(!Object.keys(manual[gid]).length)delete manual[gid];
    updateSave();clearTimeout(timer);timer=setTimeout(preview,350);
  }
  inputs.forEach(input=>input.addEventListener('input',()=>edit(input)));
  function discard(){
    clearTimeout(timer);generation++;staged=[];stagedForecasts=[];automatic.clear();Object.keys(manual).forEach(key=>delete manual[key]);baseline.forEach((value,input)=>{input.value=value;delete input.dataset.stockDerived;});paint(initial);note('Изменения отменены.');
  }
  reset.addEventListener('click',discard);
  async function saveAll(next=null){
    if(busy||!dirty()||!form.reportValidity())return;
    clearTimeout(timer);generation++;busy=true;updateSave();const progress=window.SplitProgress?.start('Сохранение остатков, целевого запаса и поставок');
    try{await request('save_stock_workspace',payload());progress?.finish('Изменения сохранены');submitted=true;remember();window.SplitUnsaved?window.SplitUnsaved.navigate(next):(next?location.assign(next):location.reload());}
    catch(error){note(error.message,true);progress?.fail(error.message);throw error;}
    finally{busy=false;updateSave();}
  }
  form.addEventListener('submit',event=>{event.preventDefault();saveAll().catch(()=>{});});
  window.SplitUnsaved?.register({dirty:()=>dirty()&&!submitted,busy:()=>busy,canSave:()=>form.checkValidity(),save:saveAll,discard});
  function reveal(gid,target=''){
    const row=rowFor(gid),child=childFor(gid);if(!row||!child)return;
    const toggle=row.querySelector('[data-stock-toggle]');child.hidden=false;toggle.setAttribute('aria-expanded','true');toggle.firstElementChild.textContent='▾';
    const input=target==='forecast'?inputFor(gid,'forecast_group'):target==='percent'?inputFor(gid,'target_stock_percent'):target==='buy'?inputFor(gid,'buy'):target.includes(':')?inputFor(gid,target):child.querySelector('input:not(:disabled):not([readonly])');
    (input||child).scrollIntoView({block:'center',behavior:'smooth'});input?.focus({preventScroll:true});
  }
  root.addEventListener('click',event=>{
    const toggle=event.target.closest('[data-stock-toggle]');if(toggle){const child=childFor(toggle.dataset.stockToggle);child.hidden=!child.hidden;toggle.setAttribute('aria-expanded',String(!child.hidden));toggle.firstElementChild.textContent=child.hidden?'▸':'▾';}
    const status=event.target.closest('[data-stock-row-status]');if(status){const gid=status.closest('[data-stock-group]').dataset.stockGroup,target=report.rows[gid].statuses[step].target;
      if(target==='distribution'||target==='plan'){const url=new URL(location.href);url.searchParams.set('tab',target==='plan'?'plan':'distribution');url.searchParams.set('group',gid);url.searchParams.set('section','powers');url.searchParams.set('focus','1');const link=document.createElement('a');link.href=url.pathname+url.search;root.append(link);link.click();link.remove();}
      else if(step!=='opening'&&(target==='forecast'||target.includes(':stock_')||target.includes(':prior_')||target.endsWith(':forecast_before'))){const url=new URL(location.href);url.searchParams.set('stock_step','opening');url.searchParams.set('group',gid);url.searchParams.set('focus','1');const link=document.createElement('a');link.href=url.pathname+url.search;root.append(link);link.click();link.remove();}
      else reveal(gid,target);
    }
  });
  const nestedHeaders=[];
  function filter(){
    const query=find('search').value.trim().toLocaleLowerCase('ru'),state=find('state').value;
    const filters=[...root.querySelectorAll('[data-stock-filter]')];let count=0;
    rows.forEach(row=>{
      const matches=(!query||row.dataset.name.toLocaleLowerCase('ru').includes(query))&&(!state||row.dataset.stockState===state)&&filters.every(control=>!control.value||(control.dataset.stockFilter==='factory'?row.dataset.factory:control.dataset.stockFilter==='outdoor_type'?row.dataset.outdoorType:row.dataset.salesPlanGroup)===control.value);
      row.hidden=!matches;row.toggleAttribute('data-reference-excluded',!matches);childFor(row.dataset.stockGroup).hidden=!matches||row.querySelector('[data-stock-toggle]').getAttribute('aria-expanded')!=='true';if(matches)count++;
    });
    nestedHeaders.forEach(({node,members})=>node.hidden=!root.hasAttribute('data-stock-nested-mode')||!members.some(row=>!row.hidden));
    find('visible').textContent='Показано '+count+' из '+rows.length+' групп';root.querySelectorAll('[data-stock-status]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.stockStatus===state)));
  }
  find('search').addEventListener('input',filter);find('state').addEventListener('change',filter);root.querySelectorAll('[data-stock-filter]').forEach(input=>input.addEventListener('change',filter));
  root.querySelectorAll('[data-stock-status]').forEach(button=>button.addEventListener('click',()=>{find('state').value=button.dataset.stockStatus;filter();}));
  find('filter-reset').addEventListener('click',()=>{find('search').value='';find('state').value='';root.querySelectorAll('[data-stock-filter]').forEach(input=>input.value='');filter();});
  find('characteristics').addEventListener('change',event=>{root.toggleAttribute('data-show-characteristics',event.target.checked);spans();});
  find('extra-choice')?.addEventListener('change',event=>{root.toggleAttribute('data-show-extra',event.target.checked);spans();});
  find('nested').addEventListener('change',event=>{
    root.toggleAttribute('data-stock-nested-mode',event.target.checked);
    if(event.target.checked&&!nestedHeaders.length){
      const buckets=new Map();rows.forEach(row=>{const factory=row.dataset.factory||'Завод не указан';if(!buckets.has(factory))buckets.set(factory,[]);buckets.get(factory).push(row);});
      for(const [name,members] of [...buckets].sort((a,b)=>a[0].localeCompare(b[0],'ru'))){const node=document.createElement('tr'),cell=document.createElement('th');node.className='split-stock-nested-heading';cell.dataset.stockNestedSpan='';cell.textContent=name+' · групп: '+members.length;node.append(cell);table.tBodies[0].append(node);members.forEach(row=>table.tBodies[0].append(row,childFor(row.dataset.stockGroup)));nestedHeaders.push({node,members});}
    }
    if(!event.target.checked){rows.forEach(row=>table.tBodies[0].append(row,childFor(row.dataset.stockGroup)));}spans();filter();
  });
  find('set-percent')?.addEventListener('click',()=>{
    const control=find('bulk-percent');if(!control.value||!control.reportValidity()){note('Введите процент от 0 до 1000.',true);return;}
    rows.filter(row=>!row.hidden).forEach(row=>{const input=inputFor(row.dataset.stockGroup,'target_stock_percent');if(!find('bulk-empty').checked||!input.value){input.value=control.value;edit(input);}});
  });
  find('load')?.addEventListener('click',async()=>{
    if(dirty()){note('Сначала сохраните или отмените изменения таблицы.',true);return;}
    const ids=find('load-scope').value==='all'?Object.keys(report.rows):pageIds;
    const area=find('load-scope').value==='all'?'всего сценария':'текущего бренда';
    if(!window.confirm('Обновить остатки и поступления '+area+'? Прогнозы и новые поставки сохранятся.'))return;
    busy=true;window.SplitUnsaved?.refresh();const progress=window.SplitProgress?.start('Обновление данных · '+area);
    try{await request('load_stock_workspace_inventory',{groups:JSON.stringify(ids)});submitted=true;remember();progress?.finish('Данные обновлены');location.reload();}
    catch(error){note(error.message,true);progress?.fail(error.message);}finally{busy=false;updateSave();}
  });
  const dialog=find('proposal-dialog'),proposalStatus=find('proposal-status'),confirm=find('proposal-confirm');let proposalKind='',proposals=[],proposalBusy=false;
  function clearProposal(){proposals=[];find('proposal-results').replaceChildren();confirm.disabled=true;proposalStatus.textContent='';}
  root.querySelectorAll('[data-stock-proposal]').forEach(button=>button.addEventListener('click',()=>{
    if(dirty()){note('Сначала сохраните или отмените изменения, затем получите предложение.',true);return;}
    proposalKind=button.dataset.stockProposal;clearProposal();find('purchase-options').hidden=proposalKind!=='purchase';
    document.getElementById('stock-proposal-title').textContent=proposalKind==='purchase'?'Перенос целевого запаса из закупок':'Расчёт новых поставок';
    find('proposal-explanation').textContent=proposalKind==='purchase'?'Процент переносится по точному сопоставлению группы; значение закупок 0,15 означает 15%. Разные проценты одной группы требуют ручного выбора.':'План продаж + целевой запас − начальный остаток − ожидаемые поступления. Расчёт учитывает каждый блок и мощность; поставки распределяются по сохранённым долям, поэтому объём может быть выше простого баланса группы.';
    dialog.showModal();
  }));
  find('purchase').addEventListener('change',clearProposal);find('import-empty').addEventListener('change',clearProposal);
  find('proposal-close').addEventListener('click',()=>{if(!proposalBusy)dialog.close();});dialog.addEventListener('cancel',event=>{if(proposalBusy)event.preventDefault();});
  find('proposal-preview').addEventListener('click',async()=>{
    if(proposalBusy)return;clearProposal();proposalBusy=true;find('proposal-preview').disabled=true;proposalStatus.textContent='Расчёт…';
    try{
      const result=await request('propose_stock_workspace',{kind:proposalKind,groups:JSON.stringify(rows.filter(row=>!row.hidden).map(row=>row.dataset.stockGroup)),purchase:find('purchase').value,only_empty:find('import-empty').checked?'1':'0'});
      proposals=result.rows;const output=document.createElement('table');output.className='admin-table';const header=document.createElement('tr');
      for(const label of ['Применить','Группа','Было','Предложено','Целевой / конечный запас','Примечание']){const cell=document.createElement('th');cell.textContent=label;header.append(cell);}output.append(header);
      proposals.forEach((item,index)=>{const row=document.createElement('tr'),checkCell=document.createElement('td'),check=document.createElement('input');check.type='checkbox';check.dataset.stockProposalIndex=index;check.checked=!!item.token;check.disabled=!item.token;check.setAttribute('aria-label','Применить: '+item.name);checkCell.append(check);row.append(checkCell);
        for(const value of [item.name,numeric(item.old),item.token?numeric(item.value):'—',proposalKind==='supplies'&&item.token?numeric(item.target)+' / '+numeric(item.closing):'—',item.error||item.warning||'']){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}output.append(row);});
      find('proposal-results').append(output);confirm.disabled=!proposals.some(row=>row.token);proposalStatus.textContent='Проверьте изменения. Без подтверждения таблица не меняется.';
    }catch(error){proposalStatus.textContent=error.message;}finally{proposalBusy=false;find('proposal-preview').disabled=false;}
  });
  confirm.addEventListener('click',()=>{
    const selected=[...dialog.querySelectorAll('[data-stock-proposal-index]:checked')].map(input=>proposals[Number(input.dataset.stockProposalIndex)]).filter(row=>row.token);
    for(const row of selected){staged=staged.filter(old=>!(old.gid===row.gid&&old.field===row.field));staged.push(row);const input=inputFor(row.gid,row.field);if(input){input.value=row.value??'';automatic.set(row.gid+':'+row.field,input.value);}}
    dialog.close();updateSave();preview();note('Предложение внесено в таблицу. Проверьте результат и сохраните изменения.');
  });
  window.SplitStockWorkspace={async stageForecast(values,onlyEmpty){stagedForecasts=values;forecastOnlyEmpty=onlyEmpty;updateSave();const validated=await preview();if(!validated){stagedForecasts=[];updateSave();throw Error(message.textContent||'Предпросмотр устарел.');}note('Прогноз внесён в таблицу. Проверьте результат и сохраните изменения.');}};
  inputs.filter(input=>!input.disabled&&!input.readOnly&&input.value!==baseline.get(input)).forEach(edit);
  paint(initial);spans();root.dataset.stockReady='1';try{const saved=JSON.parse(sessionStorage.getItem(positionKey)||'null');if(saved){for(const gid of saved.groups){const row=rowFor(gid),child=childFor(gid);if(row&&child){child.hidden=false;const button=row.querySelector('[data-stock-toggle]');button.setAttribute('aria-expanded','true');button.firstElementChild.textContent='▾';}}requestAnimationFrame(()=>window.scrollTo(0,saved.scroll));sessionStorage.removeItem(positionKey);}}catch{}const params=new URLSearchParams(location.search);if(params.get('focus')==='1'&&params.get('group'))reveal(params.get('group'));
})();
