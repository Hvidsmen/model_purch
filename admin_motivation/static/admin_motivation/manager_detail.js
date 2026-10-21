(() => {
 const root = document.getElementById('manager-detail-report');
 if (!root) return;
 const panel = document.getElementById('manager-detail-panel');
 const cards = document.querySelectorAll('[data-manager-open]');
 const syncCards = () => cards.forEach(card => card.setAttribute('aria-expanded', String(panel.open && card.dataset.managerOpen === panel.dataset.currency)));
 cards.forEach(card => card.addEventListener('click', () => {
  if (card.dataset.managerOpen === panel.dataset.currency) {
   panel.open = true;
   syncCards();
   panel.scrollIntoView({behavior: 'smooth', block: 'start'});
  } else {
   const form = document.getElementById('native-report-filters');
   form.elements.currency.value = card.dataset.managerOpen;
   let flag = form.querySelector('input[name=details]');
   if (!flag) { flag = document.createElement('input'); flag.type = 'hidden'; flag.name = 'details'; form.appendChild(flag); }
   flag.value = '1';
   form.requestSubmit(form.querySelector('button[name=run]'));
  }
 }));
 panel.addEventListener('toggle', syncCards);
 syncCards();
 const nodes = JSON.parse(document.getElementById('manager-detail-tree').textContent);
 const target = document.getElementById('manager-detail-nodes');
 const extra = document.getElementById('manager-detail-extra');
 const expanded = {segments: false, motivation: false};
 const periodToggles = [...document.querySelectorAll('[data-manager-period-toggle]')];
 const hiddenPeriods = new Set();
 const money = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0});
 const percent = new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2});
 function create(node) {
  const parent = document.createElement(node.children.length ? 'details' : 'div');
  parent.className = node.children.length ? 'native-division-node' : 'native-division-leaf';
  const row = node.children.length ? document.createElement('summary') : parent;
  row.classList.add('division-report-grid');
  row.style.setProperty('--level', node.level);
  const label = document.createElement('span'); label.className = 'native-label'; label.textContent = node.label; row.appendChild(label);
  node.values.forEach((block, blockIndex) => block.forEach((value, index) => {
   const cell = document.createElement('span'); cell.className = 'report-number';
   cell.dataset.managerPeriod = blockIndex;
   cell.classList.toggle('manager-period-hidden', hiddenPeriods.has(blockIndex));
   cell.textContent = value == null ? '—' : index < 5 || index === 12 || index === 16 ? percent.format(Number(value) * 100) + '%' : money.format(Number(value));
   if (index === 12 || index === 16) { if (value != null) cell.classList.add(Number(value) >= .8 ? 'percent-good' : 'percent-low'); }
   if (index < 5) { cell.dataset.managerExtra = ''; cell.hidden = !extra.checked; }
   else if (index < 10) { cell.dataset.managerSegments = ''; cell.hidden = !expanded.segments; }
   else if (index === 14 || index === 15) { cell.dataset.managerMotivation = ''; cell.hidden = !expanded.motivation; }
   if (index === 11 || index === 16) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'division-metric-toggle';
    button.dataset.managerReveal = index === 11 ? 'segments' : 'motivation';
    button.setAttribute('aria-expanded', String(expanded[button.dataset.managerReveal]));
    button.textContent = cell.textContent; cell.textContent = ''; cell.appendChild(button);
   }
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
 root.querySelectorAll('.division-report-grid').forEach(row => {
  [...row.children].slice(1).forEach((cell, index) => {
   cell.dataset.managerPeriod = row.classList.contains('report-period-header') ? index : Math.floor(index / 20);
  });
 });
 periodToggles.forEach(toggle => toggle.addEventListener('change', () => {
  const index = Number(toggle.dataset.managerPeriodToggle);
  if (toggle.checked) hiddenPeriods.delete(index); else hiddenPeriods.add(index);
  root.querySelectorAll('[data-manager-period="' + index + '"]').forEach(cell => cell.classList.toggle('manager-period-hidden', !toggle.checked));
  root.style.setProperty('--report-blocks', periodToggles.length - hiddenPeriods.size + 1);
 }));
 nodes.forEach(node => target.appendChild(create(node)));
 const expandAll = () => {
  const expand = element => { element._load?.(); element.open = true; [...element.children].forEach(child => child.querySelectorAll(':scope > details').forEach(expand)); };
  target.querySelectorAll(':scope > details').forEach(expand);
 };
 expandAll();
 document.getElementById('manager-detail-expand').addEventListener('click', expandAll);
 document.getElementById('manager-detail-collapse').addEventListener('click', () => target.querySelectorAll('details').forEach(element => element.open = false));
 const updateColumns = () => root.style.setProperty('--report-columns', 8 + (extra.checked ? 5 : 0) + (expanded.segments ? 5 : 0) + (expanded.motivation ? 2 : 0));
 root.addEventListener('click', event => {
  const button = event.target.closest('[data-manager-reveal]');
  if (!button) return;
  event.preventDefault(); event.stopPropagation();
  const kind = button.dataset.managerReveal; expanded[kind] = !expanded[kind];
  root.querySelectorAll('[data-manager-' + kind + ']').forEach(cell => cell.hidden = !expanded[kind]);
  root.querySelectorAll('[data-manager-reveal="' + kind + '"]').forEach(item => item.setAttribute('aria-expanded', String(expanded[kind])));
  updateColumns();
 });
 extra.addEventListener('change', () => {
  updateColumns();
  root.querySelectorAll('[data-manager-extra]').forEach(cell => cell.hidden = !extra.checked);
 });
})();
