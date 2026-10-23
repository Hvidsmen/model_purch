(() => {
  const root = document.querySelector('#version-panel .version-tools');
  if (!root) return;
  const tools = [...root.querySelectorAll('.version-tool')];
  for (const tool of tools) {
    tool.addEventListener('toggle', () => {
      if (tool.open) tools.filter(other => other !== tool).forEach(other => { other.open = false; });
    });
  }
  document.addEventListener('click', event => {
    if (!root.contains(event.target)) tools.forEach(tool => { tool.open = false; });
  });
  root.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const opened = tools.find(tool => tool.open);
    if (opened) { opened.open = false; opened.querySelector('summary').focus(); }
  });
})();
