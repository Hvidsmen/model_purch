(() => {
 const review = document.getElementById('merge-review');
 if (!review) return;
 document.querySelectorAll('.merge-form select').forEach(select => select.addEventListener('change', () => {
  review.hidden = true;
  const token = review.querySelector('[name=preview_token]');
  if (token) token.value = '';
  document.getElementById('merge-review-status').textContent = 'Параметры изменились. Проверьте объединение повторно.';
 }));
})();
