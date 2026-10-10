(() => {
    const report = document.getElementById('period-report');
    const data = document.getElementById('report-tree-data');
    if (!report || !data) return;
    const roots = JSON.parse(data.textContent);
    const number = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0});
    const rows = new WeakMap();
    function attach(element, node) {
        rows.set(element, node);
        element.addEventListener('toggle', () => { if (element.open) populate(element); });
        if (element.open) populate(element);
    }
    function populate(element) {
        if (element.dataset.loaded) return;
        const node = rows.get(element);
        if (!node) return;
        const container = element.querySelector(':scope > .report-children');
        const fragment = document.createDocumentFragment();
        node.children.forEach(child => fragment.appendChild(create(child)));
        container.appendChild(fragment);
        element.dataset.loaded = 'true';
    }
    function create(node) {
        const parent = document.createElement(node.children.length ? 'details' : 'div');
        parent.className = node.children.length ? 'report-node' : 'report-grid report-leaf';
        parent.dataset.level = node.level;
        const row = node.children.length ? document.createElement('summary') : parent;
        row.className = 'report-grid' + (node.children.length ? '' : ' report-leaf');
        row.style.setProperty('--level', node.level);
        [node.name, ...['plan', 'policies', 'sales', 'total'].map(key => node[key] == null ? (key === 'plan' ? '0' : '—') : number.format(Number(node[key])))].forEach((value, index) => {
            const span = document.createElement('span');
            if (!index) span.className = 'report-label';
            span.textContent = value;
            row.appendChild(span);
        });
        if (node.children.length) {
            parent.appendChild(row);
            const container = document.createElement('div');
            container.className = 'report-children';
            parent.appendChild(container);
            attach(parent, node);
        }
        return parent;
    }
    report.querySelectorAll(':scope > details.report-node').forEach((element, index) => attach(element, roots[index]));
    document.getElementById('report-expand').addEventListener('click', () => {
        const expand = element => {
            populate(element);
            element.open = true;
            element.querySelectorAll(':scope > .report-children > details').forEach(expand);
        };
        report.querySelectorAll(':scope > details').forEach(expand);
    });
    document.getElementById('report-collapse').addEventListener('click', () => {
        report.querySelectorAll('details').forEach(node => node.open = false);
    });
})();
