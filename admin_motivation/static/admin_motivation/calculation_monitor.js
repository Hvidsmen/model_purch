(() => {
  const cards = [...document.querySelectorAll('[data-calculation-monitor]')];

  document.querySelectorAll('input[name$=payout_threshold]').forEach(input => {
    const hint=document.createElement('small');hint.className='formula-explanation';input.after(hint);
    const update=()=>{const value=Number(input.value.replace(',','.'));hint.textContent=Number.isFinite(value) ? `Выплата начинается при выполнении плана от ${Number((value*100).toFixed(6))}%. Например: при 75% и пороге 80% выплата не наступает.` : 'Введите порог от 0 до 1.';};
    input.addEventListener('input',update);update();
  });
  if (!cards.length) return;
  function reportTarget(card,base) {
    const url=new URL(base,location.origin), branch=card.querySelector('[data-report-subdivision]')?.value;
    if(branch) url.searchParams.set('subdivision',branch);
    else url.searchParams.delete('subdivision');
    card.querySelector('[data-job-report]').href=url.pathname+url.search;
  }
  cards.forEach(card=>card.querySelector('[data-report-subdivision]')?.addEventListener('change',()=>{if(card.dataset.reportBase)reportTarget(card,card.dataset.reportBase);}));
  let active = false;
  function history(card, runs) {
    const body = card.querySelector('[data-job-history]');
    body.replaceChildren();
    card.querySelector('[data-job-history-count]').textContent = `(${runs.length})`;
    if (!runs.length) {
      const cell = document.createElement('td'); cell.colSpan = 4;
      cell.textContent = 'В журнале SQL Server Agent пока нет завершённых запусков.';
      const row = document.createElement('tr'); row.append(cell); body.append(row);
    }
    for (const run of runs) {
      const row = document.createElement('tr');
      for (const value of [run.started, run.duration, run.status, run.message]) {
        const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
      }
      body.append(row);
    }
  }
  async function refresh() {
    if (active || document.hidden) return;
    active = true;
    try {
      for (const card of cards) {
        const note = card.querySelector('[data-job-monitor-note]');
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
          const response = await fetch(card.dataset.calculationMonitor, {
            credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
            headers: {'Accept': 'application/json'}
          });
          if (!response.ok) throw new Error('monitor');
          const data = await response.json();
          card.querySelector('[data-job-status]').textContent = data.status;
          if(data.workflow) for(const [selector,label,current] of [['[data-workflow-export]','export_label','export_current'],['[data-workflow-calculation]','calculation_label','calculated_current']]) {
            const node=document.querySelector(selector);if(!node)continue;
            node.textContent=data.workflow[label];node.classList.toggle('state-success',data.workflow[current]);node.classList.toggle('state-warning',!data.workflow[current]);
          }
          history(card, data.history||[]);
          const last=card.querySelector('[data-job-last]');if(last)last.textContent=data.history?.length ? data.history[0].started+' · '+data.history[0].status : 'Запусков в журнале нет';
          card.dataset.state=data.status;
          card.querySelectorAll('button[name=action][value=run]').forEach(button=>{button.disabled=button.closest('fieldset[disabled]')!==null||['Выполняется','Ожидает запуска'].includes(data.status);});
          const link = card.querySelector('[data-job-report]');
          if (link) {
            link.hidden = !data.report_url;
            card.querySelector('[data-job-result]').hidden = !data.report_url;
            if (data.report_url) {card.dataset.reportBase=data.report_url;reportTarget(card,data.report_url);}
          }
          const provenance = card.querySelector('[data-job-provenance]');
          if (provenance) provenance.textContent = data.execution ? `Расчёт ${data.execution.completed} МСК · ${data.execution.year} · ${data.execution.plan}${data.execution.stale ? ' · Настройки изменены: нужен пересчёт' : ''}` : 'Версия завершённого расчёта пока не подтверждена. После обновления сохраните параметры задания для регистрации новых запусков.';
          note.textContent = `Проверено в ${data.checked_at} МСК · автообновление каждые 10 секунд`;
        } catch (_) {
          card.querySelectorAll('button[name=action][value=run]').forEach(button=>button.disabled=true);
          const last=card.querySelector('[data-job-last]');if(last&&last.textContent==='Проверяем…')last.textContent='Статус недоступен';
          note.textContent = 'Не удалось проверить статус. Показаны последние полученные данные; запуск недоступен до восстановления связи. Повтор проверки через 10 секунд.';
        } finally { clearTimeout(timeout); }
      }
    } finally { active = false; }
  }
  refresh();
  setInterval(refresh, 10000);
  document.addEventListener('visibilitychange', refresh);
})();
