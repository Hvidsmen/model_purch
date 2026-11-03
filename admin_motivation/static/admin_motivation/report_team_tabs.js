(() => {
  document.querySelectorAll('[data-team-tabs]').forEach(root => {
    const tabs = [...root.querySelectorAll('[data-team-tab]')];
    const panels = [...root.querySelectorAll('[data-team-panel]')];
    if (!tabs.length || tabs.length !== panels.length) return;
    const activate = (index, focus = false, remember = false) => {
      tabs.forEach((tab, current) => {
        const active = current === index;
        tab.setAttribute('aria-selected', String(active));
        tab.tabIndex = active ? 0 : -1;
        panels[current].hidden = !active;
      });
      if (focus) tabs[index].focus();
      if (remember) history.replaceState(null, '', location.pathname + location.search + '#' + panels[index].id);
    };
    const fromHash = () => Math.max(0, panels.findIndex(panel => '#' + panel.id === location.hash));
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => activate(index, false, true));
      tab.addEventListener('keydown', event => {
        let next;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = tabs.length - 1;
        if (next === undefined) return;
        event.preventDefault();
        activate(next, true, true);
      });
    });
    activate(fromHash());
    window.addEventListener('hashchange', () => activate(fromHash()));
  });
})();
