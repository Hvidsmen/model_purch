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
        const toolbar = document.getElementById('editor-toolbar');
        if (!toolbar) return;
        const main = document.getElementById(toolbar.dataset.form);
        const forms = [main, document.getElementById('addProductForm'), document.querySelector('#manager-details form')].filter(Boolean);
        const fields = forms.flatMap(form => [...form.querySelectorAll('input:not([type=hidden]):not([type=file]), select')]
            .filter(input => !input.matches(':disabled') && input.name !== 'selected_subdivisions'));
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
            rows.forEach(row => {
                row.hidden = !fold([row.dataset.pg, row.dataset.group, row.dataset.brand, row.dataset.key].join(' ')).includes(query)
                    || selectors.some(([key, select]) => select.value && select.value !== row.dataset[key])
                    || (onlyChanged.checked && !row.querySelector('.editor-changed'));
            });
            const count = rows.filter(row => !row.hidden).length;
            document.getElementById('coefficient-visible-count').textContent = `Показано: ${count} из ${rows.length}`;
            document.getElementById('coefficient-filter-empty').hidden = count > 0;
        };
        const reset = () => { search.value = ''; onlyChanged.checked = false; selectors.forEach(([, select]) => select.value = ''); filter(); };
        search.addEventListener('input', filter);
        onlyChanged.addEventListener('change', filter);
        selectors.forEach(([, select]) => select.addEventListener('change', filter));
        document.getElementById('coefficient-reset').addEventListener('click', reset);
        const refresh = () => {
            fields.forEach(field => field.classList.toggle('editor-changed', different(field)));
            const count = changed().length;
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
            let warning;
            if (action === 'copy_from_global') warning = 'Заменить все коэффициенты этого подразделения глобальными значениями выбранной версии? Текущие индивидуальные значения и несохранённые правки будут заменены.';
            if (action === 'apply_sub') {
                const selected = form.querySelectorAll('input[name=selected_subdivisions]:checked').length;
                if (!selected) { event.preventDefault(); fail('Выберите подразделения для копирования.'); return; }
                warning = `Заменить коэффициенты выбранных подразделений (${selected}) сохранёнными глобальными значениями? Несохранённые правки таблицы не копируются.`;
            }
            if (action === 'file_load' && !form.querySelector('input[type=file]')?.files.length) {
                event.preventDefault(); fail('Сначала выберите файл для импорта.'); return;
            }
            const savedForm = ['save_coeff', 'update_motive_coeff', 'add_product'].includes(action) ? form : null;
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
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, {once: true});
    else initialize();
})();
