(() => {
  const dialog=document.getElementById('split-ddp-dialog'),form=document.getElementById('split-plan-inputs');
  if(!dialog||!form)return;
  const find=s=>dialog.querySelector('[data-ddp-'+s+']'),source=find('source'),purchase=find('purchase-id');
  const groups=find('groups'),status=find('status'),progress=find('progress'),apply=find('apply');
  let busy=false,proposals=[],catalog=null;
  const element=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
  const input=gid=>form.elements.namedItem('ddp_'+gid);
  const shown=gid=>{const row=[...form.querySelectorAll('[data-plan-group]')].find(row=>row.dataset.planGroup===gid);return row&&!row.hasAttribute('data-reference-excluded');};
  const eligible=gid=>shown(gid)&&(!find('empty').checked||input(gid)?.value==='');
  function selected(){return [...groups.querySelectorAll('[data-ddp-target]:checked')].filter(el=>!el.closest('article').hidden&&eligible(el.value)).map(el=>el.value);}
  function invalidate(){proposals=[];apply.disabled=true;find('preview').replaceChildren();count();}
  function count(){find('count').textContent='Выбрано групп: '+selected().length;}
  function filter(){const query=find('search').value.trim().toLocaleLowerCase('ru');groups.querySelectorAll('article').forEach(article=>{article.hidden=!eligible(article.dataset.gid)||!article.dataset.name.includes(query);});count();}
  function setBusy(value){busy=value;progress.hidden=!value;dialog.querySelectorAll('button,input,select').forEach(el=>{el.disabled=value;});apply.disabled=value||!proposals.length;}
  async function request(action,values={}){
    const body=new FormData();body.set('csrfmiddlewaretoken',form.elements.namedItem('csrfmiddlewaretoken').value);body.set('revision',form.elements.namedItem('revision').value);body.set('action',action);
    Object.entries(values).forEach(([key,value])=>body.set(key,value));
    const response=await fetch(location.href,{method:'POST',body,headers:{'X-Requested-With':'XMLHttpRequest'}});
    const result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error||'Не удалось получить DDP. План не изменён.');return result;
  }
  function draw(){
    const initialized=!!groups.querySelector('[data-ddp-target]');
    const previous=new Set([...groups.querySelectorAll('[data-ddp-target]:checked')].map(el=>el.value));groups.replaceChildren();
    const datalist=element('datalist');datalist.id='split-ddp-purchase-goods';
    const labels=new Map((catalog.goods||[]).map(g=>[g.id,g.name+' · #'+g.id+' · '+(g.ddp??'нет DDP')+' USD']));
    for(const label of labels.values()){const option=element('option');option.value=label;datalist.append(option);}groups.append(datalist);
    const matchInput=(group,key,label)=>{const wrap=element('label',label+' '),field=element('input');field.type='search';field.placeholder='Поиск группы закупок…';field.setAttribute('list',datalist.id);field.setAttribute('aria-label',label+' · '+group.name);field.dataset.purchaseKey=key;wrap.append(field);return {wrap,field};};
    for(const group of catalog.groups){
      if(!shown(group.id))continue;
      const article=element('article');article.dataset.gid=group.id;article.dataset.name=group.name.toLocaleLowerCase('ru');article.style.cssText='padding:10px 0;border-bottom:1px solid #ddd';
      const label=element('label'),check=element('input');check.type='checkbox';check.dataset.ddpTarget='';check.value=group.id;check.checked=initialized?previous.has(group.id):eligible(group.id);label.append(check,document.createTextNode(' '+group.name));article.append(label);
      if(source.value==='purchase'){
        const mode=element('select');mode.dataset.purchaseMode='';mode.setAttribute('aria-label','База DDP · '+group.name);
        for(const [value,text] of [['group','DDP всей группы'],['powers','Сопоставить по мощностям']]){const option=element('option',text);option.value=value;mode.append(option);}
        mode.value=group.matches.length===1?'group':'powers';article.append(mode);
        const whole=matchInput(group,'group','Группа закупок');
        if(group.matches.length===1)whole.field.value=labels.get(group.matches[0]);
        const powers=element('div');for(const power of group.powers){const field=matchInput(group,power,'Мощность '+power);powers.append(field.wrap,element('br'));}
        const sync=()=>{whole.wrap.hidden=mode.value!=='group';powers.hidden=mode.value!=='powers';};sync();mode.addEventListener('change',sync);article.append(whole.wrap,powers);
      }
      groups.append(article);
    }
    filter();
  }
  async function load(){
    invalidate();setBusy(true);status.textContent='Подбираем группы и источники…';
    try{catalog=await request('ddp_catalog',{purchase:source.value==='purchase'?purchase.value:''});draw();status.textContent='Выберите группы и нажмите «Предпросмотр».';}
    catch(error){status.textContent=error.message;groups.replaceChildren();}
    finally{setBusy(false);}
  }
  function sources(){dialog.querySelectorAll('[data-ddp-fact]').forEach(el=>el.hidden=source.value!=='fact');dialog.querySelectorAll('[data-ddp-purchase]').forEach(el=>el.hidden=source.value!=='purchase');load();}
  document.querySelector('[data-ddp-open]').addEventListener('click',()=>{groups.replaceChildren();dialog.showModal();sources();});
  source.addEventListener('change',sources);purchase.addEventListener('change',load);
  find('search').addEventListener('input',()=>{filter();invalidate();});
  find('empty').addEventListener('change',()=>{filter();invalidate();});
  groups.addEventListener('change',invalidate);groups.addEventListener('input',invalidate);
  for(const name of ['year','surcharge'])find(name).addEventListener('input',invalidate);
  find('all').addEventListener('click',()=>{groups.querySelectorAll('[data-ddp-target]').forEach(el=>{if(!el.closest('article').hidden)el.checked=true;});invalidate();});
  find('none').addEventListener('click',()=>{groups.querySelectorAll('[data-ddp-target]').forEach(el=>el.checked=false);invalidate();});
  find('close').addEventListener('click',()=>dialog.close());dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  find('preview-button').addEventListener('click',async()=>{
    const targets=selected();if(!targets.length){status.textContent='Выберите хотя бы одну группу.';return;}
    if(source.value==='fact'&&(!find('year').reportValidity()||!find('surcharge').reportValidity()))return;
    const goodsByLabel=new Map((catalog.goods||[]).map(g=>[g.name+' · #'+g.id+' · '+(g.ddp??'нет DDP')+' USD',g.id]));
    const mapping={};groups.querySelectorAll('article').forEach(article=>{
      const mode=article.querySelector('[data-purchase-mode]');if(!mode)return;
      const fields=[...article.querySelectorAll('[data-purchase-key]')];
      mapping[article.dataset.gid]=mode.value==='group'?(goodsByLabel.get(fields.find(el=>el.dataset.purchaseKey==='group').value)||''):Object.fromEntries(fields.filter(el=>el.dataset.purchaseKey!=='group').map(el=>[el.dataset.purchaseKey,goodsByLabel.get(el.value)||'']));
    });
    invalidate();setBusy(true);status.textContent='Рассчитываем DDP · групп: '+targets.length+'…';
    try{
      const data=await request('preview_ddp',{targets:JSON.stringify(targets),mapping:JSON.stringify(mapping),source:source.value,purchase:purchase.value,year:find('year').value,surcharge:find('surcharge').value});
      const table=element('table');table.className='admin-table';const heading=element('tr');['Группа','Текущий DDP, USD','Предложение, USD','Источник / пояснение'].forEach(text=>heading.append(element('th',text)));const thead=element('thead');thead.append(heading);table.append(thead);const tbody=element('tbody');
      for(const row of data.rows){
        const tr=element('tr');tr.append(element('td',row.name),element('td',input(row.gid)?.value||'—'),element('td',row.proposed??'—'));
        const description=row.error||[row.source.source,row.source.year?'Год '+row.source.year+', по '+row.source.through+'; надбавка '+row.source.surcharge_percent+'%':row.source.purchase_title,row.source.note||'',row.powers.map(p=>p.power+': '+p.ddp+' USD ('+p.share+'%)').join('; ')].filter(Boolean).join(' · ');
        tr.append(element('td',description));tbody.append(tr);
      }
      table.append(tbody);find('preview').append(table);proposals=data.rows.filter(row=>row.token&&!row.error);
      status.textContent='Готово к вставке: '+proposals.length+'. Без данных: '+(data.rows.length-proposals.length)+'. Годовой факт берётся только за выбранный год, до доступной даты.';
    }catch(error){status.textContent=error.message;}finally{setBusy(false);}
  });
  apply.addEventListener('click',()=>{
    let count=0;for(const row of proposals){if(!eligible(row.gid))continue;const field=input(row.gid),token=form.elements.namedItem('ddp_proposal_'+row.gid);if(!field||!token)continue;field.value=row.proposed;token.value=row.token;field.dispatchEvent(new Event('input',{bubbles:true}));count++;}
    dialog.close();window.SplitProgress?.start('DDP').finish('Вставлено групп: '+count+'. Сохраните изменения.');proposals=[];apply.disabled=true;
  });
})();
