(() => {
    const report = document.getElementById('period-report');
    if (!report) return;
    document.getElementById('report-expand').addEventListener('click', () => {
        report.querySelectorAll('details').forEach(node => node.open = true);
    });
    document.getElementById('report-collapse').addEventListener('click', () => {
        report.querySelectorAll('details').forEach(node => node.open = false);
    });
})();
