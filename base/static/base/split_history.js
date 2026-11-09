document.addEventListener('toggle', function (event) {
    const details = event.target;
    if (!(details instanceof HTMLDetailsElement) || !details.dataset.historyToggle) return;
    const table = details.closest('table');
    if (!table) return;
    let count = 0;
    table.querySelectorAll('thead [data-history-month-column]').forEach(function (cell) {
        if (cell.dataset.historyMonthColumn === details.dataset.historyToggle) count++;
    });
    table.querySelectorAll('[data-history-month-column]').forEach(function (cell) {
        if (cell.dataset.historyMonthColumn === details.dataset.historyToggle) cell.hidden = !details.open;
    });
    const band = details.closest('th').dataset.historyBandKey;
    table.querySelectorAll('[data-history-band]').forEach(function (cell) {
        if (cell.dataset.historyBand === band) cell.colSpan += details.open ? count : -count;
    });
}, true);
