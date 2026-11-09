(function () {
    const form = document.querySelector('[data-month-grid]');
    if (!form) return;
    const draft = form.dataset.draft === '1';
    const scale = 1000000n, hundred = 100n * scale;
    const message = form.querySelector('[data-grid-message]');
    const save = form.querySelector('[data-grid-save]');
    const csrf = form.querySelector('[name=csrfmiddlewaretoken]').value;
    const revision = form.querySelector('[name=revision]').value;
    let submitting = false, autoPending = 0, stopAuto = false;

    function parse(raw, unit) {
        let text = String(raw).replace(/[\s\u00a0\u202f]/g, '');
        if (!text) return null;
        if (text.endsWith('%')) {
            if (unit !== 'percent') throw new Error('Для значений со знаком % выберите «Доли, %».');
            text = text.slice(0, -1);
        }
        if (!/^\+?(?:\d+(?:[.,]\d{0,6})?|[.,]\d{1,6})$/.test(text)) throw new Error('Введите неотрицательное число, до 6 знаков после запятой.');
        const parts = text.replace('+', '').replace(',', '.').split('.');
        const value = BigInt(parts[0] || '0') * scale + BigInt((parts[1] || '').padEnd(6, '0'));
        if (value > (unit === 'percent' ? hundred : 999999999999n * scale)) throw new Error(unit === 'percent' ? 'Доля должна быть от 0 до 100%.' : 'Количество слишком велико.');
        return value;
    }
    function format(value, separator) {
        const tail = String(value % scale).padStart(6, '0').replace(/0+$/, '');
        return String(value / scale) + (tail ? separator + tail : '');
    }
    const rows = Array.from(form.querySelectorAll('[data-grid-row]'), function (element) {
        const inputs = Array.from(element.querySelectorAll('[data-month]'));
        const row = {element, inputs, mode:element.dataset.mode, unit:element.querySelector('[data-month-unit]'),
            dirty:false, busy:false, token:'', requestError:'', requestId:0};
        row.original = {values:inputs.map(input => input.value), mode:row.mode, unit:row.unit.value,
            rule:element.querySelector('[data-row-rule]').textContent,
            status:element.querySelector('[data-row-status]').textContent,
            statusKey:element.dataset.distributionStatus, error:element.querySelector('[data-row-error]').textContent};
        return row;
    });
    function validate(row) {
        let sum = 0n, missing = false;
        for (const input of row.inputs) {
            const value = parse(input.value, row.unit.value);
            if (value === null) missing = true; else sum += value;
        }
        const target = row.unit.value === 'percent' ? hundred : parse(row.element.dataset.quantity, 'quantity');
        let error = '';
        if (missing) error = 'Заполните все месяцы, в том числе явные нули.';
        else if (target === null) error = 'Сначала исправьте плановый объём.';
        else if (sum !== target) error = row.unit.value === 'percent' ? 'Сумма должна быть ровно 100%.' : 'Сумма количеств должна совпадать с планом.';
        if (row.mode === 'fact' && row.dirty && !row.token) error = 'Получите распределение из факта заново.';
        return {sum, error};
    }
    function refresh(row) {
        const badge = row.element.querySelector('[data-row-status]');
        let result;
        try { result = validate(row); } catch (error) { result = {sum:null, error:error.message}; }
        const error = row.requestError || result.error;
        row.valid = !error;
        row.element.querySelector('[data-row-sum]').textContent = result.sum === null ? '—' : format(result.sum, ',') + (row.unit.value === 'percent' ? '%' : '');
        row.element.querySelector('[data-row-unit]').textContent = row.unit.value === 'percent' ? 'Доли, %' : 'Компл.';
        if (row.busy) { badge.textContent = 'Загрузка факта…'; badge.dataset.status = 'draft'; }
        else if (row.dirty) {
            badge.textContent = error ? 'Не сохранено · ошибка' : 'Не сохранено';
            badge.dataset.status = error ? 'invalid' : 'draft';
        } else { badge.textContent = row.original.status; badge.dataset.status = row.original.statusKey; }
        row.element.querySelector('[data-row-error]').textContent = row.dirty || row.requestError ? error : row.original.error;
        row.element.querySelectorAll('[data-mode-button]').forEach(function (button) {
            button.setAttribute('aria-pressed', String(button.dataset.modeButton === row.mode));
            button.disabled = !draft || row.busy || submitting;
        });
        row.unit.disabled = !draft || row.mode !== 'manual' || row.busy || submitting;
        row.unit.closest('label').hidden = !draft || row.mode !== 'manual';
        row.inputs.forEach(function (input) {
            input.readOnly = !draft || row.mode !== 'manual' || row.busy || submitting;
            input.setAttribute('aria-invalid', String(row.dirty && !row.valid));
        });
    }
    function toolbar() {
        const changed = rows.filter(row => row.dirty), bad = changed.filter(row => !row.valid);
        const busy = rows.some(row => row.busy) || autoPending > 0;
        if (save) save.disabled = submitting || busy || !changed.length || !!bad.length;
        message.textContent = submitting ? 'Сохранение…' : busy ? 'Получаем предложения из факта…' :
            changed.length ? 'Изменено строк: ' + changed.length + (bad.length ? '. Исправьте ошибки: ' + bad.length + '.' : '. Суммы проверены; сохраните изменения.') : 'Показаны сохранённые значения.';
    }
    function manual(row) {
        row.mode = 'manual'; row.token = ''; row.requestError = ''; row.dirty = true;
        row.element.querySelector('[data-row-rule]').textContent = 'Вручную · не сохранено';
    }
    async function request(parameters, signal) {
        const response = await fetch(window.location.href, {method:'POST',credentials:'same-origin',
            headers:{'X-CSRFToken':csrf},signal,body:new URLSearchParams({csrfmiddlewaretoken:csrf,revision,...parameters})});
        let data;
        try { data = await response.json(); } catch (_) { throw new Error('Не удалось обработать ответ сервера. Проверьте соединение или обновите страницу.'); }
        if (!response.ok || !data.ok) throw new Error(data.error || 'Не удалось выполнить действие.');
        return data;
    }
    async function fact(row) {
        const id = ++row.requestId, oldMode = row.mode;
        row.controller = new AbortController();
        row.mode = 'fact'; row.busy = true; row.requestError = ''; refresh(row); toolbar();
        try {
            const data = await request({action:'preview_month_grid',group:row.element.dataset.gid,channel:row.element.dataset.cid},row.controller.signal);
            if (id !== row.requestId) return;
            if (row.inputs.some(input => !Object.prototype.hasOwnProperty.call(data.values, input.dataset.month))) throw new Error('В ответе отсутствуют плановые месяцы.');
            row.unit.value = 'percent';
            row.inputs.forEach(input => { input.value = data.values[input.dataset.month].replace('.', ','); });
            row.token = data.token; row.dirty = true;
            row.element.querySelector('[data-row-rule]').textContent = [data.basis.rule_label,data.basis.rule_detail,'Не сохранено'].filter(Boolean).join(' · ');
        } catch (error) {
            if (id !== row.requestId) return;
            row.mode = oldMode; row.requestError = error.message;
        } finally {
            if (id === row.requestId) { row.busy = false; refresh(row); toolbar(); }
        }
    }
    rows.forEach(function (row) {
        row.element.querySelectorAll('[data-mode-button]').forEach(function (button) {
            button.addEventListener('click', function () {
                if (!draft || row.busy || submitting) return;
                const mode = button.dataset.modeButton;
                if (mode === 'fact') { fact(row); return; }
                if (mode === 'manual') manual(row);
                else {
                    row.mode = 'equal'; row.unit.value = 'percent'; row.token = ''; row.dirty = true; row.requestError = '';
                    const share = hundred / BigInt(row.inputs.length);
                    row.inputs.forEach((input,index) => { input.value = format(index === row.inputs.length-1 ? hundred-share*BigInt(index) : share, ','); });
                    row.element.querySelector('[data-row-rule]').textContent = 'Равномерно · не сохранено';
                }
                refresh(row); toolbar();
            });
        });
        row.unit.addEventListener('change', function () { manual(row); refresh(row); toolbar(); });
        row.inputs.forEach(function (input) {
            input.addEventListener('input', function () { if (draft) { manual(row); refresh(row); toolbar(); } });
            input.addEventListener('paste', function (event) {
                if (!draft || submitting) return;
                event.preventDefault();
                const text = event.clipboardData && event.clipboardData.getData('text/plain');
                if (!text) return;
                try {
                    const matrix = text.replace(/\r\n?/g, '\n').replace(/\n+$/, '').split('\n').map(line => line.split('\t'));
                    const r0 = rows.indexOf(row), c0 = row.inputs.indexOf(input), width = matrix[0].length;
                    if (r0+matrix.length > rows.length || c0+width > row.inputs.length || matrix.some(line => line.length !== width)) throw new Error('Блок Excel выходит за границы видимых строк или месяцев либо имеет разную ширину.');
                    const prepared = matrix.map(function (line,index) {
                        const target = rows[r0+index];
                        if (target.busy) throw new Error('Дождитесь загрузки факта перед вставкой.');
                        return line.map(value => parse(value.replace(/^"(.*)"$/, '$1'),target.unit.value));
                    });
                    prepared.forEach(function (line,index) {
                        const target = rows[r0+index]; manual(target);
                        line.forEach((value,col) => { target.inputs[c0+col].value = value === null ? '' : format(value, ','); });
                        target.element.querySelector('[data-row-rule]').textContent = 'Вручную · вставка из Excel · не сохранено';
                        refresh(target);
                    });
                    toolbar();
                } catch (error) { message.textContent = 'Вставка не выполнена: ' + error.message; }
            });
        });
        refresh(row);
    });
    form.addEventListener('submit', async function (event) {
        event.preventDefault();
        if (!draft || submitting || rows.some(row => row.busy) || autoPending) return;
        rows.forEach(refresh);
        const changed = rows.filter(row => row.dirty);
        if (!changed.length || changed.some(row => !row.valid)) { toolbar(); return; }
        const payload = changed.map(row => ({gid:row.element.dataset.gid,cid:row.element.dataset.cid,mode:row.mode,unit:row.unit.value,token:row.token,
            values:Object.fromEntries(row.inputs.map(input => [input.dataset.month,format(parse(input.value,row.unit.value),'.')]))}));
        submitting = true; rows.forEach(refresh); toolbar();
        try {
            await request({action:'save_month_grid',grid_payload:JSON.stringify(payload)});
            window.location.reload();
        } catch (error) {
            submitting = false; rows.forEach(refresh); toolbar();
            message.textContent = 'Изменения не сохранены: ' + error.message;
        }
    });
    const reset = form.querySelector('[data-grid-reset]');
    window.addEventListener('beforeunload', function (event) {
        if (!submitting && rows.some(row => row.dirty && row.mode === 'manual')) {
            event.preventDefault(); event.returnValue = '';
        }
    });
    if (reset) reset.addEventListener('click', function () {
        if (submitting) return;
        stopAuto = true;
        rows.forEach(function (row) {
            if (row.controller) row.controller.abort();
            row.requestId++; row.busy = false; row.dirty = false; row.requestError = ''; row.token = '';
            row.mode = row.original.mode; row.unit.value = row.original.unit;
            row.inputs.forEach((input,index) => { input.value = row.original.values[index]; });
            row.element.querySelector('[data-row-rule]').textContent = row.original.rule;
            refresh(row);
        });
        toolbar();
    });
    if (draft) {
        const defaults = rows.filter(row => row.element.dataset.autoFact === '1');
        autoPending = defaults.length;
        let queue = Promise.resolve();
        defaults.forEach(row => { queue = queue.then(async function () {
            try { if (!stopAuto && row.mode === 'fact' && !row.dirty) await fact(row); }
            finally { autoPending--; toolbar(); }
        }); });
    }
    toolbar();
}());
