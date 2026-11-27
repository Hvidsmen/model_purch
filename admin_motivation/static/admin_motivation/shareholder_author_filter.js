(() => {
 const payload = document.getElementById('shareholder-author-subdivisions');
 const form = document.getElementById('native-report-filters');
 if (!payload || !form) return;
 const affiliations = JSON.parse(payload.textContent);
 const branches = [...form.querySelectorAll('input[name=subdivision]')];
 const select = form.querySelector('select[name=author]');
 const checks = [...form.querySelectorAll('input[name=author]')];
 const update = () => {
  const selected = new Set(branches.filter(input => input.checked).map(input => input.value));
  const allowed = author => !selected.size || (affiliations[author] || []).some(branch => selected.has(branch));
  if (select) {
   const previous = select.value;
   const options = [new Option('Выберите сотрудника', '')];
   Object.keys(affiliations).filter(allowed).sort((a,b) => a.localeCompare(b,'ru')).forEach(author => options.push(new Option(author,author)));
   select.replaceChildren(...options);
   select.value = allowed(previous) ? previous : '';
   if (select.value !== previous) select.dispatchEvent(new Event('change', {bubbles:true}));
  }
  checks.forEach(input => {
   input.disabled = !allowed(input.value);
   const menu = input.closest('.next-products');
   const search = menu?.querySelector('[data-product-search]')?.value.trim().toLocaleLowerCase('ru') || '';
   input.closest('label').hidden = input.disabled || !input.value.toLocaleLowerCase('ru').includes(search);
   if (input.disabled && input.checked) {
    input.checked = false;
    input.dispatchEvent(new Event('change', {bubbles:true}));
   }
  });
 };
 branches.forEach(input => input.addEventListener('change', update));
 form.querySelectorAll('[data-next-select-all],[data-next-clear-all]').forEach(button => button.addEventListener('click', () => setTimeout(update, 0)));
 update();
})();


// Put the employee picker in the browser's top layer, outside clipping panels.
(() => {
 const details=document.querySelector('.nr-author-filter');
 if (!details) return;
 const menu=details.querySelector('.next-branch-options');
 const caption=details.querySelector('summary');
 const supported=typeof menu.showPopover==='function';
 if (supported) menu.setAttribute('popover','manual');
 const title=()=>{caption.title=caption.textContent.trim();};
 new MutationObserver(title).observe(caption,{childList:true,characterData:true,subtree:true});title();
 const position=()=>{
  if (!details.open || !supported) return;
  const rect=caption.getBoundingClientRect(),gap=6,pad=12;
  const width=Math.min(360,window.innerWidth-pad*2);
  const below=window.innerHeight-rect.bottom-pad-gap,above=rect.top-pad-gap;
  const up=below<200 && above>below;
  const available=Math.max(80,up?above:below);
  menu.style.width=width+'px';menu.style.maxHeight=Math.min(340,available)+'px';
  menu.style.left=Math.max(pad,Math.min(rect.left,window.innerWidth-width-pad))+'px';
  menu.style.top=(up?Math.max(pad,rect.top-gap-menu.offsetHeight):rect.bottom+gap)+'px';
 };
 details.addEventListener('toggle',()=>{
  if (!supported) return;
  if (details.open) {menu.showPopover();position();}
  else if (menu.matches(':popover-open')) menu.hidePopover();
 });
 window.addEventListener('resize',position);
 window.addEventListener('scroll',position,true);
 document.addEventListener('click',event=>{if (details.open&&!details.contains(event.target)) details.open=false;});
 details.addEventListener('keydown',event=>{if(event.key==='Escape'){details.open=false;caption.focus();event.preventDefault();}});
})();
