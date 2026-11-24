(() => {
  const status = document.querySelector('[data-shareholder-status]');
  if (!status) return;
  const poll = async () => {
    try {
      const response = await fetch(status.dataset.shareholderStatus, {credentials:'same-origin',headers:{Accept:'application/json'}});
      if (!response.ok) throw new Error('status');
      const data = await response.json();
      status.querySelector('[data-stage]').textContent = data.stage;
      if (data.state === 'completed' || data.state === 'failed') { location.reload(); return; }
    } catch (_) { status.querySelector('[data-stage]').textContent = 'Не удалось обновить состояние. Проверяем повторно…'; }
    setTimeout(poll, 5000);
  };
  setTimeout(poll, 1500);
})();
