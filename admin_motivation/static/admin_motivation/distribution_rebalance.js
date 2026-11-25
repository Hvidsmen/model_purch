/* Rebalance each politician fund independently, using integer cents. */
(() => {
  'use strict';
  const cents = value => Math.round(Number(String(value).replace(/\s/g, '').replace(',', '.')) * 100);
  const money = value => (value / 100).toLocaleString('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  document.querySelectorAll('[data-fund-kind="Политики"]').forEach(card => {
    const budget = cents(card.dataset.fundAmount);
    const inputs = Array.from(document.querySelectorAll('input[data-rebalance-kind="Политики"]'))
      .filter(input => input.dataset.rebalancePayment === card.dataset.fundPayment);
    // Preserve the existing editor for historical negative adjustments.
    if (!inputs.length || !Number.isSafeInteger(budget) || budget < 0 ||
        inputs.some(input => !Number.isSafeInteger(cents(input.value)) || cents(input.value) < 0)) return;
    const members = inputs.map(input => {
      const wrapper = document.createElement('div');
      wrapper.className = 'pd-slider';
      const range = document.createElement('input');
      range.type = 'range'; range.min = '0'; range.max = String(budget); range.step = '1';
      range.value = String(cents(input.value));
      range.setAttribute('aria-label', input.getAttribute('aria-label'));
      const output = document.createElement('output');
      const hint = document.createElement('small');
      input.before(wrapper); wrapper.append(output, range, hint, input);
      input.type = 'hidden';
      return {input, range, output, hint, value: cents(input.value)};
    });
    const remaining = () => budget - members.reduce((sum, member) => sum + member.value, 0);
    const refresh = () => {
      const rest = remaining();
      card.querySelector('[data-allocated]').textContent = money(budget - rest);
      card.querySelector('[data-remaining]').textContent = money(rest);
      card.dataset.unallocated = String(rest !== 0);
      members.forEach(member => {
        member.range.value = String(member.value);
        member.input.value = (member.value / 100).toFixed(2);
        member.output.textContent = money(member.value);
        member.range.setAttribute('aria-valuetext', money(member.value));
        member.hint.textContent = rest > 0 ? `Можно добавить до ${money(rest)}` :
          'Сначала уменьшите другую премию';
      });
    };
    members.forEach(member => member.range.addEventListener('input', () => {
      const ceiling = member.value + Math.max(0, remaining());
      member.value = Math.max(0, Math.min(Number(member.range.value), ceiling));
      refresh();
    }));
    refresh();
  });
})();
