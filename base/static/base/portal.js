(() => {
    const button = document.getElementById('portal-menu-toggle');
    const sidebar = document.getElementById('portal-sidebar');
    const layout = document.querySelector('.portal-layout');
    if (!button || !sidebar || !layout) return;
    const media = window.matchMedia('(max-width:850px)');
    const key = () => media.matches ? 'portal-menu-mobile-open-v1' : 'portal-menu-desktop-open-v1';
    const setOpen = (open, persist = false) => {
        sidebar.hidden = !open;
        sidebar.classList.toggle('portal-menu-open', open);
        layout.classList.toggle('portal-menu-collapsed', !open);
        button.setAttribute('aria-expanded', String(open));
        button.textContent = open ? 'Скрыть меню' : 'Показать меню';
        if (persist) { try { localStorage.setItem(key(), String(open)); } catch (_) {} }
    };
    const restore = () => {
        let open = !media.matches;
        try { const saved = localStorage.getItem(key()); if (saved !== null) open = saved === 'true'; } catch (_) {}
        setOpen(open);
    };
    button.addEventListener('click', () => setOpen(button.getAttribute('aria-expanded') !== 'true', true));
    sidebar.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); setOpen(false, true); button.focus(); }
    });
    media.addEventListener('change', restore);
    window.addEventListener('pageshow', restore);
    restore();
})();
