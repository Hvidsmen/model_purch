(() => {
  if (window.SplitProgress) return;
  const region=document.createElement('section');region.className='split-operation-status';region.hidden=true;region.setAttribute('aria-label','Состояние операции');
  const title=document.createElement('strong');title.setAttribute('role','status');title.setAttribute('aria-live','polite');
  const meter=document.createElement('progress');meter.setAttribute('aria-label','Ход выполнения');
  const detail=document.createElement('span');
  const close=document.createElement('button');close.type='button';close.textContent='Закрыть';close.hidden=true;
  region.append(title,meter,detail,close);document.body.append(region);
  const jobs=new Map();let sequence=0;
  function render() {
    const active=[...jobs.values()].filter(job=>job.state==='running');
    const job=active.at(-1) || [...jobs.values()].at(-1);
    region.hidden=!job;if(!job)return;
    title.textContent=job.label;
    region.dataset.state=job.state;
    close.hidden=job.state==='running';
    meter.hidden=job.state==='error';
    if(job.total){meter.max=job.total;meter.value=job.done;meter.setAttribute('aria-valuetext',`${job.done} из ${job.total}`);}
    else if(job.state==='done'){meter.max=1;meter.value=1;meter.setAttribute('aria-valuetext','Готово');}
    else{meter.removeAttribute('value');meter.removeAttribute('aria-valuetext');}
    const seconds=Math.floor((Date.now()-job.started)/1000);
    detail.textContent=job.message || (job.total ? `Обработано ${job.done} из ${job.total} · осталось ${Math.max(0,job.total-job.done)} · ${seconds} сек.` : `Выполняется · ${seconds} сек. Дождитесь завершения.`);
    if(active.length>1)detail.textContent+=' Активных операций: '+active.length;
  }
  function start(label,total=0) {
    jobs.forEach((job,id)=>{if(job.state!=='running')jobs.delete(id);});
    const id=++sequence, job={label,total,done:0,state:'running',started:Date.now(),message:''};jobs.set(id,job);render();
    return {update(done,message=''){job.done=done;job.message=message;render();},
      finish(message='Готово'){job.state='done';job.done=job.total;job.message=message;render();},
      fail(message){job.state='error';job.message=message;render();},
      remove(){jobs.delete(id);render();}};
  }
  close.addEventListener('click',()=>{jobs.forEach((job,id)=>{if(job.state!=='running')jobs.delete(id);});render();});
  setInterval(()=>{if([...jobs.values()].some(job=>job.state==='running'))render();},1000);
  window.SplitProgress={start};
  let submitting=new WeakSet();
  document.addEventListener('submit',event=>{
    const form=event.target;
    if(!(form instanceof HTMLFormElement))return;
    if(submitting.has(form)){event.preventDefault();return;}
    queueMicrotask(()=>{
      if(event.defaultPrevented)return;
      submitting.add(form);
      start(event.submitter?.textContent.trim() || 'Сохранение изменений');
    });
  });
  window.addEventListener('pageshow',event=>{if(event.persisted){submitting=new WeakSet();jobs.clear();render();}});
  document.addEventListener('click',async event=>{
    const link=event.target.closest('a[href]');if(!link||event.defaultPrevented||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
    const url=new URL(link.href,location.href);
    if(url.origin!==location.origin||!url.searchParams.has('export'))return;
    event.preventDefault();const job=start('Подготовка и загрузка файла');
    try{
      const response=await fetch(url,{credentials:'same-origin'});
      if(!response.ok || !response.headers.get('content-disposition')?.includes('attachment'))throw new Error('Файл не получен. Проверьте данные плана и повторите выгрузку.');
      const blob=await response.blob(), objectURL=URL.createObjectURL(blob);
      const disposition=response.headers.get('content-disposition');
      const encoded=disposition.match(/filename\*=UTF-8''([^;]+)/i), plain=disposition.match(/filename="?([^";]+)/i);
      const download=document.createElement('a');download.href=objectURL;download.download=encoded?decodeURIComponent(encoded[1]):plain?plain[1]:'export';download.click();setTimeout(()=>URL.revokeObjectURL(objectURL),60000);job.finish('Файл передан браузеру для сохранения.');
    }catch(error){job.fail(error.message);}
  });
})();
