(() => {
    let highlightTimer;
    const reveal = (region, target) => {
        if (!region) return;
        let ancestor = region.closest('details');
        while (ancestor) { ancestor.open = true; ancestor = ancestor.parentElement?.closest('details'); }
        const workspace = document.querySelector('.workspace-panel');
        const offset = workspace && getComputedStyle(workspace).position === 'sticky' ? workspace.offsetHeight + 20 : 20;
        const destination = target || region;
        destination.style.scrollMarginTop = `${offset}px`;
        destination.scrollIntoView({block:'center', inline:'center'});
        if (!destination.matches('input, select, textarea, button, a, summary')) destination.setAttribute('tabindex','-1');
        destination.focus({preventScroll:true});
        document.querySelectorAll('.purchases-setup-target').forEach(element => element.classList.remove('purchases-setup-target'));
        clearTimeout(highlightTimer);
        region.classList.add('purchases-setup-target');
        highlightTimer = setTimeout(() => region.classList.remove('purchases-setup-target'),5000);
    };
    const fillTable = () => {
        const inputs = [...document.querySelectorAll('.pricing-row input[name=exw_usd]:not(:disabled), .pricing-row input[name=volume]:not(:disabled)')];
        const target = inputs.find(input => !input.readOnly && (!Number.isFinite(Number(input.value.replace(',','.'))) || Number(input.value.replace(',','.')) <= 0))
            || inputs.find(input => input.name === 'exw_usd' && !input.readOnly);
        if (target) reveal(target.closest('label') || target, target);
        else {
            reveal(document.getElementById('refreshGoodsDirectoryForm'),document.getElementById('refreshGoodsDirectoryBtn'));
            const status = document.querySelector('[data-setup-status]');
            if (status) { status.hidden = false; status.textContent = 'Нет доступных строк для заполнения. Проверьте фильтры или добавьте товары из плана.'; }
        }
    };
    document.querySelectorAll('[data-scenario-setup] [data-setup-open]').forEach(link => {
        link.addEventListener('click', event => {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            const panel = document.getElementById(link.dataset.setupOpen);
            if (!panel) return;
            event.preventDefault();
            panel.open = true;
            const target = link.dataset.setupFocus ? document.getElementById(link.dataset.setupFocus) : panel.querySelector('input:not([type=hidden]):not(:disabled)');
            reveal(target?.closest('form') || panel,target);
        });
    });
    document.querySelectorAll('[data-setup-table]').forEach(link => link.addEventListener('click',event => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault(); fillTable();
    }));
    const modal = document.getElementById('copyPggoodsModal');
    modal?.addEventListener('shown.bs.modal',() => {
        const select = modal.querySelector('select[name=source_scenario_id]');
        if (select) reveal(select,select);
    });
    const focusHash = () => {
        const id = location.hash.slice(1);
        if (id === 'goods-editor-table') { fillTable(); return; }
        const fields = {
            'plan-parameters-load':'input[name=confirm]:not(:disabled)',
            'scenario-sales-plans':'input[name^="plans-"][name$="-name"]:not(:disabled)',
            'importForm':'input[type=file]:not(:disabled)',
            'freight-settings':'input:not([type=hidden]):not(:disabled)'
        };
        if (id === 'freight-source') {
            const select = document.getElementById(id);
            if (select) reveal(select.closest('form'),select);
        } else if (fields[id]) {
            const region = document.getElementById(id);
            if (region) reveal(region,region.querySelector(fields[id]));
        }
    };
    window.addEventListener('hashchange',focusHash);
    window.addEventListener('pageshow',() => requestAnimationFrame(focusHash));
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',() => requestAnimationFrame(focusHash),{once:true});
    else requestAnimationFrame(focusHash);
})();
