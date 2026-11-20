(() => {
 const pager=document.querySelector('[data-brand-pages]');if(!pager)return;
 // Links remain ordinary links so the shared unsaved-change guard can save
 // the current page before navigating to another brand or workflow step.
 document.querySelectorAll('.split-workspace a[href]').forEach(link=>{
  const url=new URL(link.href,location.href);
  if(url.origin===location.origin&&url.pathname===location.pathname&&url.searchParams.has('tab')&&!url.searchParams.has('brand_page')&&!url.searchParams.has('export')&&!url.searchParams.has('group')&&!url.searchParams.has('sales_view')){
   url.searchParams.set('brand_page',pager.dataset.brandPages);link.href=url.pathname+url.search+url.hash;
  }
 });
})();
