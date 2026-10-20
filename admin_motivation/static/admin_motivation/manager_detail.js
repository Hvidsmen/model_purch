(() => {
 const root = document.getElementById('manager-detail-report');
 if (!root) return;
 const nodes = JSON.parse(document.getElementById('manager-detail-tree').textContent);
 const target = document.getElementById('manager-detail-nodes');
 const extra = document.getElementById('manager-detail-extra');
 const money = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0});
 const percent = new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2});
 function create(node) {
  const parent = document.createElement(node.children.length ? 'details' : 'div');
  parent.className = node.children.length ? 'native-division-node' : 'native-division-leaf';
  const row = node.children.length ? document.createElement('summary') : parent;
  row.classList.add('division-report-grid');
  row.style.setProperty('--level', node.level);
  const label = document.createElement('span'); label.className = 'native-label'; label.textContent = node.label; row.appendChild(label);
  node.values.forEach(block => block.forEach((value, index) => {
   const cell = document.createElement('span'); cell.className = 'report-number';
   cell.textContent = value == null ? '—' : index < 5 || index === 12 || index === 16 ? percent.format(Number(value) * 100) + '%' : money.format(Number(value));
   if (index === 12 || index === 16) { if (value != null) cell.classList.add(Number(value) >= .8 ? 'percent-good' : 'percent-low'); }
   if (index < 13) { cell.dataset.managerExtra = ''; cell.hidden = !extra.checked; }
   row.appendChild(cell);
  }));
  if (node.children.length) {
   parent.appendChild(row); const children = document.createElement('div'); parent.appendChild(children);
   let loaded = false;
   parent._load = () => { if (!loaded) { loaded = true; node.children.forEach(child => children.appendChild(create(child))); } };
   parent.addEventListener('toggle', () => { if (parent.open) parent._load(); });
  }
  return parent;
 }
 nodes.forEach(node => target.appendChild(create(node)));
 document.getElementById('manager-detail-expand').addEventListener('click', () => {
  const expand = element => { element._load?.(); element.open = true; [...element.children].forEach(child => child.querySelectorAll(':scope > details').forEach(expand)); };
  target.querySelectorAll(':scope > details').forEach(expand);
 });
 document.getElementById('manager-detail-collapse').addEventListener('click', () => target.querySelectorAll('details').forEach(element => element.open = false));
 extra.addEventListener('change', () => {
  root.style.setProperty('--report-columns', extra.checked ? 20 : 7);
  root.querySelectorAll('[data-manager-extra]').forEach(cell => cell.hidden = !extra.checked);
 });
})();
