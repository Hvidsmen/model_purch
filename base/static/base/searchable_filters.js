/* Searchable filters keep their native select as the authoritative form value. */
(() => {
    const normalize = text => text.normalize('NFC').toLocaleLowerCase().trim();
    document.querySelectorAll('.filters-card select, .coefficient-filters select, #report-filters select, #purchPaymentFilter').forEach(select => {
        if (select.dataset.searchReady) return;
        select.dataset.searchReady = 'true';
        const root = document.createElement('div');
        root.className = 'searchable-filter';
        const trigger = document.createElement('button');
        trigger.type = 'button'; trigger.className = 'form-select form-select-sm';
        trigger.setAttribute('aria-expanded', 'false');
        const label = select.closest('label')?.textContent.trim() || select.parentElement.querySelector('label')?.textContent.trim() || select.getAttribute('aria-label') || 'Фильтр';
        trigger.setAttribute('aria-label', label);
        const popup = document.createElement('div'); popup.className = 'searchable-filter-menu'; popup.hidden = true;
        const search = document.createElement('input'); search.type = 'search'; search.className = 'form-control form-control-sm'; search.placeholder = 'Поиск…'; search.setAttribute('aria-label', `Поиск: ${label}`);
        const list = document.createElement('div'); list.className = 'searchable-filter-options';
        popup.append(search, list); root.append(trigger, popup);
        select.after(root); select.hidden = true;
        function close() { popup.hidden = true; trigger.setAttribute('aria-expanded', 'false'); }
        function render() {
            list.replaceChildren();
            const options = [...select.options].filter(option => normalize(option.text).includes(normalize(search.value)));
            for (const option of options) {
                const button = document.createElement('button'); button.type = 'button'; button.textContent = option.text;
                button.disabled = option.disabled; button.className = option.selected ? 'selected' : '';
                button.addEventListener('click', () => {
                    select.value = option.value;
                    select.dispatchEvent(new Event('change', {bubbles: true}));
                    select.dispatchEvent(new Event('input', {bubbles: true}));
                    close(); trigger.focus();
                });
                list.append(button);
            }
            if (!options.length) { const empty = document.createElement('p'); empty.textContent = 'Нет совпадений'; list.append(empty); }
        }
        function sync() { trigger.textContent = select.selectedOptions[0]?.text || '— Все —'; trigger.disabled = select.disabled; }
        trigger.addEventListener('click', () => {
            const open = popup.hidden;
            document.querySelectorAll('.searchable-filter-menu').forEach(menu => { menu.hidden = true; menu.previousElementSibling?.setAttribute('aria-expanded', 'false'); });
            if (open) { popup.hidden = false; trigger.setAttribute('aria-expanded', 'true'); search.value = ''; render(); search.focus(); }
        });
        search.addEventListener('input', render);
        root.addEventListener('keydown', event => {
            if (event.key === 'Escape') { close(); trigger.focus(); }
            if (event.key === 'ArrowDown') { event.preventDefault(); const buttons = [...list.querySelectorAll('button:not(:disabled)')]; buttons[(buttons.indexOf(document.activeElement) + 1) % buttons.length]?.focus(); }
            if (event.key === 'Enter' && event.target === search) { event.preventDefault(); list.querySelector('button:not(:disabled)')?.click(); }
        });
        document.addEventListener('click', event => { if (!root.contains(event.target)) close(); });
        select.addEventListener('change', sync);
        select.form?.addEventListener('reset', () => setTimeout(sync, 0));
        sync();
    });
})();
