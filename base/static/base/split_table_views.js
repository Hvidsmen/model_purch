(() => {
  const panel = document.querySelector('[data-table-view-controls]');
  if (!panel) return;
  const plan = document.querySelector('.split-plan-matrix');
  const table = plan || document.querySelector('.split-stock-table, [data-month-grid] table');
  if (!table) return;
  const storage = 'split-table-view-' + panel.dataset.tableViewControls;
  const references = JSON.parse(document.getElementById('split-reference-values')?.textContent || '{}');
  let mode = 'nested';
  try { mode = sessionStorage.getItem(storage) || mode; } catch (_) {}
  const levels = [['sales_plan_group','Группа планов продаж'],['brand','Марка'],['outdoor_type','Тип наружного блока'],['factory','Завод']];
  const nodes = [];
  const originals = [...table.tBodies[0].children];
  if (!plan) {
    const root = {children:new Map(), leaves:[]};
    originals.forEach(row => {
      const gid = row.dataset.stockGroup || row.dataset.gid;
      if (!gid) return;
      let owner = root;
      levels.forEach(([field,label], depth) => {
        const value = references[gid]?.[field] || 'Не указано';
        if (!owner.children.has(value)) owner.children.set(value, {children:new Map(), leaves:[], label, value, depth, descendants:[]});
        owner = owner.children.get(value);
        owner.descendants.push(row);
      });
      owner.leaves.push({row, children: originals.filter(child => child.dataset.stockChild === gid)});
    });
    const body = table.tBodies[0];
    function emit(owner) {
      [...owner.children.values()].sort((a,b) => a.value.localeCompare(b.value,'ru')).forEach(node => {
        const row = document.createElement('tr'); row.className = 'split-view-section';
        const cell = document.createElement('th'); cell.scope = 'row';
        cell.style.paddingLeft = (12 + node.depth * 16) + 'px';
        const button = document.createElement('button'); button.type = 'button'; button.className = 'split-stock-toggle';
        button.setAttribute('aria-expanded','true');
        node.row = row; node.cell = cell; node.button = button; node.open = true;
        button.addEventListener('click', () => { node.open = !node.open; sync(); });
        cell.append(button); row.append(cell); body.append(row); nodes.push(node);
        emit(node);
      });
      owner.leaves.forEach(({row, children}) => { body.append(row); children.forEach(child => body.append(child)); });
    }
    emit(root);
  }
  let characteristics = true;
  const referenceCells = [...table.querySelectorAll('.split-distribution-reference, .split-reference-heading')];
  if (plan) table.querySelectorAll('thead tr:first-child, [data-plan-group], [data-plan-power-group], .split-matrix-grand-total').forEach(row => referenceCells.push(...[...row.cells].slice(0,4)));
  if (!plan && table.classList.contains('split-stock-table')) table.querySelectorAll('.split-stock-power-row, tfoot tr').forEach(row => referenceCells.push(...[...row.cells].slice(0,4)));
  panel.querySelector('[data-reference-columns]').addEventListener('click', event => {
    characteristics = !characteristics;
    event.currentTarget.setAttribute('aria-pressed',String(characteristics));
    referenceCells.forEach(cell => {cell.hidden=!characteristics;});
    table.querySelectorAll('.split-plan-subtotal > th:first-child').forEach(cell => {cell.colSpan=characteristics ? 5 : 1;});
    table.querySelectorAll('[data-plan-empty], [data-stock-fullspan]').forEach(cell => {cell.colSpan += characteristics ? 4 : -4;});
    sync();
  });
  function sync() {
    const nested = mode === 'nested';
    table.dataset.tableView = mode;
  panel.querySelectorAll('[data-table-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.tableView === mode)));
    panel.querySelectorAll('[data-view-expand], [data-view-collapse]').forEach(button => { button.hidden = !nested; });
    if (plan) {
      document.dispatchEvent(new CustomEvent('split-table-view-change', {detail:{mode}}));
    } else {
      const leaves = [...table.querySelectorAll('[data-stock-group], [data-grid-row]')];
      leaves.forEach(row => {
        const excluded = nested && nodes.some(node => !node.open && node.descendants.includes(row));
        row.toggleAttribute('data-view-excluded', excluded);
        const gid = row.dataset.stockGroup;
        if (gid) table.querySelectorAll('[data-stock-child]').forEach(child => {
          if (child.dataset.stockChild === gid) child.toggleAttribute('data-view-excluded', excluded);
        });
      });
      const width = [...table.tHead.rows[0].cells].filter(cell => !cell.hidden).reduce((sum,cell) => sum + cell.colSpan, 0);
      nodes.forEach(node => {
        const visible = node.descendants.filter(row => !row.hasAttribute('data-reference-excluded'));
        const gids = new Set(visible.map(row => row.dataset.stockGroup || row.dataset.gid));
        const ancestorClosed = nodes.some(parent => parent.depth < node.depth && !parent.open && parent.descendants.includes(node.descendants[0]));
        node.row.hidden = !nested || !visible.length || ancestorClosed;
        node.cell.colSpan = width;
        node.button.setAttribute('aria-expanded', String(node.open));
        node.button.textContent = (node.open ? '▾ ' : '▸ ') + node.label + ': ' + node.value + ' · групп: ' + gids.size;
      });
    }
    document.dispatchEvent(new Event('split-table-visibility-change'));
  }
  panel.querySelectorAll('[data-table-view]').forEach(button => button.addEventListener('click', () => {
    mode = button.dataset.tableView;
    try { sessionStorage.setItem(storage, mode); } catch (_) {}
    sync();
  }));
  panel.querySelectorAll('[data-view-expand], [data-view-collapse]').forEach(button => button.addEventListener('click', () => {
    if (plan) document.querySelector(button.hasAttribute('data-view-expand') ? '[data-plan-tree-expand]' : '[data-plan-tree-collapse]').click();
    else { nodes.forEach(node => { node.open = button.hasAttribute('data-view-expand'); }); sync(); }
  }));
  document.addEventListener('split-reveal-group',event=>{
    if(plan)return;
    const row=[...table.querySelectorAll('[data-stock-group], [data-grid-row]')].find(row=>(row.dataset.stockGroup||row.dataset.gid)===event.detail.gid);
    if(!row)return;
    nodes.filter(node=>node.descendants.includes(row)).forEach(node=>{node.open=true;});sync();
  });
  document.addEventListener('split-reference-filter-change', sync);
  if (!plan) new MutationObserver(sync).observe(table.tHead, {subtree:true, attributes:true, attributeFilter:['colspan','hidden']});
  sync();
})();
