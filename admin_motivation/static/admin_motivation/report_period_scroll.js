(() => {
  const panes = [...document.querySelectorAll('[data-period-scroll]')];
  panes.forEach(source => source.addEventListener('scroll', () => {
    panes.forEach(target => {
      if (target !== source && Math.abs(target.scrollLeft - source.scrollLeft) > 1) {
        target.scrollLeft = source.scrollLeft;
      }
    });
  }, {passive: true}));
})();
