(() => {
  const panel = document.getElementById('monitor-update');
  if (!panel) return;
  let job = JSON.parse(document.getElementById('monitor-update-initial').textContent);
  const active = () => ['queued', 'running'].includes(job.state);
  const seconds = value => `${Math.max(0, Math.floor(value))} сек.`;
  function render() {
    document.getElementById('monitor-update-start').disabled = active();
    document.getElementById('monitor-update-stage').textContent = job.stage;
    document.getElementById('monitor-update-state').textContent = ({queued:'В очереди',running:'Выполняется',completed:'Завершено',failed:'Ошибка'})[job.state] || job.state;
    document.getElementById('monitor-update-message').textContent = job.message || '';
    document.getElementById('monitor-update-commit').textContent = job.target_commit || 'определяется';
    const now = Date.now() / 1000;
    const end = job.finished_at ? Date.parse(job.finished_at) / 1000 : now;
    document.getElementById('monitor-update-time').textContent = 'Общее время: ' + seconds(end - Date.parse(job.started_at) / 1000);
    document.getElementById('monitor-update-steps').replaceChildren(...(job.steps || []).map(step => {
      const li = document.createElement('li');
      li.textContent = step.label + ' · ' + seconds((step.finished_at || now) - step.started_at);
      return li;
    }));
    document.getElementById('monitor-update-progress').value = job.state === 'completed' ? 5 : (job.steps || []).filter(step => step.finished_at).length;
  }
  async function poll() {
    if (!active()) return;
    try {
      const url = new URL(location.href); url.searchParams.set('run', panel.dataset.run); url.searchParams.set('status', '1');
      const response = await fetch(url, {credentials:'same-origin', headers:{Accept:'application/json'}, cache:'no-store'});
      if (!response.ok) throw new Error();
      job = await response.json(); render();
    } catch (_) {
      document.getElementById('monitor-update-message').textContent = 'Не удалось проверить статус. Повторная проверка через 2 секунды.';
    }
    if (active()) setTimeout(poll, 2000);
  }
  render(); setInterval(() => { if (active()) render(); }, 1000); setTimeout(poll, 2000);
})();
