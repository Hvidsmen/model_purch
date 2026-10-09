(() => {
    const node = document.getElementById('pricing-options');
    if (!node) return;
    const options = JSON.parse(node.textContent);
    const key = value => String(value || '').normalize('NFC').trim().toLocaleLowerCase();
    const russian = new Set(options.russian_suppliers.map(key));
    const duties = new Map(Object.entries(options.group_duties).map(([name, value]) => [key(name), value]));
    document.querySelectorAll('.pricing-row').forEach(row => {
        const field = name => row.querySelector(`[name="${name}"]`);
        const numeric = name => Number((field(name)?.value || '0').replace(',', '.'));
        const previousKddp = field('kddp')?.value || '1';
        const display = (name, value) => { if (field(name)) field(name).value = Number.isFinite(value) ? value.toFixed(2) : ''; };
        function calculate() {
            const exw = numeric('exw_usd');
            const ratio = numeric('volume') / numeric('container_volume');
            const duty = numeric('duty_rate') / 100;
            if (!Number.isFinite(ratio) || numeric('container_volume') <= 0) return;
            const shipping = ratio * Number(options.container_price);
            const cif = exw + shipping;
            const customs = Number(options.customs_rate) / 100 * cif * (1 + duty);
            const delivery = Number(options.delivery_cost) * ratio;
            const ddp = russian.has(key(field('purch')?.value)) ? exw : cif + customs + delivery;
            display('freight_usd', shipping); display('cif_usd', cif); display('customs_payment_usd', customs);
            display('warehouse_delivery_usd', delivery); display('ddp_usd', ddp);
            if (exw > 0) display('kddp', ddp / exw);
            else if (field('kddp')) field('kddp').value = previousKddp;
        }
        row.addEventListener('change', event => { if (event.target.tagName === 'SELECT') event.target.dispatchEvent(new Event('input', {bubbles: true})); });
        row.addEventListener('input', event => {
            if (event.target.name === 'group_goods' && field('duty_rate')) {
                field('duty_rate').value = duties.get(key(event.target.value)) || '0';
                field('duty_rate').dispatchEvent(new Event('input', {bubbles: true}));
            }
            calculate();
        });
        row.addEventListener('change', calculate);
        calculate();
    });
})();
