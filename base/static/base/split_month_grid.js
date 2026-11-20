(function () {
    const form = document.querySelector('[data-month-grid]');
    if (!form) return;
    const draft = form.dataset.draft === '1';
    const powers = form.dataset.kind === 'powers';
    const previewAction = powers ? 'preview_power_grid' : 'preview_month_grid';
    const saveAction = powers ? 'save_power_grid' : 'save_month_grid';
    const columns = Array.from(form.querySelectorAll('thead [data-grid-column]'), cell => cell.dataset.gridColumn);
    const scale = 1000000n, hundred = 100n * scale;
    const message = form.querySelector('[data-grid-message]');
    const save = form.querySelector('[data-grid-save]');
    const csrf = form.querySelector('[name=csrfmiddlewaretoken]').value;
    const revision = form.querySelector('[name=revision]').value;
    let submitting = false, autoPending = 0, stopAuto = false, bulkBusy = false;

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
        if(value===null)return '';
        if(value<0n)return '-'+format(-value,separator);
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
        row.params = Array.from(element.querySelectorAll('[data-power-param]'));
        row.original.params = row.params.map(input => input.value);
        row.stockToken='';row.original.stockSource=element.querySelector('[data-power-stock-source]')?.textContent||'';
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
        if (!row.inputs.length) error = 'Нет активных колонок для распределения.';
        else if (missing) error = powers ? 'Заполните все активные мощности, в том числе явные нули.' : 'Заполните все месяцы, в том числе явные нули.';
        else if (target === null) error = 'Сначала исправьте плановый объём.';
        else if (sum !== target) error = row.unit.value === 'percent' ? 'Сумма должна быть ровно 100%.' : 'Сумма количеств должна совпадать с планом.';
        if (row.mode === 'fact' && row.dirty && !row.token && !(powers && row.original.mode === 'fact' && row.element.dataset.existingFact === '1')) error = 'Получите распределение из факта заново.';
        for (const input of row.params) {
            if (!['factor','cost'].includes(input.dataset.powerParam)) continue;
            const value = parse(input.value,input.dataset.powerParam === 'cost' ? 'percent' : 'quantity');
            if (input.dataset.powerParam === 'factor' && value === 0n) error = 'Соотношение DDP должно быть положительным.';
        }
        if(powers)for(const cell of row.element.querySelectorAll('[data-power-config]')){
            const share=cell.querySelector('[data-month]')||cell.parentElement.querySelector('[data-month]');
            if(share&&parse(share.value,'percent')>0n){
                if([...cell.querySelectorAll('select[data-power-param]')].some(input=>!input.value))error='Не выбрана пара моделей';
                else if([...cell.querySelectorAll('input[data-power-param]')].some(input=>!input.value))error='Заполните параметры стоимости мощности';
            }
        }
        return {sum, error};
    }
    function refresh(row) {
        const badge = row.element.querySelector('[data-row-status]');
        let result;
        try { result = validate(row); } catch (error) { result = {sum:null, error:error.message}; }
        const error = row.requestError || result.error;
        const jump=row.element.querySelector('[data-row-jump]');if(jump){jump.hidden=!error;jump.textContent=error||'К ошибке';}
        try{const qty=parse(row.element.dataset.quantity,'quantity');const allocated=qty===null||result.sum===null?null:row.unit.value==='percent'?qty*result.sum/hundred:result.sum;row.element.querySelector('[data-row-allocated]').textContent=format(allocated,',')||'—';row.element.querySelector('[data-row-remaining]').textContent=qty===null||allocated===null?'—':format(qty-allocated,',');}catch{row.element.querySelector('[data-row-allocated]').textContent='—';row.element.querySelector('[data-row-remaining]').textContent='—';}
        row.valid = !error;
        row.element.querySelector('[data-row-sum]').textContent = result.sum === null ? '—' : format(result.sum, ',') + (row.unit.value === 'percent' ? '%' : '');
        row.element.querySelector('[data-row-unit]').textContent = row.unit.value === 'percent' ? 'Доли, %' : 'Компл.';
        if (row.busy) { badge.textContent = row.stockBusy?'Загрузка DDP стока…':'Загрузка факта…'; badge.dataset.status = 'draft'; }
        else if (row.dirty) {
            badge.textContent = error ? 'Не сохранено · ошибка' : 'Не сохранено';
            badge.dataset.status = error ? 'invalid' : 'draft';
        } else { badge.textContent = row.original.status; badge.dataset.status = row.original.statusKey; }
        row.element.querySelector('[data-row-error]').textContent = row.dirty || row.requestError ? error : row.original.error;
        row.element.querySelectorAll('[data-mode-button]').forEach(function (button) {
            button.setAttribute('aria-pressed', String(button.dataset.modeButton === row.mode));
            button.disabled = !draft || row.busy || submitting;
        });
        const modeSelect=row.element.querySelector('[data-row-mode]');
        if(modeSelect){modeSelect.value=row.mode;modeSelect.disabled=!draft||row.busy||submitting;}
        const recalculate=row.element.querySelector('[data-row-recalculate]');
        if(recalculate){recalculate.hidden=row.mode!=='fact';recalculate.disabled=!draft||row.busy||submitting;}
        const stockButton=row.element.querySelector('[data-power-stock-row]');if(stockButton)stockButton.disabled=!draft||row.busy||submitting||bulkBusy;
        badge.title=badge.textContent;
        const info=row.element.querySelector('[data-row-info]');
        if(info && !row.busy && (row.requestError || row.dirty&&!row.valid))info.open=true;
        row.unit.disabled = !draft || row.mode !== 'manual' || row.busy || submitting;
        row.unit.closest('label').hidden = powers || !draft || row.mode !== 'manual';
        row.lastUnit=row.unit.value;
        row.params.forEach(function (input) {
            if (input.tagName === 'SELECT') input.disabled = !draft || row.busy || submitting;
            else input.readOnly = !draft || row.busy || submitting;
        });
        row.inputs.forEach(function (input) {
            input.readOnly = !draft || row.mode !== 'manual' || row.busy || submitting;
            input.setAttribute('aria-invalid', String(row.dirty && !row.valid));
        });
    }
    function canSaveRow(row){
        try{row.inputs.forEach(input=>parse(input.value,row.unit.value));row.params.filter(input=>['factor','cost'].includes(input.dataset.powerParam)).forEach(input=>parse(input.value,input.dataset.powerParam==='cost'?'percent':'quantity'));if(row.mode==='manual')return true;if(row.mode==='fact')return !!row.token||(powers&&row.original.mode==='fact'&&row.element.dataset.existingFact==='1');return row.inputs.reduce((sum,input)=>sum+(parse(input.value,'percent')||0n),0n)===hundred;}catch{return false;}
    }
    function toolbar() {
        window.SplitUnsaved?.refresh();
        const changed = rows.filter(row => row.dirty), bad = changed.filter(row => !row.valid);
        const busy = rows.some(row => row.busy) || autoPending > 0 || bulkBusy;
        bulkToolbar(busy);
        const globalUnit=form.querySelector('[data-grid-global-unit]');if(globalUnit){const units=[...new Set(rows.filter(visible).map(row=>row.unit.value))];if(units.length>1&&!globalUnit.querySelector('[value=mixed]')){const option=new Option('Смешанные единицы','mixed');option.disabled=true;globalUnit.append(option);}globalUnit.value=units.length===1?units[0]:'mixed';}
        if (save) save.disabled = submitting || busy || !changed.length || changed.some(row=>!canSaveRow(row));
        message.textContent = submitting ? 'Сохранение…' : busy ? 'Получаем предложения из факта…' :
            changed.length ? 'Изменено строк: ' + changed.length + (bad.length ? '. В строках есть ошибки: ' + bad.length + '. Черновик можно сохранить; утверждение потребует исправлений.' : '. Суммы проверены; сохраните изменения.') : 'Показаны сохранённые значения.';
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
        const progress = !bulkBusy && !autoPending ? window.SplitProgress?.start('Получение распределения из факта: ' + row.element.querySelector('.split-month-group-name').textContent) : null;
        row.progress=progress;
        row.controller = new AbortController();
        row.mode = 'fact'; row.busy = true; row.requestError = ''; refresh(row); toolbar();
        try {
            const data = await request({action:previewAction,group:row.element.dataset.gid,channel:row.element.dataset.cid},row.controller.signal);
            if (id !== row.requestId) return;
            if (row.inputs.some(input => !Object.prototype.hasOwnProperty.call(data.values, input.dataset.month))) throw new Error('В ответе отсутствуют колонки распределения.');
            row.unit.value = 'percent';
            row.inputs.forEach(input => { input.value = data.values[input.dataset.month].replace('.', ','); });
            if (powers && data.parameters) {
                row.params.forEach(input => {
                    if (input.value.trim()) return;
                    const key = input.closest('[data-power-config]').dataset.powerConfig;
                    const value = data.parameters[key]?.[input.dataset.powerParam];
                    if (value === null || value === undefined || value === '') return;
                    if (input.tagName === 'SELECT') {
                        if (Array.from(input.options).some(option => option.value === String(value))) input.value = String(value);
                    } else input.value = String(value).replace('.', ',');
                });
            }
            row.token = data.token; row.dirty = true;
            progress?.finish('Предложение получено. Проверьте и сохраните изменения.');
            row.element.querySelector('[data-row-rule]').textContent = [data.basis.rule_label,data.basis.rule_detail,data.basis.first_year&&data.basis.last_year?data.basis.first_year+'–'+data.basis.last_year:'',data.basis.through?'По '+data.basis.through:'',data.basis.parameter_note,'Не сохранено'].filter(Boolean).join(' · ');
        } catch (error) {
            if (id !== row.requestId) return;
            row.mode = oldMode; row.requestError = error.message;
            progress?.fail(error.message);
        } finally {
            if (id === row.requestId) { row.busy = false; refresh(row); toolbar(); }
        }
    }
    function powerParameters(row){
        return Object.fromEntries([...row.element.querySelectorAll('[data-power-config]')].map(cell=>[cell.dataset.powerConfig,
          Object.fromEntries([...cell.querySelectorAll('[data-power-param]')].map(input=>{
            const field=input.dataset.powerParam;
            return [field,['factor','cost'].includes(field)?format(parse(input.value,field==='cost'?'percent':'quantity'),'.'):input.value];
          }))]));
    }
    async function stockParameters(selected){
        if(!draft||!powers||submitting||bulkBusy||autoPending||rows.some(row=>row.busy)||!selected.length)return;
        let payload;
        try{payload=selected.map(row=>({gid:row.element.dataset.gid,parameters:powerParameters(row)}));}
        catch(error){message.textContent=error.message;return;}
        bulkBusy=true;selected.forEach(row=>{row.busy=true;row.stockBusy=true;row.requestError='';refresh(row);});toolbar();
        const progress=window.SplitProgress?.start('DDP мощностей из остатков');
        try{
            const data=await request({action:'preview_power_stock_parameters',rows:JSON.stringify(payload),only_empty:form.querySelector('[data-power-stock-fill]').value});
            selected.forEach(row=>{
                const result=data.rows.find(item=>item.gid===row.element.dataset.gid);
                if(!result)throw Error('Нет результата для группы.');
                row.params.forEach(input=>{const key=input.closest('[data-power-config]').dataset.powerConfig;const value=result.parameters[key][input.dataset.powerParam];input.value=value===null?'':String(value);});
                row.stockToken=result.token;row.dirty=true;
                row.element.querySelector('[data-power-stock-source]').textContent=[result.source.label,...result.source.issues,'Не сохранено'].join(' · ');
            });
            progress?.finish('Параметры получены. Проверьте и сохраните изменения.');
        }catch(error){selected.forEach(row=>row.requestError=error.message);progress?.fail(error.message);}
        finally{bulkBusy=false;selected.forEach(row=>{row.busy=false;row.stockBusy=false;refresh(row);});toolbar();}
    }
    form.querySelector('[data-power-stock-all]')?.addEventListener('click',()=>stockParameters(form.querySelector('[data-grid-scope]').value==='selected'?chosen():rows.filter(visible)));
    rows.forEach(row=>row.element.querySelector('[data-power-stock-row]')?.addEventListener('click',()=>stockParameters([row])));
    function visible(row) { return !row.element.hidden && !row.element.hasAttribute('data-reference-excluded') && !row.element.hasAttribute('data-view-excluded'); }
    function chosen() { return rows.filter(row => visible(row) && row.element.querySelector('[data-grid-select]')?.checked); }
    function bulkToolbar(busy) {
        const count = form.querySelector('[data-grid-selected-count]');
        if (!count) return;
        count.textContent = 'Выбрано строк: ' + chosen().length;
        const selection=form.querySelector('[data-grid-selection-toolbar]');if(selection)selection.hidden=!chosen().length;
        form.querySelectorAll('[data-grid-select], [data-grid-select-all], [data-grid-select-none], [data-grid-bulk-mode]').forEach(control => { control.disabled = !draft || busy || submitting; });
        const stockAll=form.querySelector('[data-power-stock-all]');if(stockAll)stockAll.disabled=!draft||busy||submitting||!rows.some(visible);
        form.querySelector('[data-grid-bulk-apply]').disabled = !draft || busy || submitting || !rows.some(visible);
    }
    form.querySelector('[data-grid-select-all]')?.addEventListener('click', () => {
        rows.filter(visible).forEach(row => { row.element.querySelector('[data-grid-select]').checked = true; }); toolbar();
    });
    form.querySelector('[data-grid-select-none]')?.addEventListener('click', () => {
        rows.forEach(row => { row.element.querySelector('[data-grid-select]').checked = false; }); toolbar();
    });
    form.querySelectorAll('[data-grid-select]').forEach(control => control.addEventListener('change',()=>{const scope=form.querySelector('[data-grid-scope]');if(scope)scope.value=chosen().length?'selected':'all';if(chosen().length)form.querySelector('.split-distribution-bulk').open=true;toolbar();}));
    document.addEventListener('split-reference-filter-change', toolbar);
    document.addEventListener('split-table-visibility-change', toolbar);
    form.querySelector('[data-grid-bulk-apply]')?.addEventListener('click', async () => {
        if (!draft || submitting || bulkBusy || autoPending || rows.some(row => row.busy)) return;
        const scope=form.querySelector('[data-grid-scope]')?.value||'selected';
        const onlyEmpty=form.querySelector('[data-grid-only-empty]')?.checked;
        const selected=(scope==='selected'?chosen():rows.filter(row=>visible(row))).filter(row=>!onlyEmpty||row.inputs.every(input=>!input.value.trim())), mode = form.querySelector('[data-grid-bulk-mode]').value;
        if (!selected.length){form.querySelector('[data-grid-bulk-result]').textContent='Нет подходящих строк: заполненные и частичные значения сохранены. Для их замены снимите «Только незаполненные». Выбор не переносится на другие страницы.';return;}
        const progress=window.SplitProgress?.start('Массовое распределение',selected.length);
        let completed=0;
        bulkBusy = true; toolbar();
        form.querySelector('[data-grid-bulk-result]').textContent = 'Применяем распределение…';
        try {
            for (const row of selected) {
                if (mode === 'fact') await fact(row);
                else row.element.querySelector('[data-mode-button="' + mode + '"]').click();
                progress?.update(++completed);
                if(mode!=='fact' && completed%20===0)await new Promise(resolve=>setTimeout(resolve,0));
            }
        } finally {
            bulkBusy = false; toolbar();
            const failures = selected.filter(row => row.requestError).length;
            if(failures)progress?.fail('Обработано '+completed+' из '+selected.length+'. Ошибок: '+failures+'. Проверьте сообщения в строках.');
            else progress?.finish('Обработано '+completed+' из '+selected.length+'. Проверьте результат и сохраните изменения.');
            form.querySelector('[data-grid-bulk-result]').textContent = 'Обработано строк: ' + selected.length + (failures ? '. Не удалось обработать: ' + failures + '. Проверьте сообщения в строках.' : '. Проверьте распределение и сохраните изменения.');
        }
    });
    rows.forEach(function (row) {
        row.element.querySelectorAll('[data-mode-button]').forEach(function (button) {
            button.addEventListener('click', function () {
                if (!draft || row.busy || submitting) return;
                const mode = button.dataset.modeButton;
                if (!row.inputs.length) { row.requestError = 'Нет активных мощностей. Обновите состав группы.'; refresh(row); toolbar(); return; }
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
        row.element.querySelector('[data-row-mode]')?.addEventListener('change',event=>{row.element.querySelector('[data-mode-button="'+event.target.value+'"]').click();});
        row.element.querySelector('[data-row-recalculate]')?.addEventListener('click',()=>row.element.querySelector('[data-mode-button="fact"]').click());
        const info=row.element.querySelector('[data-row-info]'),infoToggle=row.element.querySelector('[data-row-info-toggle]');
        if(info&&infoToggle){infoToggle.addEventListener('click',()=>{info.open=!info.open;});info.addEventListener('toggle',()=>infoToggle.setAttribute('aria-expanded',String(info.open)));}
        row.unit.addEventListener('change', function () { convertUnit(row,row.unit.value); toolbar(); });
        row.params.forEach(function (input) {
            input.addEventListener(input.tagName === 'SELECT' ? 'change' : 'input', function () {
                row.dirty = true; row.requestError = ''; refresh(row); toolbar();
            });
        });
        row.inputs.forEach(function (input) {
            input.addEventListener('input', function () { if (draft) { manual(row); refresh(row); toolbar(); } });
            input.addEventListener('paste', function (event) {
                if (!draft || submitting) return;
                event.preventDefault();
                const text = event.clipboardData && event.clipboardData.getData('text/plain');
                if (!text) return;
                try {
                    const matrix = text.replace(/\r\n?/g, '\n').replace(/\n+$/, '').split('\n').map(line => line.split('\t'));
                    const pasteRows = Array.from(form.querySelectorAll('[data-grid-row]'), element => rows.find(item => item.element === element)).filter(visible);
                    const r0 = pasteRows.indexOf(row), c0 = columns.indexOf(input.dataset.month), width = matrix[0].length;
                    if (r0 < 0 || r0+matrix.length > pasteRows.length || c0 < 0 || c0+width > columns.length || matrix.some(line => line.length !== width)) throw new Error('Блок Excel выходит за границы видимых строк или колонок либо имеет разную ширину.');
                    const prepared = matrix.map(function (line,index) {
                        const target = pasteRows[r0+index];
                        if (target.busy) throw new Error('Дождитесь загрузки факта перед вставкой.');
                        return line.map(function (value,col) {
                            const cell = target.inputs.find(item => item.dataset.month === columns[c0+col]);
                            if (!cell) throw new Error('В одной из строк нет этой активной мощности. Разделите блок Excel.');
                            return {input:cell,value:parse(value.replace(/^"(.*)"$/, '$1'),target.unit.value)};
                        });
                    });
                    prepared.forEach(function (line,index) {
                        const target = pasteRows[r0+index]; manual(target);
                        line.forEach(cell => { cell.input.value = cell.value === null ? '' : format(cell.value, ','); });
                        target.element.querySelector('[data-row-rule]').textContent = 'Вручную · вставка из Excel · не сохранено';
                        refresh(target);
                    });
                    toolbar();
                } catch (error) { message.textContent = 'Вставка не выполнена: ' + error.message; }
            });
        });
        refresh(row);
    });
    async function saveGrid(next=null) {
        if (!draft || submitting || bulkBusy || rows.some(row => row.busy) || autoPending) return;
        rows.forEach(refresh);
        const changed = rows.filter(row => row.dirty);
        if (!changed.length || changed.some(row => !canSaveRow(row))) { toolbar(); return; }
        const payload = changed.map(function (row) {
            const item = {gid:row.element.dataset.gid,cid:row.element.dataset.cid,mode:row.mode,unit:row.unit.value,token:row.token,
                stock_token:row.stockToken,values:Object.fromEntries(row.inputs.map(input => [input.dataset.month,format(parse(input.value,row.unit.value),'.')]))};
            if (powers) item.parameters = Object.fromEntries(Array.from(row.element.querySelectorAll('[data-power-config]'), function (cell) {
                return [cell.dataset.powerConfig,Object.fromEntries(Array.from(cell.querySelectorAll('[data-power-param]'), function (input) {
                    let value = input.value;
                    if (['factor','cost'].includes(input.dataset.powerParam)) {
                        const parsed = parse(value,input.dataset.powerParam === 'cost' ? 'percent' : 'quantity');
                        value = parsed === null ? null : format(parsed,'.');
                    }
                    return [input.dataset.powerParam,value];
                }))];
            }));
            return item;
        });
        const progress=window.SplitProgress?.start('Сохранение распределения · строк: '+changed.length);
        submitting = true; rows.forEach(refresh); toolbar();
        try {
            await request({action:saveAction,save_draft:'1',grid_payload:JSON.stringify(payload)});
            progress?.finish('Распределение сохранено');
            window.SplitUnsaved ? window.SplitUnsaved.navigate(next) : window.location.reload();
        } catch (error) {
            submitting = false; rows.forEach(refresh); toolbar();
            message.textContent = 'Изменения не сохранены: ' + error.message;
            progress?.fail(error.message);
            throw error;
        }
    }
    form.addEventListener('submit',event=>{event.preventDefault();saveGrid().catch(()=>{});});
    const reset = form.querySelector('[data-grid-reset]');
    if (reset) reset.addEventListener('click', function () {
        if (submitting || bulkBusy) return;
        stopAuto = true;
        rows.forEach(function (row) {
            if (row.controller) row.controller.abort();
            row.progress?.finish('Операция отменена.');
            row.requestId++; row.busy = false; row.dirty = false; row.requestError = ''; row.token = '';row.stockToken='';
            const source=row.element.querySelector('[data-power-stock-source]');if(source)source.textContent=row.original.stockSource;
            row.mode = row.original.mode; row.unit.value = row.original.unit;
            row.inputs.forEach((input,index) => { input.value = row.original.values[index]; });
            row.params.forEach((input,index) => { input.value = row.original.params[index]; });
            row.element.querySelector('[data-row-rule]').textContent = row.original.rule;
            refresh(row);
        });
        toolbar();
    });
    if(draft)window.SplitUnsaved?.register({dirty:()=>rows.some(row=>row.dirty),busy:()=>submitting||bulkBusy||autoPending>0||rows.some(row=>row.busy),canSave:()=>rows.filter(row=>row.dirty).every(canSaveRow),save:saveGrid,discard(){reset?.click();}});
    if (draft && form.dataset.autoLoad==='1') {
        const defaults = rows.filter(row => row.element.dataset.autoFact === '1');
        autoPending = defaults.length;
        const autoProgress=defaults.length ? window.SplitProgress?.start('Автоматическое получение распределения',defaults.length) : null;
        let autoCompleted=0;
        let queue = Promise.resolve();
        defaults.forEach(row => { queue = queue.then(async function () {
            try { if (!stopAuto && row.mode === 'fact' && !row.dirty) await fact(row); }
            finally {autoPending--;autoProgress?.update(++autoCompleted);toolbar();if(!autoPending){const failures=defaults.filter(row=>row.requestError).length;if(failures)autoProgress?.fail('Не удалось получить предложение для строк: '+failures);else autoProgress?.finish(stopAuto ? 'Автоматическая загрузка остановлена.' : 'Предложения получены. Проверьте распределение и сохраните изменения.');}}
        }); });
    }
    function convertUnit(row,unit){
        const previous=row.lastUnit||row.original.unit;if(previous===unit)return;
        try{
            const qty=parse(row.element.dataset.quantity,'quantity');if(!qty)throw Error('Для пересчёта единиц нужен положительный план.');
            const values=row.inputs.map(input=>parse(input.value,previous));
            const converted=values.map(value=>value===null?null:previous==='percent'?value*qty/hundred:value*hundred/qty);
            if(values.every(value=>value!==null)&&values.reduce((a,b)=>a+b,0n)===(previous==='percent'?hundred:qty)){
                converted[converted.length-1]+=(unit==='quantity'?qty:hundred)-converted.reduce((a,b)=>a+b,0n);
            }
            row.inputs.forEach((input,index)=>input.value=converted[index]===null?'':format(converted[index],','));
            manual(row);row.unit.value=unit;refresh(row);
        }catch(error){row.unit.value=previous;message.textContent=error.message;}
    }
    form.querySelector('[data-grid-global-unit]')?.addEventListener('change',event=>{
        if(!draft)return;const unit=event.target.value;for(const row of rows.filter(visible))convertUnit(row,unit);toolbar();
    });
    for(const row of rows)row.element.querySelector('[data-row-jump]')?.addEventListener('click',()=>{
        const missingParam=row.params.find(input=>!input.value||(input.dataset.powerParam==='factor'&&Number(input.value.replace(',','.'))<=0));const target=missingParam||row.inputs.find(input=>!input.value.trim())||row.inputs.at(-1);const details=target?.closest('details');if(details)details.open=true;target?.scrollIntoView({block:'center',inline:'center'});target?.focus();
    });
    toolbar();
}());
