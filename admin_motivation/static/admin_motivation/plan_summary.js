(() => {
    const report = document.getElementById('plan-summary');
    if (!report) return;
    report.querySelectorAll('[data-summary-side]').forEach(input => {
        input.addEventListener('change', () => report.classList.toggle(`hide-${input.dataset.summarySide}`, !input.checked));
    });
    report.querySelector('[data-summary-expand]')?.addEventListener('click', () => report.querySelectorAll('details').forEach(node => node.open = true));
    report.querySelector('[data-summary-collapse]')?.addEventListener('click', () => report.querySelectorAll('details').forEach(node => node.open = false));
})();
