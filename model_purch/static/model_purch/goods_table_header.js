/* Keep a wide table's heading visible while the document scrolls vertically. */
(() => {
  const wrapper = document.querySelector('.goods-table-scroll');
  const table = wrapper?.querySelector('table');
  if (!table?.tHead) return;
  const panel = document.createElement('div');
  panel.className = 'goods-fixed-heading';
  panel.hidden = true;
  const copy = document.createElement('table');
  copy.className = table.className;
  copy.setAttribute('aria-label', 'Заголовок справочника товаров');
  const columns = table.querySelector('colgroup');
  if (columns) copy.append(columns.cloneNode(true));
  copy.append(table.tHead.cloneNode(true));
  copy.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
  panel.append(copy);
  wrapper.after(panel);
  const sourceChecks = Array.from(table.tHead.querySelectorAll('input[type=checkbox]'));
  copy.querySelectorAll('input[type=checkbox]').forEach((check, index) => {
    check.addEventListener('change', () => {
      sourceChecks[index].click();
      syncChecks();
    });
  });
  const syncChecks = () => copy.querySelectorAll('input[type=checkbox]').forEach((check, index) => {
    check.checked = sourceChecks[index].checked;
    check.indeterminate = sourceChecks[index].indeterminate;
  });
  let pending = false;
  const render = () => {
    pending = false;
    const rect = table.getBoundingClientRect();
    const bounds = wrapper.getBoundingClientRect();
    const height = table.tHead.getBoundingClientRect().height;
    panel.hidden = rect.top >= 0 || rect.bottom <= 0 || !height;
    if (panel.hidden) return;
    const left = Math.max(0, bounds.left);
    panel.style.left = `${left}px`;
    panel.style.width = `${Math.max(0, Math.min(innerWidth, bounds.right) - left)}px`;
    panel.style.transform = `translateY(${Math.min(0, rect.bottom - height)}px)`;
    copy.style.width = `${rect.width}px`;
    copy.style.minWidth = `${rect.width}px`;
    copy.style.transform = `translateX(${bounds.left - left - wrapper.scrollLeft}px)`;
    const originals = table.tHead.querySelectorAll('th');
    copy.querySelectorAll('th').forEach((cell, index) => {
      const source = originals[index];
      cell.style.display = getComputedStyle(source).display;
      cell.style.width = `${source.getBoundingClientRect().width}px`;
    });
    syncChecks();
  };
  const schedule = () => {
    if (!pending) { pending = true; requestAnimationFrame(render); }
  };
  document.addEventListener('scroll', schedule, {capture: true, passive: true});
  window.addEventListener('resize', schedule, {passive: true});
  table.addEventListener('change', schedule);
  new ResizeObserver(schedule).observe(table);
  render();
})();
