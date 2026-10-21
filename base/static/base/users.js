(() => {
 const button = document.getElementById('ad-find');
 if (!button) return;
 const query = document.getElementById('ad-query'), status = document.getElementById('ad-status'), results = document.getElementById('ad-results');
 let pending;
 button.addEventListener('click', async () => {
  pending?.abort(); pending = new AbortController(); results.replaceChildren();
  if (query.value.trim().length < 3) { status.textContent = 'Введите не менее трёх символов.'; return; }
  status.textContent = 'Поиск в домене…'; button.disabled = true;
  const timeout = setTimeout(() => pending.abort(), 150000);
  try {
   const url = new URL(button.dataset.url, location.origin); url.searchParams.set('q', query.value.trim());
   const selectedDomain = document.getElementById('ad-domain')?.value;
   if (selectedDomain) url.searchParams.set('domain', selectedDomain);
   const started = Date.now();
   let data;
   while (true) {
    const response = await fetch(url, {credentials: 'same-origin', signal: pending.signal});
    data = await response.json();
    if (!response.ok) { status.textContent = data.error || 'Поиск недоступен.'; return; }
    if (!data.pending) break;
    if (pending.signal.aborted) throw new DOMException('Aborted', 'AbortError');
    status.textContent = `${data.message} Ожидание: ${Math.floor((Date.now() - started) / 1000)} с.`;
    await new Promise(resolve => setTimeout(resolve, 1500));
   }
   status.textContent = data.users.length ? 'Выберите сотрудника, затем назначьте роль и сохраните карточку.' : 'Ничего не найдено.';
   data.users.forEach(user => {
    const item = document.createElement('button'); item.type = 'button'; item.className = 'user-ad-result'; item.textContent = user.full_name || user.username;
    const login = document.createElement('small'); login.textContent = user.username; item.append(login);
    item.addEventListener('click', () => {
     document.getElementById('id_username').value = user.username;
     const parts = user.full_name.trim().split(/\s+/);
     document.getElementById('id_last_name').value = parts.shift() || '';
     document.getElementById('id_first_name').value = parts.join(' ');
     document.getElementById('id_password1').value = ''; document.getElementById('id_password2').value = '';
     status.textContent = 'Сотрудник выбран. Назначьте роль и доступ к отчётам, затем нажмите «Добавить пользователя».';
    }); results.append(item);
   });
  } catch (error) { status.textContent = error.name === 'AbortError' ? 'AD не ответила вовремя. Можно указать DOMAIN\\login вручную и назначить права.' : 'Поиск AD недоступен. Проверьте соединение с сервером.'; }
  finally { clearTimeout(timeout); button.disabled = false; }
 });
 const domain = document.getElementById('ad-domain'), domainStatus = document.getElementById('ad-domain-status');
 let domainsLoaded = false;
 domain?.closest('details').addEventListener('toggle', async () => {
  if (!domain.closest('details').open || domainsLoaded) return;
  domainsLoaded = true; domainStatus.textContent = 'Определяем домены леса AD…';
  try {
   const started = Date.now();
   while (true) {
    const response = await fetch(domain.dataset.url, {credentials: 'same-origin'});
    const data = await response.json();
    if (!response.ok) { domainStatus.textContent = data.error; domainsLoaded = false; return; }
    if (data.pending) {
     if (Date.now() - started > 150000) throw new Error('timeout');
     await new Promise(resolve => setTimeout(resolve, 1500)); continue;
    }
    const previous = domain.value.toLowerCase();
    domain.replaceChildren(...data.domains.map(item => { const option = document.createElement('option'); option.value = item.name; option.textContent = `${item.name} · ${item.dns}`; option.selected = item.name.toLowerCase() === previous; return option; }));
    domainStatus.textContent = `Доменов: ${data.domains.length}. Выберите домен сотрудника. Можно также указать DOMAIN\\login в поиске.`;
    break;
   }
  } catch (error) { domainStatus.textContent = 'Не удалось получить домены AD. Основной домен остаётся доступен.'; domainsLoaded = false; }
 });
 query.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); button.click(); } });
})();
