(() => {
 const button = document.getElementById('ad-find');
 if (!button) return;
 const query = document.getElementById('ad-query'), status = document.getElementById('ad-status'), results = document.getElementById('ad-results');
 let pending;
 button.addEventListener('click', async () => {
  pending?.abort(); pending = new AbortController(); results.replaceChildren();
  if (query.value.trim().length < 3) { status.textContent = 'Введите не менее трёх символов.'; return; }
  status.textContent = 'Поиск в домене…'; button.disabled = true;
  try {
   const url = new URL(button.dataset.url, location.origin); url.searchParams.set('q', query.value.trim());
   const response = await fetch(url, {credentials: 'same-origin', signal: pending.signal}); const data = await response.json();
   if (!response.ok) { status.textContent = data.error || 'Поиск недоступен.'; return; }
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
  } catch (error) { if (error.name !== 'AbortError') status.textContent = 'Поиск AD недоступен. Проверьте соединение с сервером.'; }
  finally { button.disabled = false; }
 });
 query.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); button.click(); } });
})();
