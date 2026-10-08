(() => {
    const panel = document.getElementById('plan-progress');
    if (!panel) return;
    const stage = document.getElementById('plan-progress-stage');
    const bar = document.getElementById('plan-progress-bar');
    const counter = document.getElementById('plan-progress-count');
    const error = document.getElementById('plan-progress-error');
    const elapsed = document.getElementById('plan-progress-time');
    const forms = [...document.querySelectorAll('form[data-plan-operation]')];
    let busy = false;
    const storageKey = `motivation-operation:${location.pathname}`;
    const prior = sessionStorage.getItem(storageKey);
    if (prior) {
        sessionStorage.removeItem(storageKey);
        panel.hidden = false;
        stage.textContent = prior;
        bar.style.width = '100%';
        bar.textContent = '100%';
        bar.setAttribute('aria-valuenow', '100');
        bar.classList.remove('progress-bar-animated');
    }
    forms.forEach(form => form.addEventListener('submit', async event => {
        if (event.defaultPrevented) return;
        event.preventDefault();
        if (busy) return;
        busy = true;
        const started = Date.now();
        const buttons = forms.map(item => item.querySelector('button'));
        const priorDisabled = buttons.map(button => button.disabled);
        const body = new FormData(form);
        buttons.forEach(button => button.disabled = true);
        panel.hidden = false;
        error.hidden = true;
        error.textContent = '';
        counter.textContent = '';
        stage.textContent = 'Подготовка операции';
        bar.textContent = '';
        bar.style.width = '100%';
        bar.removeAttribute('aria-valuenow');
        bar.classList.add('progress-bar-animated');
        const timer = setInterval(() => elapsed.textContent = `Прошло ${Math.floor((Date.now() - started) / 1000)} сек.`, 1000);
        let finished = false;
        const receive = data => {
            if (data.type === 'heartbeat') return;
            if (data.type === 'error') throw new Error(data.message);
            stage.textContent = data.stage;
            if (data.percent == null) {
                bar.style.width = '100%';
                bar.textContent = '';
                bar.removeAttribute('aria-valuenow');
            } else {
                bar.style.width = `${data.percent}%`;
                bar.textContent = `${data.percent}%`;
                bar.setAttribute('aria-valuenow', String(data.percent));
            }
            counter.textContent = data.total != null ? `Обработано ${data.processed.toLocaleString('ru-RU')} из ${data.total.toLocaleString('ru-RU')} строк` : data.processed != null ? `Обработано ${data.processed.toLocaleString('ru-RU')} строк` : '';
            if (data.type === 'complete') {
                finished = true;
                const message = `${data.stage}: ${data.count.toLocaleString('ru-RU')} строк. За ${Math.floor((Date.now() - started) / 1000)} сек.`;
                sessionStorage.setItem(storageKey, message);
                location.reload();
            }
        };
        try {
            const response = await fetch(location.href, {method: 'POST', body, headers: {'Accept': 'application/x-ndjson'}});
            if (!response.ok || !response.headers.get('Content-Type')?.includes('application/x-ndjson')) {
                throw new Error(`Сервер не начал операцию (HTTP ${response.status}). Обновите страницу и повторите.`);
            }
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            while (true) {
                const {value, done} = await reader.read();
                buffer += decoder.decode(value, {stream: !done});
                let end;
                while ((end = buffer.indexOf('\n')) >= 0) {
                    const line = buffer.slice(0, end);
                    buffer = buffer.slice(end + 1);
                    if (line.trim()) receive(JSON.parse(line));
                }
                if (done) break;
            }
            if (!finished) throw new Error('Связь с сервером прервалась. Операция может ещё выполняться; обновите страницу перед повторным запуском.');
        } catch (problem) {
            error.textContent = problem.message;
            error.hidden = false;
            stage.textContent = 'Операция не завершена';
            bar.classList.remove('progress-bar-animated');
        } finally {
            clearInterval(timer);
            busy = false;
            if (!finished) buttons.forEach((button, i) => button.disabled = priorDisabled[i]);
        }
    }));
})();
