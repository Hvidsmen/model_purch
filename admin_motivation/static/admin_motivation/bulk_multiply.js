(() => {
  const root = document.getElementById("bulk-multiply-controls");
  if (!root) return;
  const el = (id) => document.getElementById(`multiply-${id}`);
  const boxes = [...document.querySelectorAll(".bulk-good")];
  const dialog = el("review");
  let pending = [],
    undo = [];
  const count = () => {
    el("count").textContent =
      `Выбрано: ${boxes.filter((box) => box.checked).length}`;
  };
  boxes.forEach((box) => box.addEventListener("change", count));
  // Selection is shared with bulk formula actions, including their select/clear controls.
  document
    .querySelector("#bulk-variation-form [data-bulk-visible]")
    ?.addEventListener("click", count);
  document
    .querySelector("#bulk-variation-form [data-bulk-clear]")
    ?.addEventListener("click", count);
  for (const [id, selected] of [
    ["visible", true],
    ["clear", false],
  ])
    el(id).addEventListener("click", () => {
      boxes.forEach((box) => {
        if (!selected || !box.closest("tr").hidden) {
          box.checked = selected;
          box.dispatchEvent(new Event("change", { bubbles: true }));
        }
      });
      count();
    });
  el("mode").addEventListener("change", () => {
    const separate = el("mode").value === "segments";
    el("segments").hidden = !separate;
    el("common-label").hidden = separate;
  });
  const showError = (text) => {
    el("error").textContent = text;
    el("error").hidden = false;
  };
  const parse = (text, coefficient = false) => {
    const value = text.trim().replace(",", ".");
    if (
      !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?%?$/.test(value) ||
      (!coefficient && value.endsWith("%"))
    )
      throw new Error("Введите множитель числом, например 1,1.");
    const number =
      Number(value.replace(/%$/, "")) /
      (coefficient && value.endsWith("%") ? 100 : 1);
    if (!Number.isFinite(number) || (!coefficient && number < 0))
      throw new Error("Множитель должен быть конечным неотрицательным числом.");
    return number;
  };
  const percent = (value) => `${Number((value * 100).toFixed(10))}%`;
  const notify = (changes) => {
    changes[0]?.input.dispatchEvent(new Event("input", { bubbles: true }));
  };
  el("preview").addEventListener("click", () => {
    el("error").hidden = true;
    pending = [];
    try {
      const selected = boxes.filter((box) => box.checked);
      if (!selected.length)
        throw new Error("Сначала отметьте товары в первом столбце.");
      const kinds = [
        ...root.querySelectorAll("[data-multiply-kind]:checked:not(:disabled)"),
      ].map((box) => box.dataset.multiplyKind);
      if (!kinds.length)
        throw new Error(
          "Выберите хотя бы один тип: Политики, Продажи или Директор.",
        );
      const factors =
        el("mode").value === "common"
          ? Array(5).fill(parse(el("common").value))
          : [...root.querySelectorAll("[data-multiply-segment]")].map((input) =>
              parse(input.value),
            );
      let skipped = 0;
      for (const box of selected) {
        const row = box.closest("tr");
        if (row.querySelector('[name^="delete_coeff="]:checked')) {
          skipped++;
          continue;
        }
        for (const group of kinds)
          for (const cell of row.querySelectorAll(
            `[data-coefficient-group="${group}"]`,
          )) {
            const input = cell.querySelector(".coeff-input");
            if (!input || input.matches(":disabled")) {
              skipped++;
              continue;
            }
            const namedSegment = input.dataset.segment
              ?.trim()
              .match(/^[KКOО]([0-4])$/iu);
            const segment = input.dataset.segment
              ? namedSegment
                ? Number(namedSegment[1])
                : NaN
              : Number(cell.dataset.coefficientSegment);
            if (!Number.isInteger(segment) || segment < 0 || segment > 4)
              throw new Error(
                "Не удалось определить ступень коэффициента. Обновите страницу.",
              );
            const before = parse(input.value, true),
              raw = before * factors[segment];
            if (!Number.isFinite(raw) || Math.abs(raw) > 1 + 1e-12)
              throw new Error(
                `${row.dataset.brand}, ${group}, K${segment}: результат выходит за диапазон −100%…100%. Ни одна ячейка не изменена.`,
              );
            const after = Number(
              Math.max(-1, Math.min(1, raw)).toFixed(
                group === "directors" ? 6 : 12,
              ),
            );
            if (Math.abs(after - before) > 1e-14)
              pending.push({
                input,
                original: input.value,
                value: percent(after),
                before: percent(before),
                goods: [
                  row.dataset.pg,
                  row.dataset.group,
                  row.dataset.brand,
                ].join(" · "),
                kind:
                  {
                    policies: "Политики",
                    sales: "Продажи",
                    directors: "Директор",
                  }[group] + ` / K${segment}`,
              });
          }
      }
      if (!pending.length)
        throw new Error(
          "Нет изменяемых значений. Проверьте множители, выбранные типы и доступность ставок.",
        );
      el("review-summary").textContent =
        `Товаров выбрано: ${selected.length}. Изменится ячеек: ${pending.length}. Пропущено недоступных ячеек или удаляемых товаров: ${skipped}. Показаны первые 100 изменений.`;
      const body = el("review-rows");
      body.replaceChildren();
      for (const change of pending.slice(0, 100)) {
        const row = document.createElement("tr");
        for (const text of [
          change.goods,
          change.kind,
          change.before,
          change.value,
        ]) {
          const cell = document.createElement("td");
          cell.textContent = text;
          row.append(cell);
        }
        body.append(row);
      }
      dialog.showModal();
    } catch (error) {
      pending = [];
      showError(error.message);
    }
  });
  el("cancel").addEventListener("click", () => dialog.close());
  el("apply").addEventListener("click", () => {
    if (!pending.length) return;
    if (
      pending.some(
        (change) =>
          change.input.value !== change.original ||
          change.input.matches(":disabled"),
      )
    ) {
      dialog.close();
      showError(
        "Значения изменились после проверки. Проверьте умножение заново.",
      );
      return;
    }
    undo = pending;
    undo.forEach((change) => (change.input.value = change.value));
    pending = [];
    notify(undo);
    el("undo").disabled = false;
    el("status").textContent =
      `Изменено ячеек: ${undo.length}. Правки не сохранены — проверьте таблицу и нажмите «Сохранить изменения».`;
    dialog.close();
  });
  el("undo").addEventListener("click", () => {
    el("error").hidden = true;
    if (undo.some((change) => change.input.value !== change.value)) {
      showError(
        "После умножения значения менялись вручную. Автоматическая отмена недоступна, чтобы сохранить ваши правки.",
      );
      return;
    }
    undo.forEach((change) => (change.input.value = change.original));
    notify(undo);
    undo = [];
    el("undo").disabled = true;
    el("status").textContent =
      "Последнее умножение отменено. Предыдущие правки сохранены в таблице.";
  });
  count();
})();
