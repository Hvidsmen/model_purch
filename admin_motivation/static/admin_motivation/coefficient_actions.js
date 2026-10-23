/* Group existing forms without nesting them or changing their submit owners. */
(() => {
  const hub = document.getElementById('coefficient-action-hub');
  if (!hub) return;
  const buttons = hub.querySelector('[data-action-buttons]');
  const panels = hub.querySelector('[data-action-panels]');
  const entries = [];
  function addPanel(detail, label, id) {
    if (!detail) return;
    if (!detail.id) detail.id = id;
    // Import controls used to live inside the main coefficient form.
    for (const control of detail.querySelectorAll('input,select,textarea,button')) {
      const owner = control.form;
      if (owner && !detail.contains(owner)) {
        control.disabled = control.matches(':disabled');
        control.setAttribute('form', owner.id);
      }
    }
    detail.classList.add('coefficient-action-panel');
    panels.append(detail);
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'btn btn-outline-primary';
    button.textContent = label; button.setAttribute('aria-controls', detail.id);
    function sync() {
      button.setAttribute('aria-expanded', String(detail.open));
      button.classList.toggle('is-active', detail.open);
    }
    button.addEventListener('click', () => {
      const open = !detail.open;
      entries.forEach(entry => { entry.detail.open = false; });
      detail.open = open;
      entries.forEach(entry => entry.sync());
    });
    detail.addEventListener('toggle', sync);
    entries.push({detail, sync}); buttons.append(button); sync();
  }
  addPanel(document.getElementById('add-product-details'), 'Добавить товар', 'add-product-details');
  addPanel(document.querySelector('.bulk-formula'), 'Формула для товаров', 'bulk-formula-details');
  for (const selector of ['.coefficient-copy-between', '#globalBranchCopyForm', '#copy-global-open']) {
    const control = document.querySelector(selector);
    if (control) buttons.append(control);
  }
  addPanel(document.getElementById('coefficient-tools'), 'Импорт и справочники', 'coefficient-tools');
  hub.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    entries.forEach(entry => { entry.detail.open = false; entry.sync(); });
  });
})();
