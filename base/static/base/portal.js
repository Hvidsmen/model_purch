(() => {
    const button = document.getElementById('portal-menu-toggle');
    const sidebar = document.getElementById('portal-sidebar');
    if (!button || !sidebar) return;
    const setOpen = open => {
        sidebar.classList.toggle('portal-menu-open', open);
        button.setAttribute('aria-expanded', String(open));
    };
    button.addEventListener('click', () => setOpen(button.getAttribute('aria-expanded') !== 'true'));
    sidebar.addEventListener('keydown', event => {
        if (event.key === 'Escape' && button.offsetParent !== null) { setOpen(false); button.focus(); }
    });
    const media = window.matchMedia('(max-width:850px)');
    media.addEventListener('change', () => setOpen(false));
})();
