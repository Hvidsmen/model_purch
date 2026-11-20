(() => {
  const table = document.querySelector('.split-plan-matrix');
  if (!table) return;
  const nodes = new Map(Array.from(table.querySelectorAll('[data-plan-node]'), row => [row.dataset.planNode, row]));
  const groups = new Map(Array.from(table.querySelectorAll('[data-plan-group]'), row => [row.dataset.planGroup, row]));
  const storage = 'split-plan-tree-' + table.dataset.planTreeStorage;
  function nodeExpanded(node) { return node.querySelector('[data-plan-subtotal-toggle]').getAttribute('aria-expanded') === 'true'; }
  function setNode(node, value) {
    const button = node.querySelector('[data-plan-subtotal-toggle]');
    button.setAttribute('aria-expanded', String(value));
    button.querySelector('[data-plan-subtotal-arrow]').textContent = value ? '▾' : '▸';
  }
  let nested = true;
  document.addEventListener('split-table-view-change', event => { nested = event.detail.mode === 'nested'; syncTree(); });
  function syncTree() {
    nodes.forEach(node => { node.toggleAttribute('data-view-excluded', !nested); });
    table.closest('.split-workspace').querySelectorAll('.split-hierarchy-tools').forEach(tool => { tool.hidden = !nested; });
    table.querySelectorAll('[data-plan-parent]').forEach(row => {
      let parent = row.dataset.planParent, visible = true;
      while (nested && parent && nodes.has(parent)) {
        const ancestor = nodes.get(parent);
        if (!nodeExpanded(ancestor)) { visible = false; break; }
        parent = ancestor.dataset.planParent;
      }
      if (row.hasAttribute('data-plan-power-group')) {
        const group = groups.get(row.dataset.planPowerGroup);
        visible = visible && group?.querySelector('[data-plan-group-toggle]').getAttribute('aria-expanded') === 'true';
      }
      row.hidden = !visible;
    });
  }
  function saveTree() {
    try { sessionStorage.setItem(storage, JSON.stringify(Object.fromEntries(Array.from(nodes, ([key, node]) => [key, nodeExpanded(node)])))); } catch (_) {}
  }
  try {
    const saved = JSON.parse(sessionStorage.getItem(storage) || '{}');
    nodes.forEach((node, key) => { if (typeof saved[key] === 'boolean') setNode(node, saved[key]); });
  } catch (_) {}
  syncTree();
  const workspace = table.closest('.split-workspace');
  workspace.querySelectorAll('[data-plan-tree-expand], [data-plan-tree-collapse]').forEach(button => {
    button.addEventListener('keydown', event => {
      if (!['Enter', ' '].includes(event.key)) return;
      event.preventDefault(); button.click();
    });
    button.addEventListener('click', () => {
      nodes.forEach(node => setNode(node, button.hasAttribute('data-plan-tree-expand')));
      syncTree(); saveTree();
    });
  });
  table.addEventListener('keydown', event => {
    if (!event.target.matches('[data-plan-group-toggle], [data-plan-opening-toggle], [data-plan-closing-toggle], [data-plan-subtotal-toggle]') || !['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    event.target.click();
  });
  table.addEventListener('click', event => {
    const subtotal = event.target.closest('[data-plan-subtotal-toggle]');
    if (subtotal && table.contains(subtotal)) {
      const node = nodes.get(subtotal.dataset.planSubtotalToggle);
      setNode(node, !nodeExpanded(node)); syncTree(); saveTree();
      return;
    }
    const closing = event.target.closest('[data-plan-closing-toggle]');
    if (closing && table.contains(closing)) {
      const expanded = closing.getAttribute('aria-expanded') !== 'true';
      closing.setAttribute('aria-expanded', String(expanded));
      closing.querySelector('[data-plan-closing-arrow]').textContent = expanded ? '▾' : '▸';
      table.querySelectorAll('[data-plan-closing-component]').forEach(cell => { cell.hidden = !expanded; });
      const band=table.querySelector('[data-history-band="all"]');
      band.dataset.historyBaseSpan=String(3+(expanded?2:0)+(table.querySelector('[data-plan-opening-toggle]')?.getAttribute('aria-expanded')==='true'?3:0));
      band.colSpan += expanded ? 2 : -2;
      table.querySelectorAll('[data-plan-empty]').forEach(cell=>{cell.colSpan+=expanded?2:-2;});
      return;
    }
    const opening = event.target.closest('[data-plan-opening-toggle]');
    if (opening && table.contains(opening)) {
      const expanded = opening.getAttribute('aria-expanded') !== 'true';
      opening.setAttribute('aria-expanded', String(expanded));
      opening.querySelector('[data-plan-opening-arrow]').textContent = expanded ? '▾' : '▸';
      table.querySelectorAll('[data-plan-opening-component]').forEach(cell => { cell.hidden = !expanded; });
      const band = table.querySelector('[data-history-band="all"]');
      band.dataset.historyBaseSpan = String(3+(expanded?3:0)+(table.querySelector('[data-plan-closing-toggle]')?.getAttribute('aria-expanded')==='true'?2:0));
      band.colSpan += expanded ? 3 : -3;
      table.querySelectorAll('[data-plan-empty]').forEach(cell => { cell.colSpan += expanded ? 3 : -3; });
      return;
    }
    const button = event.target.closest('[data-plan-group-toggle]');
    if (!button || !table.contains(button)) return;
    const expanded = button.getAttribute('aria-expanded') !== 'true';
    button.setAttribute('aria-expanded', String(expanded));
    button.querySelector('[data-plan-group-arrow]').textContent = expanded ? '▾' : '▸';
    syncTree();
  });
})();
