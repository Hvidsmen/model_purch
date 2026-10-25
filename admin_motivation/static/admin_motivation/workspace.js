(() => {
    const initialize = () => {
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
        const original = new Map();
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
        fields.forEach(field => original.set(field, normalize(field)));
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
            rows.forEach(row => { row.hidden = !matchesFilter(row) || row.dataset.groupCollapsed === 'yes'; });
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
        const refresh = () => {
            fields.forEach(field => field.classList.toggle('editor-changed', different(field)));
            const count = changed().length;
            document.querySelectorAll('[data-workflow-dirty]').forEach(node => { node.hidden = !count; node.textContent = `Не сохранено: ${count}`; });
            document.getElementById('editor-dirty-count').textContent = `Изменено: ${count}`;
            document.getElementById('editor-save-state').textContent = count ? 'Правки ещё не сохранены' : 'Нет несохранённых правок';
            toolbar.querySelector('.editor-state').classList.toggle('has-changes', count > 0);
            document.getElementById('editor-save').disabled = !changed().some(field => field.form === main);
            filter();
        };
        fields.forEach(field => {
            field.addEventListener('input', refresh);
            field.addEventListener('change', refresh);
            field.addEventListener('blur', refresh);
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
            fail('Правки восстановлены после ошибки. Сверьте их с текущей версией и сохраните повторно.');
        }
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, {once: true});
    else initialize();
})();
