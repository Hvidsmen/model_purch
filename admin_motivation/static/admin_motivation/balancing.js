(() => {
  const root = document.getElementById("coefficient-balancing");
  if (!root) return;
  const form = document.getElementById("balance-setup");
  if (!form?.elements.scope) return;
  const el = (id) => document.getElementById(`balance-${id}`);
  let cells = [],
    selections = new Map(),
    fingerprint = "",
    preview = null,
    editable = false,
    page = 0,
    loaded = null,
    busy = false;
  const pageSize = 80;
  const money = (n) =>
    (Math.abs(Number(n)) < 0.005 ? 0 : Number(n)).toLocaleString("ru-RU", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  const percent = (n) =>
    Number((Number(n) * 100).toFixed(10)).toLocaleString("ru-RU", {
      maximumFractionDigits: 10,
      useGrouping: false,
    });
  const setup = () => Object.fromEntries(new FormData(form));
  function scopeNote() {
    const local = form.elements.scope.value === "local",
      company = form.elements.mode.value === "company";
    form.elements.mode.querySelector('[value="subdivision"]').disabled = !local;
    if (!local && form.elements.mode.value === "subdivision")
      form.elements.mode.value = "type";
    form.elements.subdivision.disabled = !local || company;
    form.elements.subdivision.closest("label").hidden = !local || company;
    el("branch").closest("label").hidden = !local;
    el("branch").disabled = !local;
    if (!local) el("branch").value = "";
    el("summary-scope").textContent = local ? "Подразделение" : "Область";
    el("scope-note").textContent = !local
      ? "Балансируются глобальные ставки по плану компании."
      : company
        ? "Будут загружены коэффициенты всех подразделений. Компенсацию можно распределить между выбранными подразделениями."
        : "Будут загружены коэффициенты выбранного подразделения.";
  }
  function invalidate() {
    preview = null;
    el("cells")
      .querySelectorAll(".balance-cell-after")
      .forEach((node) => node.remove());
    el("total").textContent =
      "Изменения ещё не рассчитаны. Выполните балансировку.";
    el("summary").replaceChildren();
    el("save").disabled = true;
    el("save-note").textContent =
      "Параметры изменились. Выполните предварительный расчёт перед сохранением.";
  }
  function status(text) {
    el("status").textContent = text;
  }
  async function request(data) {
    if (busy) return;
    busy = true;
    root.setAttribute("aria-busy", "true");
    const controls = [...root.querySelectorAll("button,input,select")].map(
      (control) => [control, control.disabled],
    );
    controls.forEach(([control]) => (control.disabled = true));
    el("error").hidden = true;
    try {
      const response = await fetch(root.dataset.endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-CSRFToken": form.elements.csrfmiddlewaretoken.value,
        },
        body: JSON.stringify(data),
      });
      const payload = await response.json();
      if (!response.ok)
        throw new Error(payload.error || "Не удалось выполнить действие.");
      return payload;
    } catch (error) {
      el("error").textContent = error.message;
      el("error").hidden = false;
      throw error;
    } finally {
      controls.forEach(([control, disabled]) => (control.disabled = disabled));
      busy = false;
      root.removeAttribute("aria-busy");
    }
  }
  const choice = (cell) =>
    selections.get(cell.id) || { role: "fixed", value: percent(cell.value) };
  function filtered() {
    const query = el("search").value.toLocaleLowerCase().trim(),
      kind = el("kind").value,
      branch = el("branch").value,
      show = el("show").value;
    return cells.filter(
      (cell) =>
        (!kind || cell.kind === kind) &&
        (!branch || cell.subdivision === branch) &&
        (!query ||
          [
            cell.goods,
            cell.planning_group,
            cell.group,
            cell.brand,
            cell.employee,
            cell.subdivision,
          ]
            .join(" ")
            .toLocaleLowerCase()
            .includes(query)) &&
        (show === "all" ||
          (show === "selected" && choice(cell).role !== "fixed") ||
          (show === "changed" && preview?.changes[cell.id])),
    );
  }
  function td(row, text) {
    const cell = document.createElement("td");
    cell.textContent = text;
    row.append(cell);
    return cell;
  }
  function render() {
    const visible = filtered();
    const groupKey = (cell) =>
      JSON.stringify([
        cell.subdivision,
        cell.goods,
        cell.employee_key || cell.employee,
      ]);
    const grouped = new Map();
    for (const cell of visible) {
      const key = groupKey(cell);
      if (!grouped.has(key))
        grouped.set(key, { sample: cell, cells: new Map() });
      grouped.get(key).cells.set(`${cell.kind}:${cell.segment}`, cell);
    }
    const groups = [...grouped.values()].sort((a, b) =>
      [
        a.sample.subdivision,
        a.sample.planning_group,
        a.sample.group,
        a.sample.brand,
        a.sample.employee,
      ]
        .join(" ")
        .localeCompare(
          [
            b.sample.subdivision,
            b.sample.planning_group,
            b.sample.group,
            b.sample.brand,
            b.sample.employee,
          ].join(" "),
          "ru",
        ),
    );
    const kinds = ["Политики", "Продажи", "Директор"].filter(
      (kind) =>
        (!el("kind").value || el("kind").value === kind) &&
        cells.some((cell) => cell.kind === kind),
    );
    const pages = Math.max(1, Math.ceil(groups.length / pageSize));
    page = Math.min(page, pages - 1);
    const table = el("cells"),
      head = table.querySelector("thead"),
      body = table.querySelector("tbody");
    head.replaceChildren();
    body.replaceChildren();
    const top = document.createElement("tr"),
      bottom = document.createElement("tr");
    for (const title of [
      "Группа планов продаж",
      "Группа ERP",
      loaded?.scope === "local" ? "Марка / подразделение" : "Марка / бренд",
    ]) {
      const th = document.createElement("th");
      th.textContent = title;
      th.rowSpan = 2;
      th.scope = "col";
      top.append(th);
    }
    for (const kind of kinds) {
      const th = document.createElement("th");
      th.textContent = kind;
      th.colSpan = 5;
      th.scope = "colgroup";
      th.dataset.kind = kind;
      top.append(th);
      for (let index = 0; index < 5; index++) {
        const step = document.createElement("th");
        step.textContent = `K${index}`;
        step.scope = "col";
        step.dataset.kind = kind;
        bottom.append(step);
      }
    }
    head.append(top, bottom);
    table.classList.toggle("show-budgets", el("show-budget").checked);
    for (const group of groups.slice(page * pageSize, (page + 1) * pageSize)) {
      const row = document.createElement("tr"),
        sample = group.sample;
      td(row, sample.planning_group);
      td(row, sample.group);
      const name = td(row, sample.brand),
        note = document.createElement("small");
      note.textContent = [sample.subdivision || "Глобально", sample.employee]
        .filter(Boolean)
        .join(" · ");
      name.append(note);
      for (const kind of kinds)
        for (let index = 0; index < 5; index++) {
          const cell = group.cells.get(`${kind}:K${index}`),
            target = td(row, "");
          if (!cell) {
            target.textContent = "—";
            continue;
          }
          const selected = choice(cell);
          target.dataset.participation = selected.role;
          target.className = "balance-matrix-cell";
          target.title = `${kind} / K${index} · План: ${money(cell.plan_amount)} USD · Премия до: ${money(cell.premium)} USD`;
          const before = document.createElement("small");
          before.className = "balance-cell-before";
          before.textContent = `Было: ${percent(cell.value)}%`;
          target.append(before);
          const role = document.createElement("select");
          role.setAttribute("aria-label", `Участие ${cell.id}`);
          for (const [value, label] of [
            ["fixed", "Не менять"],
            ["manual", "Ручное"],
            ["auto", "Авто"],
          ])
            role.add(new Option(label, value));
          role.value = selected.role;
          role.title =
            "Не менять / Ручное значение / Автоматическая компенсация";
          target.append(role);
          const input = document.createElement("input");
          input.type = "text";
          input.inputMode = "decimal";
          input.value = selected.value;
          input.disabled = selected.role !== "manual";
          input.setAttribute("aria-label", `Новый процент ${cell.id}`);
          target.append(input);
          if (preview?.changes[cell.id]) {
            const result = document.createElement("strong");
            result.className = "balance-cell-after";
            result.textContent = `→ ${percent(preview.changes[cell.id].after)}%`;
            target.append(result);
          }
          const budget = document.createElement("small");
          budget.className = "balance-cell-budget";
          budget.textContent = `План: ${money(cell.plan_amount)} USD · Премия: ${money(cell.premium)} USD`;
          target.append(budget);
          role.addEventListener("change", () => {
            selections.set(cell.id, { role: role.value, value: input.value });
            invalidate();
            render();
          });
          input.addEventListener("input", () => {
            selections.set(cell.id, { role: "manual", value: input.value });
            invalidate();
          });
        }
      body.append(row);
    }
    el("page").textContent =
      `Страница ${page + 1} из ${pages} · товарных строк: ${groups.length} · ячеек: ${visible.length}`;
    el("prev").disabled = page === 0;
    el("next").disabled = page >= pages - 1;
    const selected = [...selections.values()];
    el("count").textContent =
      `Ручных: ${selected.filter((x) => x.role === "manual").length} · Для компенсации: ${selected.filter((x) => x.role === "auto").length}`;
  }
  function summary(rows) {
    const body = el("summary");
    body.replaceChildren();
    const subdivisions = new Map();
    for (const item of rows) {
      if (!subdivisions.has(item.subdivision))
        subdivisions.set(item.subdivision, new Map());
      subdivisions.get(item.subdivision).set(item.kind, item);
    }
    for (const [subdivision, kinds] of subdivisions) {
      const row = document.createElement("tr");
      const label = document.createElement("th");
      label.scope = "row";
      label.textContent = subdivision || "Глобально";
      row.append(label);
      for (const kind of ["Политики", "Продажи", "Директор"]) {
        const item = kinds.get(kind);
        for (const field of ["before", "after", "delta"]) {
          const cell = td(row, item ? money(item[field]) : "—");
          if (field === "delta") {
            cell.classList.add("balance-delta-column");
            if (item && Math.abs(Number(item.delta)) >= 0.005)
              cell.classList.add("balance-delta-changed");
          }
        }
      }
      body.append(row);
    }
  }

  form.addEventListener("change", () => {
    scopeNote();
    invalidate();
    el("workspace").hidden = true;
    loaded = null;
    status("Настройки изменились. Загрузите исходные значения заново.");
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    if (
      selections.size &&
      !window.confirm(
        "Загрузить исходные значения заново? Несохранённая балансировка будет сброшена.",
      )
    )
      return;
    const params = setup();
    status("Загружаем план и коэффициенты…");
    try {
      const data = await request({ ...params, action: "load" });
      cells = data.cells;
      fingerprint = data.fingerprint;
      editable = data.editable;
      loaded = params;
      selections.clear();
      preview = null;
      page = 0;
      el("branch").replaceChildren(new Option("Все подразделения", ""));
      [...new Set(cells.map((cell) => cell.subdivision).filter(Boolean))]
        .sort()
        .forEach((value) => el("branch").add(new Option(value, value)));
      el("workspace").hidden = false;
      el("preview").disabled = false;
      el("save").disabled = true;
      summary(data.display_totals || data.totals);
      el("total").textContent =
        "Исходные значения загружены. Выберите ручные изменения и ячейки для компенсации.";
      render();
      status(
        `Загружено ячеек: ${cells.length}. Использований минимального коэффициента: ${data.fallbacks}.`,
      );
    } catch {
      status("Не удалось загрузить исходные значения.");
    }
  });
  ["search", "kind", "branch", "show"].forEach((id) =>
    el(id).addEventListener(id === "search" ? "input" : "change", () => {
      page = 0;
      render();
    }),
  );
  el("prev").onclick = () => {
    page--;
    render();
  };
  el("next").onclick = () => {
    page++;
    render();
  };
  el("select").onclick = () => {
    for (const cell of filtered())
      if (choice(cell).role !== "manual")
        selections.set(cell.id, { role: "auto", value: percent(cell.value) });
    invalidate();
    render();
  };
  el("clear").onclick = () => {
    for (const cell of filtered())
      if (choice(cell).role === "auto") selections.delete(cell.id);
    invalidate();
    render();
  };
  el("preview").onclick = async () => {
    if (busy || !loaded) return;
    const manual = {},
      compensate = [];
    for (const [key, value] of selections)
      if (value.role === "manual") manual[key] = `${value.value}%`;
      else if (value.role === "auto") compensate.push(key);
    invalidate();
    status("Пересчитываем коэффициенты и проверяем бюджет…");
    try {
      preview = await request({
        ...loaded,
        action: "preview",
        fingerprint,
        manual,
        compensate,
      });
      summary(preview.display_totals || preview.totals);
      el("total").textContent =
        `Было: ${money(preview.before)} USD → Стало: ${money(preview.after)} USD · Разница: ${money(preview.delta)} USD · Изменено ячеек: ${Object.keys(preview.changes).length}`;
      el("save").disabled = !editable;
      el("save-note").textContent =
        "Бюджет сохранён. Можно записать результат в черновик.";
      render();
      status("Балансировка рассчитана. Проверьте изменения перед сохранением.");
    } catch {
      status("Балансировка не выполнена. Коэффициенты не изменены.");
    }
  };
  el("save").onclick = async () => {
    if (busy || !preview) return;
    try {
      const data = await request({
        action: "save",
        token: preview.token,
      });
      el("save").disabled = true;
      preview = null;
      loaded = null;
      form.elements.version.value = String(data.version);
      el("preview").disabled = true;
      status(data.message);
      el("save-note").textContent =
        `Сохранено. Запись истории №${data.log}. Для следующей балансировки загрузите исходные значения заново.`;
    } catch {
      el("save").disabled = true;
      status("Сохранение не выполнено. Проверьте сообщение об ошибке.");
    }
  };
  el("show-budget").addEventListener("change", () =>
    el("cells").classList.toggle("show-budgets", el("show-budget").checked),
  );
  scopeNote();
})();
