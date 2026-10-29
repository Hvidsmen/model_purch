(() => {
    const node = document.getElementById('pricing-options');
    if (!node) return;
    const options = JSON.parse(node.textContent);
    const key = value => String(value || '').normalize('NFC').trim().toLocaleLowerCase();
    const russian = new Set(options.russian_suppliers.map(key));
    const suppliers = new Map(Object.entries(options.supplier_types || {}).map(([name, type]) => [key(name), type]));
    const purchaseUrls = new Map(Object.entries(options.purchase_urls || {}).map(([name, url]) => [key(name), url]));
    const duties = new Map(Object.entries(options.group_duties).map(([name, value]) => [key(name), value]));
    document.querySelectorAll('.pricing-row').forEach(row => {
        const field = name => row.querySelector(`[name="${name}"]`);
        const numeric = name => Number((field(name)?.value || '0').replace(',', '.'));
        const previousKddp = field('kddp')?.value || '1';
        const display = (name, value) => { if (field(name)) field(name).value = Number.isFinite(value) ? value.toFixed(2) : ''; };
        const purchaseLink = row.querySelector('[data-purchase-link]');
        const purchaseListUrl = purchaseLink?.href;
        const percent = row.querySelector('[data-duty-percent]');
        if (percent) {
            percent.addEventListener('keydown', event => {
                if (event.key !== 'Escape') return;
                event.preventDefault();
                percent.setCustomValidity('');
                row.querySelectorAll('.editable-field').forEach(input => { input.value = input.dataset.original; });
                percent.blur();
                field('duty_rate').dispatchEvent(new Event('input', {bubbles:true}));
            });
            percent.addEventListener('input', () => {
                const value = Number(percent.value.replace(',', '.'));
                percent.setCustomValidity(percent.value === '' || !Number.isFinite(value) || value < 0 || value > 10000 ? 'Введите пошлину в процентах: например, 10 для 10%.' : '');
                if (!percent.checkValidity()) { percent.reportValidity(); return; }
                field('duty_rate').value = String(value / 100);
                field('duty_rate').dispatchEvent(new Event('input', {bubbles:true}));
            });
        }
        const setDuty = value => {
            field('duty_rate').value = String(value);
            percent.value = String(Number(value) * 100);
            field('duty_rate').dispatchEvent(new Event('input', {bubbles:true}));
        };
        row.querySelector('[data-duty-fix]')?.addEventListener('click', () => setDuty(0.1));
        row.querySelector('[data-duty-group]')?.addEventListener('click', () => setDuty(duties.get(key(field('group_goods')?.value))));
        function calculate() {
            if (percent && document.activeElement !== percent) percent.value = String(Number((numeric('duty_rate') * 100).toPrecision(15)));
            const source = row.querySelector('[data-duty-source]');
            if (source) source.textContent = duties.has(key(field('group_goods')?.value)) && Number(duties.get(key(field('group_goods')?.value))) === numeric('duty_rate') ? 'Совпадает с группой' : 'Ставка товара';
            const rate = numeric('duty_rate');
            const groupRate = duties.get(key(field('group_goods')?.value));
            const small = rate > 0 && rate < 0.01;
            const differs = groupRate !== undefined && Math.abs(Number(groupRate) - rate) > 1e-9;
            const notices = [];
            if (small) notices.push(`Вы указали ${rate * 100}%. Возможно, имелось в виду 10%?`);
            if (differs) notices.push(`Для группы установлено ${Number(groupRate) * 100}%, у товара — ${rate * 100}%.`);
            row.dataset.dutyWarning = notices.join(' ');
            const meaning = row.querySelector('[data-duty-meaning]');
            if (meaning) meaning.textContent = `${Number((rate * 100).toPrecision(10))}% от таможенной стоимости`;
            const warning = row.querySelector('[data-duty-warning]');
            if (warning) warning.textContent = notices.join(' ');
            const fix = row.querySelector('[data-duty-fix]');
            if (fix) fix.hidden = !small;
            const groupButton = row.querySelector('[data-duty-group]');
            if (groupButton) groupButton.hidden = !differs;
            if (purchaseLink) {
                purchaseLink.href = purchaseUrls.get(key(field('purch')?.value)) || purchaseListUrl;
            }
            const exw = numeric('exw_usd');
            const ratio = numeric('volume') / numeric('container_volume');
            const duty = numeric('duty_rate');
            if (!Number.isFinite(ratio) || numeric('container_volume') <= 0) return;
            const isRussian = russian.has(key(field('purch')?.value));
            const shipping = isRussian ? 0 : ratio * Number(options.container_price);
            const cif = isRussian ? 0 : exw + shipping;
            const customs = Number(options.customs_rate) / 100 * cif * (1 + duty);
            const delivery = isRussian ? 0 : Number(options.delivery_cost) * ratio;
            const ddp = isRussian ? exw : cif + customs + delivery;
            display('freight_usd', shipping); display('cif_usd', cif); display('customs_payment_usd', customs);
            display('foreign_delivery_usd', isRussian ? 0 : ratio * Number(options.foreign_delivery_cost || 0));
            display('nr_customs_vat_usd', suppliers.get(key(field('purch')?.value)) === 'p2' ? customs * Number(options.nr_customs_vat_rate || 0) / 100 : 0);
            display('warehouse_delivery_usd', delivery); display('ddp_usd', ddp);
            const planQuantity = Number(row.dataset.planQuantity);
            const stockQuantity = Number(row.dataset.stockQuantity || 0);
            const stockDdp = row.dataset.stockDdp;
            const validQuantities = Number.isFinite(planQuantity) && planQuantity > 0 && Number.isFinite(stockQuantity) && stockQuantity >= 0;
            const purchaseQuantity = validQuantities ? Math.max(planQuantity - stockQuantity, 0) : NaN;
            const stockUsed = validQuantities ? Math.min(stockQuantity, planQuantity) : NaN;
            if (field('weighted_ddp_usd')) {
                const stockPrice = stockDdp === '' ? NaN : Number(stockDdp);
                const validPrices = (!purchaseQuantity || Number.isFinite(ddp) && ddp >= 0) && (!stockUsed || Number.isFinite(stockPrice) && stockPrice >= 0);
                const weighted = validQuantities && validPrices
                    ? ((purchaseQuantity ? ddp * purchaseQuantity : 0) + (stockUsed ? stockPrice * stockUsed : 0)) / planQuantity : NaN;
                field('weighted_ddp_usd').value = Number.isFinite(weighted) && weighted >= 0 ? weighted.toFixed(2) : '—';
            }
            const money = value => Number.isFinite(value) ? value.toLocaleString('ru-RU', {maximumFractionDigits:2}) : '—';
            const explanation = row.querySelector('[data-price-explanation]');
            if (explanation) explanation.textContent = isRussian ? `Российская закупка: DDP = EXW = ${money(exw)} USD/шт.` : `CIF ${money(cif)} + таможня ${money(customs)} + доставка ${money(delivery)} = DDP ${money(ddp)} USD/шт. Таможня = CIF × ${options.customs_rate}% × (1 + ${money(duty)}). Фрахт и доставка распределяются по доле объёма товара в контейнере.`;
            const stockExplanation = row.querySelector('[data-stock-explanation]');
            if (stockExplanation) stockExplanation.textContent = validQuantities ? `План: ${money(planQuantity)} шт. Из стока: ${money(stockUsed)} шт. по ${stockDdp === '' ? 'неизвестной цене' : money(Number(stockDdp)) + ' USD/шт.'} Закупка: ${money(purchaseQuantity)} шт. по ${money(ddp)} USD/шт. Средняя стоимость = (стоимость закупки + стоимость используемого стока) / план.` : 'Средняя стоимость недоступна: нужны положительный план и корректное количество стока.';
            const purchaseNote = row.querySelector('[data-purchase-plan-note]');
            if (purchaseNote) purchaseNote.hidden = !(validQuantities && purchaseQuantity === 0);
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
