(() => {
    document.querySelectorAll('[data-scenario-setup] [data-setup-open]').forEach(link => {
        link.addEventListener('click', event => {
            const panel = document.getElementById(link.dataset.setupOpen);
            if (!panel) return;
            event.preventDefault();
            panel.open = true;
            panel.scrollIntoView({block:'start'});
            const target = link.dataset.setupFocus ? document.getElementById(link.dataset.setupFocus) : panel.querySelector('input:not([type=hidden])');
            target?.focus({preventScroll:true});
        });
    });
})();
