document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('i.bi').forEach(icon => icon.setAttribute('aria-hidden', 'true'));
    document.querySelectorAll('form[data-copy-target]').forEach(form => {
        const source = form.querySelector('[name="source_scenario_id"]');
        const direction = document.createElement('div');
        direction.className = 'copy-direction';
        direction.setAttribute('aria-live', 'polite');
        source.parentNode.insertBefore(direction, source);
        function update() {
            const selected = source.selectedOptions[0];
            direction.textContent = `Из: ${source.value && selected ? selected.textContent.trim() : 'выберите источник'} → В: ${form.dataset.copyTarget}`;
        }
        source.addEventListener('change', update);
        update();
        form.addEventListener('submit', () => {
            const button = form.querySelector('button[type="submit"]');
            if (button) { button.disabled = true; button.textContent = 'Копирование…'; }
        });
        window.addEventListener('pageshow', () => {
            const button = form.querySelector('button[type="submit"]');
            if (button && source.options.length > 1) button.disabled = false;
        });
    });
    document.querySelectorAll('a[title], button[title]').forEach(control => {
        if (!control.hasAttribute('aria-label')) control.setAttribute('aria-label', control.title);
    });
});
