(() => {
  const root = document.getElementById("coefficient-balancing");
  if (!root) return;
  const form = document.getElementById("balance-setup");
  const el = (id) => document.getElementById(`balance-${id}`);
  let cells = [],
    selections = new Map(),
    fingerprint = "",
    preview = null,
    editable = false,
    canCopy = false,
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
    el("scope-note").textContent = !local
      ? "Балансируются глобальные ставки по плану компании."
      : company
        ? "Будут загружены коэффициенты всех подразделений. Компенсацию можно распределить между выбранными подразделениями."
        : "Будут загружены коэффициенты выбранного подразделения.";
  }
  function invalidate() {
    preview = null;
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
    const visible = filtered(),
      pages = Math.max(1, Math.ceil(visible.length / pageSize));
    page = Math.min(page, pages - 1);
    const body = el("cells").querySelector("tbody");
    body.replaceChildren();
    for (const cell of visible.slice(page * pageSize, (page + 1) * pageSize)) {
      const selected = choice(cell),
        row = document.createElement("tr");
      row.dataset.participation = selected.role;
      const name = td(
        row,
        [cell.planning_group, cell.group, cell.brand].join(" · "),
      );
      const note = document.createElement("small");
      note.textContent = [cell.subdivision || "Глобально", cell.employee]
        .filter(Boolean)
        .join(" · ");
      name.append(note);
      td(row, `${cell.kind} / ${cell.segment}`);
      td(row, money(cell.plan_amount));
      td(row, money(cell.premium));
      td(row, percent(cell.value));
      const role = document.createElement("select");
      role.setAttribute("aria-label", `Участие ${cell.id}`);
      for (const [value, label] of [
        ["fixed", "Не менять"],
        ["manual", "Ручное"],
        ["auto", "Компенсация"],
      ])
        role.add(new Option(label, value));
      role.value = selected.role;
      td(row, "").append(role);
      const input = document.createElement("input");
      input.type = "text";
      input.inputMode = "decimal";
      input.value = selected.value;
      input.disabled = selected.role !== "manual";
      input.setAttribute("aria-label", `Новый процент ${cell.id}`);
      td(row, "").append(input);
      td(
        row,
        preview?.changes[cell.id]
          ? percent(preview.changes[cell.id].after)
          : "—",
      );
      role.addEventListener("change", () => {
        selections.set(cell.id, { role: role.value, value: input.value });
        invalidate();
        render();
      });
      input.addEventListener("input", () => {
        selections.set(cell.id, { role: "manual", value: input.value });
        invalidate();
      });
      body.append(row);
    }
    el("page").textContent =
      `Страница ${page + 1} из ${pages} · строк: ${visible.length}`;
    el("prev").disabled = page === 0;
    el("next").disabled = page >= pages - 1;
    const selected = [...selections.values()];
    el("count").textContent =
      `Ручных: ${selected.filter((x) => x.role === "manual").length} · Для компенсации: ${selected.filter((x) => x.role === "auto").length}`;
  }
  function summary(rows) {
    const body = el("summary");
    body.replaceChildren();
    for (const item of rows) {
      const row = document.createElement("tr");
      [
        item.subdivision,
        item.kind,
        money(item.before),
        money(item.after),
        money(item.delta),
      ].forEach((value) => td(row, value));
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
      canCopy = data.can_copy;
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
      summary(data.totals);
      el("total").textContent =
        "Исходные значения загружены. Выберите ручные изменения и ячейки для компенсации.";
      render();
      status(
        `Загружено ячеек: ${cells.length}. Использований минимального коэффициента: ${data.fallbacks}.${editable ? "" : " Версия доступна только для анализа. Для сохранения создайте черновик."}`,
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
      summary(preview.totals);
      el("total").textContent =
        `Было: ${money(preview.before)} USD → Стало: ${money(preview.after)} USD · Разница: ${money(preview.delta)} USD · Изменено ячеек: ${Object.keys(preview.changes).length}`;
      el("save").disabled = !(editable || canCopy);
      el("save").textContent = canCopy
        ? "Сохранить в новый черновик"
        : "Сохранить в черновик";
      el("save-note").textContent = editable
        ? "Бюджет сохранён. Можно записать результат в черновик."
        : canCopy
          ? "Будет создан новый черновик с той же датой начала действия. Утверждённая версия сохранится."
          : "Результат рассчитан для анализа. Для сохранения создайте черновик версии и повторите балансировку.";
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
        create_draft: canCopy,
      });
      el("save").disabled = true;
      preview = null;
      loaded = null;
      if (
        ![...form.elements.version.options].some(
          (option) => option.value === String(data.version),
        )
      )
        form.elements.version.add(
          new Option("Новый черновик · Балансировка", data.version),
        );
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
  scopeNote();
})();
