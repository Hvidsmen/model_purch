(() => {
 const branch = document.getElementById('id_report_subdivision');
 const author = document.getElementById('id_report_author');
 if (!branch || !author) return;
 const options = document.createElement('datalist'); options.id = 'report-author-options';
 author.after(options);
 const status = document.createElement('small'); status.setAttribute('role', 'status'); author.after(status);
 let pending;
 async function load(clear) {
  pending?.abort(); pending = new AbortController(); options.replaceChildren();
  if (clear) author.value = '';
  if (!branch.value) { status.textContent = 'Выберите подразделение для загрузки ФИО из отчётов.'; return; }
  status.textContent = 'Загрузка авторов сделки…';
  const url = new URL(branch.dataset.authorUrl, location.origin);
  url.searchParams.set('subdivision', branch.value);
  try {
   const response = await fetch(url, {signal: pending.signal, credentials: 'same-origin'});
   const data = await response.json();
   if (!response.ok) throw new Error('lookup');
   data.authors.forEach(name => { const option = document.createElement('option'); option.value = name; options.appendChild(option); });
   status.textContent = `Найдено авторов: ${data.authors.length}. Выберите точное ФИО из подсказки.`;
  } catch (error) {
   if (error.name !== 'AbortError') status.textContent = 'Список DWH недоступен. Можно указать точное ФИО вручную.';
  }
 }
 branch.addEventListener('change', () => load(true));
 load(false);
})();
