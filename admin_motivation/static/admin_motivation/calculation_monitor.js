(() => {
  const cards = [...document.querySelectorAll('[data-calculation-monitor]')];
  if (!cards.length) return;
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
          history(card, data.history);
          note.textContent = `Проверено в ${data.checked_at} МСК · автообновление каждые 10 секунд`;
        } catch (_) {
          note.textContent = 'Не удалось проверить статус. Показаны последние полученные данные; повтор через 10 секунд.';
        } finally { clearTimeout(timeout); }
      }
    } finally { active = false; }
  }
  refresh();
  setInterval(refresh, 10000);
  document.addEventListener('visibilitychange', refresh);
})();
