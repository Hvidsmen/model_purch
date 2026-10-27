/* Spreadsheet navigation and read-only preflight. Both reuse the editor's undo/save path. */
(() => {
 const initialize=()=>{
  const table=document.getElementById('coefficient-table'),toolbar=document.getElementById('editor-toolbar'),open=document.getElementById('editor-paste-open');
  if(!table||!open||!window.motivationEditor)return;
  const editor=window.motivationEditor,by=id=>document.getElementById(id),dialog=by('editor-sheet-dialog');
  let anchor=null,last=null,selected=[],pending=[],mode='paste';
  const visibleRows=()=>[...table.querySelectorAll('.coefficient-row')].filter(row=>!row.hidden);
  const cells=row=>[...row.querySelectorAll('td[data-coefficient-group]')].filter(cell=>!cell.hidden);
  const input=cell=>cell?.querySelector('.coeff-input');
  const coordinate=field=>{const rows=visibleRows(),row=field.closest('.coefficient-row');return {rows,row,y:rows.indexOf(row),x:cells(row).indexOf(field.closest('td'))};};
  const text=field=>{const row=field.closest('.coefficient-row');return [row.dataset.pg,row.dataset.group,row.dataset.brand].join(' · ');};
  const kind=field=>{const cell=field.closest('td');return ({policies:'Политики',sales:'Продажи',directors:'Директор'}[cell.dataset.coefficientGroup]||'Ставка')+' / '+(field.dataset.segment||'K'+cell.dataset.coefficientSegment);};
  const percent=value=>((value*100).toFixed(10).replace(/\.?0+$/,'')||'0')+'%';
  const parse=(value,units)=>{
   const raw=String(value).trim().replace(/[\s\u00a0]/g,'').replace(',','.').replace('−','-');
   if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?%?$/.test(raw))throw Error('Неверное число: '+value);
   const n=Number(raw.replace(/%$/,''))/(raw.endsWith('%')||units==='percent'?100:1);
   if(!Number.isFinite(n)||Math.abs(n)>1)throw Error('Ставка выходит за диапазон −100%…100%: '+value);
   return n;
  };
  const clear=()=>{table.querySelectorAll('.editor-cell-selected').forEach(cell=>cell.classList.remove('editor-cell-selected'));selected=[];};
  const select=(first,target)=>{
   clear();const a=coordinate(first),b=coordinate(target);
   if(a.y<0||b.y<0||a.x<0||b.x<0)return;
   for(let y=Math.min(a.y,b.y);y<=Math.max(a.y,b.y);y++)for(let x=Math.min(a.x,b.x);x<=Math.max(a.x,b.x);x++){
    const cell=cells(a.rows[y])[x];if(cell){cell.classList.add('editor-cell-selected');selected.push(input(cell));}
   }
   by('editor-cell-selection').textContent=`Выбрано ячеек: ${selected.length}. Заполнение берёт ставку первой ячейки диапазона.`;
   by('editor-fill').disabled=selected.length<2;
  };
  table.addEventListener('pointerdown',event=>{
   const field=event.target.closest('.coeff-input');if(!field||field.matches(':disabled'))return;
   last=field;
   if(event.shiftKey&&anchor){event.preventDefault();select(anchor,field);}else{anchor=field;select(field,field);}
  });
  table.addEventListener('focusin',event=>{if(event.target.matches('.coeff-input:not(:disabled)'))last=event.target;});
  table.addEventListener('keydown',event=>{
   const field=event.target;if(!field.matches('.coeff-input')||event.altKey||event.ctrlKey||event.metaKey)return;
   const a=coordinate(field);if(a.y<0||a.x<0)return;
   let dy=0,dx=0;
   if(event.key==='Enter')dy=event.shiftKey?-1:1;
   else if(event.key==='ArrowDown')dy=1;
   else if(event.key==='ArrowUp')dy=-1;
   else if(event.key==='ArrowLeft'&&field.selectionStart===0)dx=-1;
   else if(event.key==='ArrowRight'&&field.selectionEnd===field.value.length)dx=1;
   else return;
   event.preventDefault();let y=a.y+dy,x=a.x+dx;
   while(y>=0&&y<a.rows.length&&x>=0&&x<cells(a.rows[y]).length){
    const target=input(cells(a.rows[y])[x]);if(target&&!target.matches(':disabled')){anchor=last=target;select(target,target);target.focus();return;}y+=dy;x+=dx;
   }
  });
  const context=()=>{if(!last||last.matches(':disabled')||last.closest('tr').hidden)throw Error('Сначала выберите доступную ячейку таблицы.');by('editor-sheet-context').textContent='Начало: '+text(last)+' · '+kind(last);};
  const show=()=>{try{context();pending=[];by('editor-sheet-apply').disabled=true;by('editor-sheet-error').hidden=true;by('editor-sheet-preview').replaceChildren();dialog.showModal();}catch(error){alert(error.message);}};
  open.addEventListener('click',()=>{mode='paste';by('editor-sheet-data').value='';by('editor-sheet-data').disabled=false;show();});
  table.addEventListener('paste',event=>{
   if(!event.target.matches('.coeff-input:not(:disabled)'))return;
   const data=event.clipboardData?.getData('text/plain')||'';if(!/[\t\r\n]/.test(data))return;
   event.preventDefault();last=event.target;mode='paste';by('editor-sheet-data').disabled=false;by('editor-sheet-data').value=data;show();
  });
  by('editor-fill').addEventListener('click',()=>{
   mode='fill';last=selected[0];by('editor-sheet-data').value=last?.value||'';by('editor-sheet-data').disabled=true;show();
  });
  const invalidate=()=>{pending=[];by('editor-sheet-apply').disabled=true;};
  by('editor-sheet-data').addEventListener('input',invalidate);by('editor-sheet-units').addEventListener('change',invalidate);
  by('editor-sheet-check').addEventListener('click',()=>{
   pending=[];by('editor-sheet-error').hidden=true;by('editor-sheet-preview').replaceChildren();
   try{
    context();const targets=[];
    if(mode==='fill'){
     const value=parse(selected[0].value,'ratio');selected.forEach(field=>targets.push({field,value}));
    }else{
     const rows=by('editor-sheet-data').value.replace(/\r/g,'').replace(/\n$/,'').split('\n').map(row=>row.split('\t'));
     const a=coordinate(last);
     rows.forEach((values,dy)=>values.forEach((value,dx)=>{
      if(!value.trim())return;
      const row=a.rows[a.y+dy];if(!row)throw Error('Диапазон выходит за последнюю видимую строку.');
      const field=input(cells(row)[a.x+dx]);if(!field)throw Error('Диапазон выходит за границу доступных столбцов.');
      targets.push({field,value:parse(value,by('editor-sheet-units').value)});
     }));
    }
    for(const {field,value} of targets){
     if(!field||field.matches(':disabled')||field.closest('tr').querySelector('[name^="delete_coeff="]:checked'))throw Error('Диапазон содержит недоступную или удаляемую ячейку. Изменения не применены.');
     const rounded=Number(value.toFixed(field.classList.contains('director-input')?6:12));
     if(Math.abs(rounded-parse(field.value,'ratio'))>1e-14)pending.push({input:field,original:field.value,value:percent(rounded)});
    }
    if(!pending.length)throw Error('Нет новых значений для применения.');
    for(const change of pending.slice(0,100)){
     const row=document.createElement('tr');for(const value of [text(change.input),kind(change.input),percent(parse(change.original,'ratio')),change.value]){const td=document.createElement('td');td.textContent=value;row.append(td);}by('editor-sheet-preview').append(row);
    }
    by('editor-sheet-context').textContent+=` · Изменится: ${pending.length}. Показаны первые 100, все правки доступны в «Изменения».`;
    by('editor-sheet-apply').disabled=false;
   }catch(error){pending=[];by('editor-sheet-error').textContent=error.message;by('editor-sheet-error').hidden=false;by('editor-sheet-apply').disabled=true;}
  });
  by('editor-sheet-apply').addEventListener('click',()=>{
   if(!pending.length)return;
   if(pending.some(change=>change.input.value!==change.original||change.input.matches(':disabled'))){invalidate();by('editor-sheet-error').hidden=false;by('editor-sheet-error').textContent='Значения изменились после просмотра. Проверьте вставку снова.';return;}
   editor.apply(pending,mode==='fill'?'Заполнение диапазона':'Вставка Excel');pending=[];dialog.close();
  });
  by('editor-sheet-cancel').addEventListener('click',()=>dialog.close());
  const check=by('editor-preflight'),checkDialog=by('editor-preflight-dialog');let controller;
  check.addEventListener('click',async()=>{
   checkDialog.showModal();by('editor-preflight-status').textContent='Проверяем правки и покрытие по выбранному плану…';by('editor-preflight-result').replaceChildren();by('editor-preflight-coverage').hidden=true;by('editor-preflight-note').textContent='';check.disabled=true;
   controller=new AbortController();const timer=setTimeout(()=>controller.abort(),30000);
   try{
    const response=await fetch(check.dataset.endpoint,{method:'POST',credentials:'same-origin',signal:controller.signal,headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('[name=csrfmiddlewaretoken]').value},body:JSON.stringify({fields:editor.patch(),subdivision:toolbar.dataset.subdivision||null,plan:by('comparison-plan')?.value||null})});
    if(!response.headers.get('content-type')?.includes('application/json'))throw Error('Проверка недоступна. Обновите страницу и проверьте доступ к выбранному плану.');
    const result=await response.json();if(!response.ok)throw Error(result.error||'Не удалось проверить черновик.');
    by('editor-preflight-status').textContent='Правки корректны. '+result.summary;
    const lines=result.coverage?[`Проверено сочетаний: ${result.coverage.examined}`,`Без полного коэффициента: ${result.coverage.missing}`,`Отличающаяся формула: ${result.coverage.formula_mismatches}`]:['План не выбран: проверены значения и область изменений. Для проверки полноты выберите план.'];
    for(const line of lines){const p=document.createElement('p');p.textContent=line;by('editor-preflight-result').append(p);}
    if(result.coverage?.issues?.length){const list=document.createElement('ul');for(const issue of result.coverage.issues.slice(0,20)){const li=document.createElement('li');li.textContent=[issue.subdivision,issue.planning,issue.group,issue.brand,issue.kind,issue.employee,issue.reason,issue.missing].filter(Boolean).join(' · ');list.append(li);}by('editor-preflight-result').append(list);}
    by('editor-preflight-note').textContent=result.save_first?'Проверка учитывает правки таблицы, но не сохраняет их. Перед переходом к созданию пропусков сохраните правки.':'Проверка ничего не сохраняет. Можно открыть проблемы и подготовить недостающие коэффициенты.';
    by('editor-preflight-coverage').href=result.coverage_url;by('editor-preflight-coverage').hidden=false;
   }catch(error){if(error.name!=='AbortError'||checkDialog.open)by('editor-preflight-status').textContent=error.name==='AbortError'?'Проверка не завершена. Повторите её или откройте отдельную проверку покрытия.':error.message;}
   finally{clearTimeout(timer);check.disabled=false;}
  });
  by('editor-preflight-close').addEventListener('click',()=>checkDialog.close());checkDialog.addEventListener('close',()=>controller?.abort());
 };
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initialize,{once:true});else initialize();
})();
