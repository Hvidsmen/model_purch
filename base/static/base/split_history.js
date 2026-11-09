document.addEventListener('toggle', function (event) {
    const details = event.target;
    if (!(details instanceof HTMLDetailsElement) || !details.dataset.historyToggle) return;
    const table = details.closest('table');
    if (!table) return;
    table.querySelectorAll('[data-history-month]').forEach(function (row) {
        if (row.dataset.historyMonth === details.dataset.historyToggle) row.hidden = !details.open;
    });
}, true);
