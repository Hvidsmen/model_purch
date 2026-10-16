(() => {
    const filters = document.querySelectorAll('[data-summary-filter]');
    filters.forEach(filter => {
        const inputs = [...filter.querySelectorAll('input[type=checkbox]')];
        const labels = [...filter.querySelectorAll('.summary-filter-options label')];
        const caption = filter.querySelector('[data-filter-caption]');
        const sync = () => { const selected = inputs.filter(input => input.checked); caption.textContent = selected.length ? `Выбрано: ${selected.length}` : 'Все'; };
        filter.querySelector('input[type=search]').addEventListener('input', event => {
            const query = event.target.value.toLocaleLowerCase().trim();
            labels.forEach(label => label.hidden = !label.textContent.toLocaleLowerCase().includes(query));
        });
        inputs.forEach(input => input.addEventListener('change', sync));
        filter.querySelector('[data-filter-all]').addEventListener('click', () => { labels.filter(label => !label.hidden).forEach(label => label.querySelector('input').checked = true); sync(); });
        filter.querySelector('[data-filter-clear]').addEventListener('click', () => { inputs.forEach(input => input.checked = false); sync(); });
        document.addEventListener('click', event => { if (!filter.contains(event.target)) filter.open = false; });
        filter.addEventListener('keydown', event => { if (event.key === 'Escape') filter.open = false; });
        sync();
    });
    document.getElementById('summary-plan')?.addEventListener('change', () => filters.forEach(filter => {
        filter.querySelectorAll('input[type=checkbox]').forEach(input => { input.checked = false; input.dispatchEvent(new Event('change')); });
    }));
    const report = document.getElementById('plan-summary');
    if (!report) return;
    report.querySelectorAll('[data-summary-side]').forEach(input => {
        input.addEventListener('change', () => report.classList.toggle(`hide-${input.dataset.summarySide}`, !input.checked));
    });
    report.querySelector('[data-summary-expand]')?.addEventListener('click', () => report.querySelectorAll('details').forEach(node => node.open = true));
    report.querySelector('[data-summary-collapse]')?.addEventListener('click', () => report.querySelectorAll('details').forEach(node => node.open = false));
})();
