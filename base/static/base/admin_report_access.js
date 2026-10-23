(() => {
 const branch = document.getElementById('id_report_subdivision');
 const author = document.getElementById('id_report_author');
 if (!branch || !author) return;
 const options = document.createElement('datalist'); options.id = 'report-author-options';
 author.after(options);
 const status = document.createElement('small'); status.setAttribute('role', 'status'); author.after(status);
 const role = document.getElementById('id_portal_role');
 const first = document.getElementById('id_first_name');
 const last = document.getElementById('id_last_name');
 const configuredGroups = new Set((role?.dataset.reportGroups || '').split(',').filter(Boolean));
 const groupRoles = JSON.parse(role?.dataset.groupRoles || '{}');
 const selectedIds = () => [...document.querySelectorAll('input[name="groups"]:checked, select[name="groups"] option:checked')].map(input => input.value).filter(id => configuredGroups.has(id));
 const selectedGroups = () => selectedIds().length > 0;
 const inheritedRoles = () => [...new Set(selectedIds().map(id => groupRoles[id] || 'Политик'))];
 const effectiveRole = () => role?.value || (inheritedRoles().length === 1 ? inheritedRoles()[0] : '');
 const roleField = role?.closest('.access-field') || role?.closest('.form-row');
 const branchField = branch.closest('.access-field') || branch.closest('.form-row');
 const authorField = author.closest('.access-field') || author.closest('.form-row');
 let editRole = false;
 const changeRole = document.createElement('button'); changeRole.type = 'button'; changeRole.className = 'user-text-button'; changeRole.textContent = 'Изменить роль';
 changeRole.addEventListener('click', () => { editRole = true; syncGroupAccess(); role?.focus(); });
 const groupHint = document.createElement('p'); groupHint.className = 'access-explanation';
 roleField?.before(groupHint);
 function syncGroupAccess() {
  const grouped = selectedGroups();
  const inherited = grouped && (!role?.value || (role.value === 'Политик' && inheritedRoles().length === 1 && inheritedRoles()[0] === 'Политик'));
  const current = effectiveRole();
  if (roleField) roleField.hidden = inherited && !editRole;
  const personal = ['Политик', 'Продажи'].includes(current);
  if (authorField) authorField.hidden = Boolean(current) && !personal;
  if (branchField) branchField.hidden = grouped || (Boolean(current) && !personal);
  groupHint.hidden = !grouped;
  groupHint.textContent = inherited ? `Роль: ${current || 'конфликт ролей групп'} · доступ из групп. Подразделения берутся из выбранных групп.` : 'Роль задана отдельно. Подразделения для отчётов берутся из групп.';
  if (inherited) groupHint.append(' ', changeRole);
  if (current === 'Директор' && grouped) groupHint.textContent = 'Директор: все виды работников и авторы сделок в филиалах выбранных групп.';
  if (current === 'Зам. директора' && grouped) groupHint.textContent = 'Зам. директора: все политики и авторы сделок в подразделениях выбранных групп.';
  if (grouped) status.textContent = personal ? 'Подразделения определяются группами. ФИО автора сделки можно исправить вручную.' : '';
 }
 let generated = null;
 const normalize = value => value.trim().replace(/\s+/g, ' ');
 function fillAuthor() {
  if (!['Политик', 'Продажи'].includes(effectiveRole()) || !first || !last) return;
  const firstName = normalize(first.value), lastName = normalize(last.value);
  if (!firstName || !lastName) return;
  if (!author.value.trim() || author.value === generated) {
   generated = `${lastName} ${firstName}`;
   author.value = generated;
  }
 }
 role?.addEventListener('change', () => { syncGroupAccess(); fillAuthor(); });
 document.addEventListener('change', event => { if (event.target.name === 'groups') { syncGroupAccess(); fillAuthor(); } });
 first?.addEventListener('input', fillAuthor);
 last?.addEventListener('input', fillAuthor);
 let pending;
 async function load() {
  pending?.abort(); pending = new AbortController(); options.replaceChildren();
  if (selectedGroups()) { syncGroupAccess(); return; }
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
 branch.addEventListener('change', load);
 syncGroupAccess();
 fillAuthor();
 load();
})();
