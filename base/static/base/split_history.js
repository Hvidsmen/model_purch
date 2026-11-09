(function () {
    function syncHistory(table) {
        const workspace = table.closest('.split-workspace');
        const selected = new Set(Array.from(workspace.querySelectorAll('[data-history-year-choice]:checked'), input => input.dataset.historyYearChoice));
        const opened = new Set(Array.from(table.querySelectorAll('details[data-history-toggle][open]'), details => details.dataset.historyToggle));
        table.querySelectorAll('[data-history-year-column]').forEach(function (cell) {
            cell.hidden = !selected.has(cell.dataset.historyYearColumn) ||
                (cell.hasAttribute('data-history-month-column') && !opened.has(cell.dataset.historyMonthColumn));
        });
        table.querySelectorAll('[data-history-band]').forEach(function (band) {
            let count = Number(band.dataset.historyBaseSpan);
            table.querySelectorAll('thead [data-history-year-column]').forEach(function (cell) {
                const key = cell.dataset.historyBandKey || cell.dataset.historyMonthColumn.slice(0, -5);
                if (!cell.hidden && key === band.dataset.historyBand) count++;
            });
            band.colSpan = count;
        });
    }
    document.addEventListener('toggle', function (event) {
        const details = event.target;
        if (details instanceof HTMLDetailsElement && details.dataset.historyToggle) syncHistory(details.closest('table'));
    }, true);
    document.addEventListener('change', function (event) {
        if (!event.target.matches('[data-history-year-choice]')) return;
        const table = event.target.closest('.split-workspace').querySelector('.split-plan-matrix');
        if (table) syncHistory(table);
    });
}());
