(() => {
  const panel = document.getElementById('version-panel');
  const toolbar = panel?.querySelector('.version-tools');
  if (!toolbar) return;
  const tools = [...toolbar.querySelectorAll('.version-tool')];
  const reopen = panel.querySelector('#version-reopen');
  if (reopen) {
    const content = document.createElement('div');
    content.className = 'version-tool-content';
    [...reopen.children].filter(child => child.tagName !== 'SUMMARY').forEach(child => content.append(child));
    reopen.append(content); reopen.classList.add('version-tool'); tools.push(reopen);
  }
  const panels = document.createElement('div');
  panels.className = 'version-panels';
  panel.querySelector('.version-controls').after(panels);
  const entries = tools.map(tool => {
    const summary = tool.querySelector('summary');
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'btn btn-outline-primary';
    button.textContent = tool.id === 'version-reopen' ? 'Возврат в черновик' : summary.textContent;
    button.setAttribute('aria-controls', tool.id);
    button.setAttribute('aria-expanded', String(tool.open));
    button.addEventListener('click', () => {
      const open = !tool.open;
      tools.forEach(other => { other.open = other === tool && open; });
      sync();
    });
    tool.addEventListener('toggle', () => {
      if (tool.open) tools.filter(other => other !== tool).forEach(other => { other.open = false; });
      sync();
    });
    toolbar.append(button); panels.append(tool);
    return {tool, button};
  });
  function sync() { entries.forEach(({tool, button}) => button.setAttribute('aria-expanded', String(tool.open))); }
  panel.classList.add('version-tools-ready');
  panel.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const entry = entries.find(({tool}) => tool.open);
    if (entry) { entry.tool.open = false; sync(); entry.button.focus(); }
  });
})();
