(() => {
  document.querySelectorAll('[data-author-diagnostics]').forEach(button=>{
    const panel=button.closest('[data-author-diagnostics-panel]'),status=panel.querySelector('[role=status]'),progress=panel.querySelector('progress'),output=panel.querySelector('[data-author-diagnostics-output]');
    let downloadURL=null;
    button.addEventListener('click',async()=>{
      button.disabled=true;progress.hidden=false;output.replaceChildren();status.textContent='Проверяем поля автора, ERP-документы и ключи соответствия… План не меняется.';
      try{
        const body=new FormData();body.set('action','diagnose_authors');body.set('csrfmiddlewaretoken',document.querySelector('#director-calculation-form [name=csrfmiddlewaretoken]').value);
        const response=await fetch(button.dataset.url,{method:'POST',body,cache:'no-store'});
        if(!response.headers.get('content-type')?.includes('application/json'))throw new Error('Не получен ответ диагностики. Проверьте подключение и вход в портал.');
        const result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error||'Не удалось проверить источник.');
        const report=result.report,table=document.createElement('table');table.className='iteration-table';
        const thead=document.createElement('thead'),heading=document.createElement('tr');
        for(const title of ['Источник / причина','Строк факта','Документов','Сумма основы, USD']){const cell=document.createElement('th');cell.textContent=title;heading.append(cell);}thead.append(heading);table.append(thead);
        const tbody=document.createElement('tbody');for(const row of report.coverage){const tr=document.createElement('tr');for(const value of [row.label,row.rows,row.documents,row.amount_usd??'нет данных']){const td=document.createElement('td');td.textContent=String(value);tr.append(td);}tbody.append(tr);}table.append(tbody);output.append(table);
        const note=document.createElement('p');note.textContent=report.note;output.append(note);
        if(report.column_coverage?.length){
          const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='Заполненность полей автора в Sales и подготовленном факте';details.append(summary);
          for(const field of report.column_coverage){const line=document.createElement('p');line.textContent=field.table+'.'+field.column+': заполнено '+field.nonempty+' из '+field.rows;details.append(line);}output.append(details);
        }
        if(report.uses_prepared===false){const info=document.createElement('p');info.textContent='Именованных авторов сейчас: '+report.current_named_authors+'. В сохранённом запуске: '+report.saved_named_authors+'.';output.append(info);}
        else if(report.same_prepared_snapshot!==true){const warning=document.createElement('p');warning.textContent=report.same_prepared_snapshot===false?'Подготовленный источник уже отличается от источника сохранённого запуска. В отчёте указаны оба идентификатора.':'В старом запуске не сохранён идентификатор подготовленного источника; сравнить источники нельзя.';output.append(warning);}
        if(downloadURL)URL.revokeObjectURL(downloadURL);
        downloadURL=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json;charset=utf-8'}));
        const link=document.createElement('a');link.href=downloadURL;link.download='iteration-'+report.run+'-author-diagnostics.json';link.className='btn btn-outline-primary';link.textContent='Скачать диагностику авторов';output.append(link);
        status.textContent='Проверка завершена. В файле — примеры документов и доступные поля авторов. План и результаты не изменены.';
      }catch(error){status.textContent=error.message;}finally{button.disabled=false;progress.hidden=true;}
    });
  });
})();
