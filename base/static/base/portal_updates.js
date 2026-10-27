(() => {
    const root = document.getElementById('portal-updates');
    if (!root) return;
    const base = root.dataset.base;
    const el = id => document.getElementById(`update-${id}`);
    const labels = {queued: 'Ожидание задания Windows', fetch: 'Проверка новой версии', download: 'Подготовка компонентов — портал работает', stop: 'Остановка портала', checkout: 'Обновление кода', dependencies: 'Обновление компонентов', prepare: 'Резервная копия и миграции', static: 'Подготовка файлов интерфейса', starting: 'Запуск портала', verify: 'Проверка готовности', complete: 'Портал готов'};
    const time = value => new Intl.DateTimeFormat('ru-RU', {timeZone: 'Europe/Moscow', dateStyle: 'short', timeStyle: 'medium'}).format(new Date(value));
    const storageKey = id => `portal-update:${id}`;
    const store = (key, value) => { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch (_) {} };
    const read = key => { try { return JSON.parse(sessionStorage.getItem(key)); } catch (_) { return null; } };
    let selected = root.dataset.run || null;
    let current = null, credentials = null, timer = null, generation = 0, starting = false;
    let disconnectedAt = null;
    const initial = document.getElementById('portal-update-current');
    if (initial) { try { current = JSON.parse(initial.textContent); selected ||= current?.id; } catch (_) {} }
    selected ||= read('portal-update-active');
    const csrf = () => document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
    const actionError = message => { el('action-error').hidden = !message; el('action-error').textContent = message; };

    async function request(path, options = {}) {
        const response = await fetch(base + path, {cache: 'no-store', credentials: 'same-origin', ...options, signal: AbortSignal.timeout(15000)});
        const data = await response.json();
        if (!response.ok) {
            const problem = new Error(data.error || `Ошибка запроса: ${response.status}`);
            problem.status = response.status;
            throw problem;
        }
        return data;
    }
    function render(data) {
        current = data;
        el('status-card').hidden = false;
        el('stage').textContent = data.status === 'failed' ? 'Обновление не завершено' : data.status === 'completed' ? 'Портал готов' : labels[data.stage] || 'Обновление выполняется';
        el('progress').value = data.percent || 0;
        el('detail').textContent = `${data.detail || ''}${data.target_revision ? ` · Версия ${data.target_revision.slice(0, 12)}` : ''}`;
        el('error').hidden = !data.error;
        el('error').textContent = data.error || '';
        el('open').hidden = data.status !== 'completed';
        el('retry').hidden = !(data.status === 'failed' && data.maintenance && credentials?.control);
        if (el('launch')) el('launch').hidden = data.status !== 'queued';
        if (el('start')) el('start').disabled = starting || ['queued', 'running'].includes(data.status) || !!data.maintenance;
        if (el('confirm')) el('confirm').disabled = ['queued', 'running'].includes(data.status) || !!data.maintenance;
        const events = (data.events || []).map(event => {
            const item = document.createElement('li');
            item.textContent = `${time(event.at)} МСК · ${event.detail}`;
            return item;
        });
        el('events').replaceChildren(...events);
        elapsed();
    }
    function elapsed() {
        if (!current?.started_at) return;
        const end = current.finished_at && current.status !== 'running' ? new Date(current.finished_at) : new Date();
        const seconds = Math.max(0, Math.floor((end - new Date(current.started_at)) / 1000));
        el('elapsed').textContent = `${Math.floor(seconds / 60)} мин ${seconds % 60} сек`;
    }
    function schedule(delay = 2000) {
        clearTimeout(timer);
        timer = setTimeout(poll, delay);
    }
    async function poll() {
        if (!selected) return;
        if (root.dataset.maintenance && !credentials?.token) {
            el('connection').textContent = 'Для просмотра этапов откройте вкладку, из которой запущено обновление. После завершения обновите страницу и войдите в портал.';
            return;
        }
        const expected = generation;
        try {
            const headers = credentials?.token ? {'X-Portal-Update-Status': credentials.token} : {};
            const data = await request(`status/${selected}/`, {headers});
            if (expected !== generation) return;
            disconnectedAt = null;
            render(data);
            el('connection').textContent = data.status === 'completed' ? 'Новая версия ответила и готова к работе.' : data.status === 'queued' ? 'Ожидается запуск независимого задания Windows.' : data.status === 'failed' ? 'Обновление остановлено с ошибкой. Повторный запуск автоматически не выполняется.' : 'Статус обновляется автоматически. Индикатор показывает этапы, а не оставшееся время.';
            if (data.status === 'queued' || data.status === 'running' || data.maintenance) schedule();
        } catch (error) {
            if (expected !== generation) return;
            el('status-card').hidden = false;
            if (error.status === 401 || error.status === 403) {
                el('connection').textContent = 'Нет доступа к статусу обновления. Используйте исходную вкладку запуска или войдите администратором после восстановления портала. Задание продолжает работать независимо от вкладки.';
                return;
            }
            disconnectedAt ??= Date.now();
            const offlineSeconds = Math.floor((Date.now() - disconnectedAt) / 1000);
            if (offlineSeconds >= 30) {
                el('connection').textContent = `Нет связи с сервером обновления ${Math.floor(offlineSeconds / 60)} мин ${offlineSeconds % 60} сек. На экране последний полученный этап; выполнение сейчас не подтверждено. Повторно обновление не запускайте. Проверьте состояние задания на Windows-сервере. Переподключение продолжается автоматически.`;
            } else {
                el('connection').textContent = 'Переключаемся между порталом и заданием обновления. Переподключаемся автоматически…';
            }
            schedule(3000);
        }
    }
    function select(id, data = null) {
        generation += 1;
        disconnectedAt = null;
        selected = id;
        credentials = read(storageKey(id));
        if (data) render(data);
        poll();
    }
    async function launch() {
        actionError('');
        if (el('launch')) el('launch').disabled = true;
        try {
            if (!credentials?.token) {
                credentials = await request(`credentials/${selected}/`, {method: 'POST', headers: {'X-CSRFToken': csrf()}});
                store(storageKey(selected), credentials);
                store('portal-update-active', selected);
            }
            await request(`launch/${selected}/`, {method: 'POST', headers: {'X-CSRFToken': csrf()}});
        } catch (error) {
            // The worker may have stopped Django before the dispatch response arrived.
            // Credentials are already saved, so keep reading the independent status.
            if (error.status) actionError(error.message);
            if (current?.status !== 'running') el('connection').textContent = 'Проверяем, запустилось ли отдельное задание…';
        } finally {
            if (el('launch')) el('launch').disabled = false;
            poll();
        }
    }
    el('start')?.addEventListener('click', async () => {
        actionError('');
        if (!el('confirm').checked) { actionError('Подтвердите временную остановку портала.'); return; }
        starting = true;
        el('start').disabled = true;
        try {
            const result = await request('start/', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()}, body: JSON.stringify({confirm: true})});
            store(storageKey(result.run.id), {token: result.token, control: result.control});
            store('portal-update-active', result.run.id);
            selected = result.run.id;
            credentials = {token: result.token, control: result.control};
            generation += 1;
            render(result.run);
            await launch();
        } catch (error) { actionError(error.message); }
        finally { starting = false; if (current) render(current); else el('start').disabled = false; }
    });
    el('launch')?.addEventListener('click', launch);
    el('retry')?.addEventListener('click', async () => {
        if (!credentials?.control) return;
        el('retry').disabled = true;
        actionError('');
        try {
            await request(`retry/${selected}/`, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Portal-Update-Control': credentials.control}, body: JSON.stringify({action: 'retry'})});
            el('connection').textContent = 'Задание проверяет последнюю версию и повторяет подготовку…';
        } catch (error) { actionError(error.message); }
        finally { el('retry').disabled = false; schedule(500); }
    });
    document.querySelectorAll('.update-show-run').forEach(button => button.addEventListener('click', () => select(button.dataset.run)));
    document.querySelectorAll('[data-update-time]').forEach(cell => cell.textContent = time(cell.dataset.updateTime));
    if (selected) select(selected, current);
    else if (root.dataset.maintenance) el('connection').textContent = 'Для просмотра этапов откройте вкладку, из которой администратор запустил обновление. После запуска портал снова станет доступен.';
    setInterval(elapsed, 1000);
})();
