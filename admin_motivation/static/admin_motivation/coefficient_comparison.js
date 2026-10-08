(() => {
    const panel = document.getElementById('comparison-panel');
    if (!panel) return;
    const plan = document.getElementById('comparison-plan');
    const baseline = document.getElementById('comparison-baseline');
    const status = document.getElementById('comparison-status');
    const error = document.getElementById('comparison-error');
    const warning = document.getElementById('comparison-warning');
    const dialog = document.getElementById('approval-dialog');
    const commit = document.getElementById('approval-commit');
    const money = value => value == null ? '—' : new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0}).format(Number(value));
    const percent = value => value == null ? '—' : `${new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1}).format(Number(value))}%`;
    let controller, timer, review, revision = 0;
    const prefix = panel.dataset.subdivision ? 'sub_coeff=' : 'global_coeff=';
    const inputs = () => [...document.querySelectorAll(`input[name^="${prefix}"]`)];
    function parameters(action) {
        const changes = {};
        inputs().filter(input => !input.disabled).forEach(input => changes[input.name.split('=')[1]] = input.value);
        const deleted = [...document.querySelectorAll('input[name^="delete_coeff="]:checked')].map(input => input.name.split('=')[1]);
        return {action, version: panel.dataset.version, subdivision: panel.dataset.subdivision || null,
                plan: plan.value || null, baseline: baseline.value || null, changes, deleted};
    }
    async function post(data, signal) {
        const token = document.querySelector('input[name=csrfmiddlewaretoken]')?.value;
        const response = await fetch(panel.dataset.endpoint, {method: 'POST', signal,
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': token}, body: JSON.stringify(data)});
        if (!response.headers.get('Content-Type')?.includes('application/json')) throw new Error(`Ошибка сервера: HTTP ${response.status}`);
        const result = await response.json();
        if (!response.ok || result.error) throw new Error(result.error || 'Операция не выполнена.');
        return result;
    }
    const visibilityKey = 'motivation-comparison-visibility';
    let visibility = {approved: true, baseline: false};
    try { visibility = {...visibility, ...JSON.parse(localStorage.getItem(visibilityKey) || '{}')}; } catch (_) {}
    for (const name of ['approved', 'baseline']) {
        const toggle = document.getElementById(`comparison-show-${name}`);
        toggle.checked = visibility[name];
        const apply = () => {
            visibility[name] = toggle.checked;
            panel.querySelectorAll(`[data-variant="${name}"]`).forEach(cell => cell.hidden = !toggle.checked);
            try { localStorage.setItem(visibilityKey, JSON.stringify(visibility)); } catch (_) {}
        };
        toggle.addEventListener('change', apply);
        apply();
    }
    async function compare() {
        const requestRevision = ++revision;
        controller?.abort();
        panel.querySelectorAll('[data-amount], [data-delta]').forEach(cell => {
            cell.textContent = '—'; cell.title = '';
            cell.classList.remove('delta-up', 'delta-down');
        });
        warning.hidden = true; error.hidden = true;
        if (!plan.value) { status.textContent = 'Выберите план для сравнения.'; return; }
        controller = new AbortController();
        error.hidden = true;
        status.textContent = 'Сравниваем варианты на одном плане…';
        try {
            const result = await post(parameters('compare'), controller.signal);
            if (requestRevision !== revision) return;
            panel.querySelectorAll('[data-amount]').forEach(cell => {
                const [variant, field] = cell.dataset.amount.split('.');
                cell.textContent = money(result.variants[variant][field]);
                cell.title = result.variants[variant].error || '';
            });
            panel.querySelectorAll('[data-delta]').forEach(cell => {
                const [variant, field] = cell.dataset.delta.split('.');
                const delta = result.deltas[variant][field];
                cell.textContent = `${money(delta.amount)} / ${percent(delta.percent)}`;
                cell.classList.toggle('delta-up', Number(delta.amount) > 0);
                cell.classList.toggle('delta-down', Number(delta.amount) < 0);
            });
            status.textContent = result.unsaved ? 'Предварительный расчёт: учтены несохранённые изменения.' : 'Сравнение по сохранённым коэффициентам.';
            const messages = Object.entries(result.variants).filter(([key, value]) => value.error && (key !== 'baseline' || baseline.value)).map(([key, value]) => `${key === 'approved' ? 'Утверждённый' : key === 'baseline' ? 'Базовый' : 'Текущий'}: ${value.error}`);
            const replaced = result.replaced_versions.map(version => version.title);
            if (replaced.length) messages.push(`Текущий вариант заменяет версии с новой даты: ${replaced.join('; ')}. До утверждения история не меняется.`);
            if (result.baseline_replaced) messages.push('Дата текущей версии не позже базовой. Базовая остаётся фиксированным эталоном.');
            warning.hidden = !messages.length;
            warning.textContent = messages.join(' ');
        } catch (problem) {
            if (problem.name === 'AbortError') return;
            error.hidden = false;
            error.textContent = problem.message;
            status.textContent = 'Сравнение не выполнено.';
        }
    }
    const schedule = () => { clearTimeout(timer); timer = setTimeout(compare, 600); };
    inputs().forEach(input => input.addEventListener('input', schedule));
    document.querySelectorAll('input[name^="delete_coeff="]').forEach(input => input.addEventListener('change', schedule));
    plan.addEventListener('change', compare);
    baseline.addEventListener('change', compare);
    document.getElementById('comparison-refresh').addEventListener('click', compare);
    document.getElementById('approval-review')?.addEventListener('click', async () => {
        error.hidden = true;
        try {
            review = await post(parameters('review'));
            document.getElementById('approval-description').textContent = `Начало действия: ${review.date}. ${review.unsaved ? 'Несохранённые изменения будут сохранены при утверждении.' : 'Будут утверждены сохранённые коэффициенты.'}`;
            const list = document.getElementById('approval-replaced');
            list.replaceChildren();
            review.replaced.forEach(version => { const item = document.createElement('li'); item.textContent = `${version.title} · ${version.status} → заменена`; list.append(item); });
            const impact = document.getElementById('approval-impact');
            impact.replaceChildren();
            for (const [key, title] of [['global', 'Глобальный план'], ['subdivisions', 'Подразделения суммарно']]) {
                const result = review.summary[key];
                if (!result) continue;
                const line = document.createElement('p');
                line.textContent = `${title}: ${money(result.variants.approved.total)} → ${money(result.variants.current.total)} USD; изменение ${money(result.deltas.approved.total.amount)} USD.`;
                if (result.variants.current.error) line.textContent += ` ${result.variants.current.error}`;
                impact.append(line);
            }
            if (!plan.value) impact.textContent = 'План продаж не выбран: утверждение без оценки суммы мотивации.';
            const needsConfirm = review.replaced.length > 0 || review.baseline_replaced;
            document.getElementById('approval-confirm-label').hidden = !needsConfirm;
            document.getElementById('approval-baseline-warning').hidden = !review.baseline_replaced;
            document.getElementById('approval-confirm').checked = false;
            document.getElementById('approval-error').textContent = '';
            dialog.showModal();
        } catch (problem) { error.hidden = false; error.textContent = problem.message; }
    });
    document.getElementById('approval-cancel').addEventListener('click', () => dialog.close());
    commit.addEventListener('click', async () => {
        commit.disabled = true;
        try {
            await post({action: 'approve', token: review.token, confirm_overwrite: document.getElementById('approval-confirm').checked});
            location.reload();
        } catch (problem) {
            document.getElementById('approval-error').textContent = problem.message;
            commit.disabled = false;
        }
    });
    if (plan.value) compare();
})();
