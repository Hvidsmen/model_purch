(() => {
    const initialize = () => {
        document.querySelectorAll('form[data-ux-submit]').forEach(form => {
            let pending = false;
            const reset = () => { pending = false; form.removeAttribute('aria-busy'); form.querySelector('.ux-submit-status')?.remove(); };
            window.addEventListener('pageshow', reset);
            form.addEventListener('submit', event => {
                if (event.defaultPrevented) return;
                if (pending) { event.preventDefault(); return; }
                pending = true;
                form.setAttribute('aria-busy', 'true');
                const status = document.createElement('p');
                status.className = 'ux-submit-status'; status.setAttribute('role', 'status');
                status.textContent = form.dataset.uxSubmit;
                form.append(status);
            });
        });
        const navigation = document.getElementById('subdivision-nav');
        if (navigation) {
            const search = document.getElementById('subdivision-search');
            const links = [...document.querySelectorAll('#subdivision-links li:has(a)')];
            const fold = value => value.toLocaleLowerCase('ru-RU').replaceAll('ё', 'е');
            search.addEventListener('input', () => {
                const query = fold(search.value.trim());
                links.forEach(link => link.hidden = !fold(link.textContent).includes(query));
                document.getElementById('subdivision-search-empty').hidden = !links.length || links.some(link => !link.hidden);
            });
            navigation.addEventListener('toggle', () => { if (navigation.open) search.focus(); });
            document.addEventListener('click', event => { if (!navigation.contains(event.target)) navigation.open = false; });
            navigation.addEventListener('keydown', event => {
                if (event.key === 'Escape') { navigation.open = false; navigation.querySelector('summary').focus(); }
            });
        }
        const readonly = document.querySelector('.motivation-viewer');
        if (readonly) {
            readonly.querySelectorAll('form[method=post]').forEach(form => {
                form.querySelectorAll('input,select,textarea,button').forEach(control=>control.disabled=true);
            });
        }
        const toolbar = document.getElementById('editor-toolbar');
        if (!toolbar) return;
        const main = document.getElementById(toolbar.dataset.form);
        const forms = [main, document.getElementById('addProductForm'), document.querySelector('#manager-details form')].filter(Boolean);
        const fields = forms.flatMap(form => [...form.querySelectorAll('input:not([type=hidden]):not([type=file]), select')]
            .filter(input => !input.matches(':disabled, .bulk-good') && input.name !== 'selected_subdivisions'));
        const original = new Map(), originalRaw = new Map(), committed = new Map();
        const history = [];
        const raw = field => field.type === 'checkbox' ? field.checked : field.value;
        const put = (field, value) => { if (field.type === 'checkbox') { field.checked = value; field.dispatchEvent(new Event('change', {bubbles:true})); } else {
            const text=String(value), number=Number(text.replace(',','.'));
            field.value=field.classList.contains('coeff-input') && text.trim() && !text.includes('%') && Number.isFinite(number) && Math.abs(number)<=1 ? ((number*100).toFixed(12).replace(/\.?0+$/,'')||'0')+'%' : value;
        } };
        const equal = (a,b) => typeof a === 'number' && typeof b === 'number' ? Math.abs(a-b) <= 1e-14 : a === b;
        const normalize = field => {
            if (field.type === 'checkbox') return field.checked;
            if (field.classList.contains('coeff-input')) {
                const value = field.value.trim().replace(',', '.');
                if (!value) return '';
                const number = Number(value.replace(/%$/, '')) / (value.endsWith('%') ? 100 : 1);
                return Number.isFinite(number) ? number : value;
            }
            return field.value;
        };
        fields.forEach(field => { original.set(field, normalize(field)); originalRaw.set(field, raw(field)); committed.set(field, {raw:raw(field), normalized:normalize(field)}); });
        const different = field => {
            const old = original.get(field), value = normalize(field);
            return typeof old === 'number' && typeof value === 'number' ? Math.abs(old - value) > 1e-14 : old !== value;
        };
        const changed = () => fields.filter(different);
        const rows = [...document.querySelectorAll('#coefficient-table .coefficient-row')];
        const search = document.getElementById('coefficient-search');
        const onlyChanged = document.getElementById('coefficient-only-changed');
        const selectors = ['pg', 'group', 'brand'].map(key => [key, document.getElementById(`coefficient-${key}`)]);
        const fold = value => value.toLocaleLowerCase('ru-RU').replaceAll('ё', 'е');
        selectors.forEach(([key, select]) => {
            [...new Set(rows.map(row => row.dataset[key]))].sort((a, b) => a.localeCompare(b, 'ru')).forEach(value => {
                const option = document.createElement('option'); option.value = value; option.textContent = value; select.append(option);
            });
        });
        const filter = () => {
            const query = fold(search.value.trim());
            const matchesFilter = row => fold([row.dataset.pg,row.dataset.group,row.dataset.brand,row.dataset.key].join(' ')).includes(query)
                && !selectors.some(([key,select])=>select.value && select.value!==row.dataset[key])
                && (!onlyChanged.checked || row.querySelector('.editor-changed'));
            rows.forEach(row => { row.dataset.matchesFilter = matchesFilter(row) ? 'yes' : 'no'; row.hidden = !matchesFilter(row) || row.dataset.groupCollapsed === 'yes'; });
            document.querySelectorAll('.coefficient-group-heading').forEach(header => {
                header.hidden=!rows.filter(row=>row.dataset.uxGroup===header.dataset.groupKey).some(matchesFilter);
            });
            const count = rows.filter(row => !row.hidden).length;
            document.getElementById('coefficient-visible-count').textContent = `Показано: ${count} из ${rows.length}`;
            document.getElementById('coefficient-filter-empty').hidden = rows.some(matchesFilter);
        };
        const reset = () => { search.value = ''; onlyChanged.checked = false; selectors.forEach(([, select]) => select.value = ''); filter(); };
        search.addEventListener('input', filter);
        onlyChanged.addEventListener('change', filter);
        selectors.forEach(([, select]) => select.addEventListener('change', filter));
        document.getElementById('coefficient-reset').addEventListener('click', reset);
        document.querySelectorAll('[data-workflow-dirty]').forEach(node => node.addEventListener('click', () => {
            onlyChanged.checked = true;
            filter();
        }));
        const describe = field => {
            const row = field.closest('.coefficient-row');
            const group = field.closest('[data-coefficient-group]')?.dataset.coefficientGroup;
            const groupName = {policies:'Политики',sales:'Продажи',directors:'Директор'}[group];
            const index = field.dataset.segment || ('K' + (field.closest('td')?.dataset.coefficientSegment ?? ''));
            return {goods: row ? [row.dataset.pg,row.dataset.group,row.dataset.brand].join(' · ') : field.closest('#manager-details') ? 'Поправки руководителя' : 'Добавление товара',
                kind: field.name.startsWith('delete_coeff=') ? 'Удаление товара' : field.classList.contains('select-var-calc') ? 'Формула товара' : groupName ? groupName + ' / ' + index : field.getAttribute('aria-label') || field.closest('label')?.textContent.trim().split('\n')[0] || 'Настройка'};
        };
        const display = (field, value, normalized) => {
            if (field.type === 'checkbox') return value ? 'Да' : 'Нет';
            if (field.tagName === 'SELECT') return [...field.options].find(option => option.value === value)?.textContent || value;
            if (field.classList.contains('coeff-input') && typeof normalized === 'number' && Number.isFinite(normalized)) return new Intl.NumberFormat('ru-RU',{maximumFractionDigits:10}).format(normalized*100) + '%';
            return value;
        };
        let changePage = 0;
        const reviewDialog = document.getElementById('editor-changes-dialog');
        const reviewBody = document.getElementById('editor-changes-rows');
        const renderReview = () => {
            if (!reviewBody) return;
            reviewBody.replaceChildren();
            const term = document.getElementById('editor-changes-search').value.trim().toLocaleLowerCase('ru');
            const all = changed().filter(field => Object.values(describe(field)).join(' ').toLocaleLowerCase('ru').includes(term));
            const pages = Math.max(1, Math.ceil(all.length/100)); changePage = Math.min(changePage,pages-1);
            document.getElementById('editor-changes-summary').textContent = `Всего правок: ${changed().length}. По поиску: ${all.length}. Страница ${changePage+1} из ${pages}.`;
            document.getElementById('editor-changes-prev').disabled = changePage === 0;
            document.getElementById('editor-changes-next').disabled = changePage >= pages-1;
            for (const field of all.slice(changePage*100,(changePage+1)*100)) {
                const info = describe(field), tr = document.createElement('tr');
                for (const text of [info.goods, info.kind, display(field,originalRaw.get(field),original.get(field)), display(field,raw(field),normalize(field))]) {
                    const td = document.createElement('td'); td.textContent = text; tr.append(td);
                }
                const td=document.createElement('td'), button=document.createElement('button');
                button.type='button';button.className='btn btn-outline-secondary';button.textContent='Вернуть исходное';
                button.addEventListener('click',()=>{ window.motivationEditor.apply([{input:field,value:originalRaw.get(field)}],'Вернуть исходное значение');renderReview(); });
                td.append(button);tr.append(td);reviewBody.append(tr);
            }
        };
        document.getElementById('editor-review-changes')?.addEventListener('click',()=>{changePage=0;renderReview();reviewDialog.showModal();});
        document.getElementById('editor-changes-search')?.addEventListener('input',()=>{changePage=0;renderReview();});
        document.getElementById('editor-changes-prev')?.addEventListener('click',()=>{changePage--;renderReview();});
        document.getElementById('editor-changes-next')?.addEventListener('click',()=>{changePage++;renderReview();});
        document.getElementById('editor-changes-close')?.addEventListener('click',()=>reviewDialog.close());
        const sourceBadges = [];
        rows.forEach(row => row.querySelectorAll('input[data-rate-source]').forEach(input => {
            const badge=document.createElement('small');badge.className='coefficient-source';input.after(badge);
            if(document.getElementById('subCoeffForm')){
                const global=document.createElement('small');global.className='coefficient-global-reference';
                const value=input.dataset.globalValue;
                global.textContent=value && value !== 'None' ? 'Глобально: '+new Intl.NumberFormat('ru-RU',{maximumFractionDigits:10}).format(Number(value)*100)+'%' : 'Глобально: нет точной ставки';
                badge.after(global);
            }
            sourceBadges.push({input,badge});
        }));
        const refresh = () => {
            fields.forEach(field => field.classList.toggle('editor-changed', different(field)));
            const count = changed().length;
            sourceBadges.forEach(({input,badge})=>{
                const missing=input.dataset.rateExact==='no', dirty=original.has(input)&&different(input);
                badge.textContent=dirty ? 'Изменено'+(missing?' · новая ставка':'') : missing ? 'Нет точной' : input.dataset.rateSource;
                badge.hidden=!document.getElementById('subCoeffForm')&&!dirty&&!missing;
                badge.classList.toggle('coefficient-source-missing',missing&&!dirty);
                badge.title=missing ? 'Точная запись отсутствует. Ноль в редакторе не подтверждает наличие коэффициента. Создайте недостающие записи через проверку покрытия.' : 'Сохранённая точная ставка выбранной версии. Изменения вступят в действие после утверждения, выгрузки и расчёта.';
            });
            document.querySelectorAll('[data-workflow-dirty]').forEach(node => { node.hidden = !count; node.textContent = `Не сохранено: ${count}`; });
            document.getElementById('editor-dirty-count').textContent = `Изменено: ${count}`;
            document.getElementById('editor-save-state').textContent = count ? 'Правки ещё не сохранены' : 'Нет несохранённых правок';
            toolbar.querySelector('.editor-state').classList.toggle('has-changes', count > 0);
            document.getElementById('editor-save').disabled = !changed().some(field => field.form === main);
            document.querySelectorAll('[data-editor-change-total]').forEach(node=>node.textContent=String(count));
            const changedRows = new Set(changed().map(field=>field.closest('.coefficient-row')).filter(Boolean));
            const scope = document.getElementById('editor-change-scope');
            if (scope) scope.textContent = `Товаров: ${changedRows.size} · полей: ${count}`;
            const undo = document.getElementById('editor-undo'), resetChanges = document.getElementById('editor-reset-changes');
            if (undo) undo.disabled = !history.length;
            if (resetChanges) resetChanges.disabled = !count;
            document.dispatchEvent(new CustomEvent('motivation:editor-state',{detail:{canUndo:history.length>0,changed:count}}));
            if (reviewDialog?.open) renderReview();
            filter();
        };
        const notify = items => {
            for(const {input} of items){
                if(input.classList.contains('coeff-input')&&typeof window.applyValidation==='function'){
                    input.dataset.value=String(normalize(input));window.applyValidation(input);
                    const description=input.getAttribute('aria-describedby');
                    if(description?.startsWith('restored-error-')){document.getElementById(description)?.remove();input.removeAttribute('aria-describedby');}
                    if(input.classList.contains('invalid'))input.setAttribute('aria-invalid','true');else input.removeAttribute('aria-invalid');
                }
            }
            refresh();
            items[0]?.input.dispatchEvent(new Event('input',{bubbles:true}));
            document.dispatchEvent(new CustomEvent('motivation:editor-updated'));
        };
        const rememberManual = field => {
            const before=committed.get(field), current=normalize(field);
            if (!equal(before.normalized,current)) {
                history.push({label:'Ручное изменение',changes:[{input:field,before:before.raw,beforeN:before.normalized,after:raw(field),afterN:current}]});
                if(history.length>100)history.shift();
            }
            committed.set(field,{raw:raw(field),normalized:current});refresh();
        };
        window.motivationEditor = {
            apply(items,label) {
                if(items.some(item=>!fields.includes(item.input)||item.input.matches(':disabled')))throw new Error('Выбраны недоступные ячейки. Правки не применены.');
                const changes=items.filter(item=>raw(item.input)!==item.value).map(item=>({input:item.input,before:raw(item.input),beforeN:normalize(item.input),after:item.value}));
                if(!changes.length)return;
                // Suppress manual-history hooks while restoring checkbox/select states.
                applying=true;
                try{changes.forEach(change=>put(change.input,change.after));}finally{applying=false;}
                changes.forEach(change=>{change.afterN=normalize(change.input);committed.set(change.input,{raw:raw(change.input),normalized:change.afterN});});
                history.push({label,changes});if(history.length>100)history.shift();notify(changes);
            },
            undo() {
                const last=history.at(-1);if(!last)return;
                if(last.changes.some(change=>!equal(normalize(change.input),change.afterN)||change.input.matches(':disabled'))){fail('После действия появились другие изменения. Завершите ввод или верните значение через список «Изменения».');return;}
                history.pop();applying=true;
                try{last.changes.forEach(change=>put(change.input,change.before));}finally{applying=false;}
                last.changes.forEach(change=>committed.set(change.input,{raw:raw(change.input),normalized:normalize(change.input)}));
                notify(last.changes);
                document.getElementById('editor-save-state').textContent=`Отменено: ${last.label}. ${changed().length?'Остались несохранённые правки.':'Несохранённых правок нет.'}`;
            }
        };
        let applying=false;
        document.getElementById('editor-undo')?.addEventListener('click',()=>window.motivationEditor.undo());
        const resetDialog=document.getElementById('editor-reset-dialog');
        document.getElementById('editor-reset-changes')?.addEventListener('click',()=>resetDialog.showModal());
        document.getElementById('editor-reset-cancel')?.addEventListener('click',()=>resetDialog.close());
        document.getElementById('editor-reset-confirm')?.addEventListener('click',()=>{window.motivationEditor.apply(changed().map(input=>({input,value:originalRaw.get(input)})),'Сбросить правки');resetDialog.close();});
        fields.forEach(field => {
            field.addEventListener('input', refresh);
            field.addEventListener('change', () => { if(!applying)rememberManual(field); });
            field.addEventListener('blur', () => { if(!applying)rememberManual(field); });
        });
        let leaving = false;
        window.addEventListener('beforeunload', event => {
            if (!leaving && changed().length) { event.preventDefault(); event.returnValue = ''; }
        });
        document.addEventListener('click', event => {
            const link = event.target.closest('a[href]');
            if (!link || link.hasAttribute('download') || link.target === '_blank' || link.getAttribute('href').startsWith('#') || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            if (changed().length && !window.confirm('Есть несохранённые изменения. Покинуть страницу и потерять эти правки?')) {
                event.preventDefault(); event.stopImmediatePropagation();
            } else leaving = true;
        }, true);
        const error = document.getElementById('editor-error');
        const fail = message => { error.textContent = message; error.hidden = false; };
        document.addEventListener('submit', event => {
            const form = event.target;
            error.hidden = true;
            const action = event.submitter?.value;
            if (form.id === 'director-employee-picker') { leaving = true; queueMicrotask(() => { if (event.defaultPrevented) leaving = false; }); return; }
            if (form.id === 'bulk-variation-form' && changed().length) { event.preventDefault(); fail('Сначала сохраните или отмените правки коэффициентов, затем примените формулу.'); return; }
            let warning;
            if (action === 'copy_to_director') warning = 'Заменить коэффициенты выбранного получателя сохранёнными значениями текущего директора? Остальные сотрудники и группы сохранятся.';
            if (action === 'copy_from_global') { const group = form.querySelector('[name=copy_groups]')?.selectedOptions?.[0]?.textContent || 'Политики и продажи'; warning = `Заменить коэффициенты группы «${group}» глобальными значениями выбранной версии? Остальные группы сохраняются. Несохранённые правки будут потеряны.`; }
            if (action === 'apply_sub') {
                const selected = form.querySelectorAll('input[name=selected_subdivisions]:checked').length;
                if (!selected) { event.preventDefault(); fail('Выберите подразделения для копирования.'); return; }
                const groups = form.querySelectorAll('input[name=copy_groups]:checked');
                if (!groups.length) { event.preventDefault(); fail('Выберите группы коэффициентов для копирования.'); return; }
                if ([...groups].some(input => input.value === 'directors')) {
                    if (form.querySelector('[name=branch_employee_selection]')?.value === 'yes') {
                        const missing = [...form.querySelectorAll('[name=selected_subdivisions]:checked')].filter(branch =>
                            form.querySelector(`input[name="director_employees_${branch.value}"]`) && !form.querySelector(`input[name="director_employees_${branch.value}"]:checked`));
                        if (missing.length) { event.preventDefault(); fail('Не выбраны директора: ' + missing.map(branch => branch.closest('label').textContent.trim()).join(', ') + '. Раскройте выбор сотрудников рядом с филиалом.'); return; }
                    } else if (!form.querySelector('[name=director_employee]')?.value) {
                        event.preventDefault(); fail('Выберите сотрудника-директора для копирования.'); return;
                    }
                }
                warning = `Заменить выбранные группы коэффициентов выбранных подразделений (${selected}) сохранёнными глобальными значениями? Несохранённые правки таблицы не копируются.`;
            }
            if (action === 'file_load' && ![...form.elements].find(field => field.type === 'file')?.files.length) {
                event.preventDefault(); fail('Сначала выберите файл для импорта.'); return;
            }
            const savedForm = ['save_coeff', 'update_motive_coeff', 'update_approved_manager', 'add_product'].includes(action) ? form : null;
            if (!warning && changed().some(field => field.form !== savedForm)) warning = 'Несохранённые правки, не относящиеся к этому действию, будут потеряны. Продолжить?';
            if (warning && !window.confirm(warning)) { event.preventDefault(); event.stopImmediatePropagation(); return; }
            leaving = true;
            queueMicrotask(() => { if (event.defaultPrevented) leaving = false; });
        }, true);
        document.addEventListener('invalid', event => {
            if (main.contains(event.target)) reset();
            const details = event.target.closest('details'); if (details) details.open = true;
        }, true);
        document.addEventListener('motivation:before-approval', event => {
            const notInPreview = changed().filter(field => field.form !== main || field.name.startsWith('select_var_calc='));
            if (notInPreview.length) {
                event.preventDefault();
                fail('Сначала сохраните настройки руководителя, формул или добавляемого товара. В предварительное утверждение входят правки коэффициентов и удаления строк.');
                const details = notInPreview[0].closest('details'); if (details) details.open = true;
            }
        });
        document.addEventListener('motivation:approved', () => leaving = true);
        refresh();
        const attempt = document.getElementById('coefficient-attempt');
        if (attempt) {
            const restored = JSON.parse(attempt.textContent);
            for (const field of fields) {
                if (!Object.hasOwn(restored.fields, field.name)) continue;
                if (field.type === 'checkbox') field.checked = true;
                else field.value = restored.fields[field.name];
                if (restored.errors[field.name]) {
                    field.classList.add('invalid'); field.setAttribute('aria-invalid','true');
                    field.title = restored.errors[field.name];
                    const message=document.createElement('small');message.className='ux-form-error';message.textContent=restored.errors[field.name];
                    message.id=`restored-error-${fields.indexOf(field)}`;field.after(message);field.setAttribute('aria-describedby',message.id);
                    field.addEventListener('input',()=>message.remove(),{once:true});
                    field.addEventListener('input', () => field.removeAttribute('aria-invalid'), {once:true});
                }
                field.dispatchEvent(new Event('input',{bubbles:true}));
            }
            if (restored.action === 'add_product') document.getElementById('add-product-details').open=true;
            if (restored.action === 'update_motive_coeff') document.getElementById('manager-details').open=true;
            refresh();
            fields.forEach(field=>committed.set(field,{raw:raw(field),normalized:normalize(field)}));
            fail('Правки восстановлены после ошибки. Сверьте их с текущей версией и сохраните повторно.');
        }
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, {once: true});
    else initialize();
})();
