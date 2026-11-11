(() => {
    const form = document.getElementById('director-calculation-form');
    if (!form) return;
    const panel = document.getElementById('director-progress');
    const state = document.getElementById('director-progress-state');
    const stage = document.getElementById('director-progress-stage');
    const bar = document.getElementById('director-progress-bar');
    const time = document.getElementById('director-progress-time');
    const connection = document.getElementById('director-progress-connection');
    const error = document.getElementById('director-calculation-error');
    const submit = form.querySelector('button[type=submit],button:not([type])');
    const storageKey = 'director-comparison-progress:' + location.pathname;
    let active = false, pollTimer, clock, started = Date.now(), stageSeconds = 0, stageObserved = Date.now();
    const duration = value => `${Math.floor(value / 60)} мин ${Math.floor(value % 60)} сек`;
    function stop() {
        active = false;
        clearTimeout(pollTimer); clearInterval(clock);
        submit.disabled = false;
        form.setAttribute('aria-busy', 'false');
        try { sessionStorage.removeItem(storageKey); } catch (_) {}
    }
    function show() {
        panel.hidden = false; error.hidden = true; active = true;
        submit.disabled = true; form.setAttribute('aria-busy', 'true');
        state.textContent = 'Выполняется сравнение директоров';
        clearInterval(clock);
        const tick = () => time.textContent = `Всего: ${duration((Date.now() - started) / 1000)} · текущий этап: ${duration(stageSeconds + (Date.now() - stageObserved) / 1000)}`;
        tick(); clock = setInterval(tick, 1000);
    }
    async function poll(token) {
        if (!active) return;
        try {
            const url = new URL(location.href); url.search = ''; url.searchParams.set('progress', token);
            const response = await fetch(url, {cache: 'no-store', headers: {Accept: 'application/json'}});
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Статус недоступен');
            if (!active) return;
            connection.textContent = '';
            stage.textContent = data.stage;
            if (data.state === 'pending' && Date.now() - started > 30000) {
                state.textContent = 'Сервер ещё не подтвердил запуск';
                stop(); connection.textContent = 'Обновите страницу перед повторной попыткой.';
                return;
            }
            if (data.step !== undefined) bar.value = Math.max(0, data.step - (data.state === 'completed' ? 0 : 1));
            if (data.elapsed !== undefined) started = Date.now() - data.elapsed * 1000;
            stageSeconds = data.stage_elapsed || 0; stageObserved = Date.now();
            if (data.state === 'completed') {
                state.textContent = 'Расчёт завершён'; stop();
                if (data.url) location.assign(data.url);
                return;
            }
            if (data.state === 'failed') {
                state.textContent = 'Расчёт завершился с ошибкой'; stop();
                connection.textContent = 'Откройте страницу заново перед повторным запуском.';
                return;
            }
        } catch (_) {
            if (active) connection.textContent = 'Не удалось обновить статус. Повторяем проверку; последний показанный этап сохранён.';
        }
        if (active) pollTimer = setTimeout(() => poll(token), 2000);
    }
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (active || !form.reportValidity()) return;
        const body = new FormData(form), token = body.get('progress_token');
        started = Date.now(); stageSeconds = 0; stageObserved = started;
        stage.textContent = 'Отправка запроса'; bar.removeAttribute('value');
        connection.textContent = ''; show();
        try { sessionStorage.setItem(storageKey, JSON.stringify({token, started})); } catch (_) {}
        poll(token);
        try {
            const response = await fetch(form.action || location.href, {method: 'POST', body, headers: {Accept: 'application/json'}});
            const data = await response.json();
            if (!response.ok) {
                if (data.progress_token) form.elements.progress_token.value = data.progress_token;
                throw new Error(data.error || 'Не удалось выполнить сравнение.');
            }
            stop(); state.textContent = 'Расчёт завершён'; bar.value = 12;
            location.assign(data.url);
        } catch (problem) {
            stop(); state.textContent = 'Запрос завершился с ошибкой';
            error.textContent = problem.message; error.hidden = false;
        }
    });
    try {
        const saved = JSON.parse(sessionStorage.getItem(storageKey));
        if (saved?.token) { started = saved.started; stage.textContent = 'Проверка ранее запущенного расчёта'; show(); poll(saved.token); }
    } catch (_) {}
})();
