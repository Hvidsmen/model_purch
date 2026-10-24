(() => {
  const root = document.getElementById("snapshot-job");
  if (!root) return;
  const form = document.getElementById("snapshot-run");
  let pending = false;
  const dates = (value) =>
    value
      ? new Date(value).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })
      : "—";
  const names = {
    never: "Обновлений ещё не было",
    running: "Выполняется",
    completed: "Успешно завершено",
    error: "Ошибка обновления",
    interrupted: "Обновление прервано",
  };
  function render(data) {
    const run = data.run,
      snapshot = data.snapshot;
    document.getElementById("snapshot-state").textContent =
      names[run.state] || run.state;
    const elapsed = run.started_at
      ? Math.max(
          0,
          Math.floor(
            ((run.completed_at ? new Date(run.completed_at) : new Date()) -
              new Date(run.started_at)) /
              1000,
          ),
        )
      : 0;
    document.getElementById("snapshot-progress").textContent =
      run.state === "never"
        ? "Запустите первое обновление данных."
        : `${run.stage || ""} · Прочитано строк: ${run.rows || 0} · Время: ${elapsed} сек.`;
    document.getElementById("snapshot-last").textContent = snapshot
      ? `Последний успешный снимок: ${dates(snapshot.loaded_at)} МСК · ${snapshot.rows} строк · Данные до ${snapshot.end_exclusive} (не включая).`
      : "Успешного снимка пока нет.";
    const error = document.getElementById("snapshot-error");
    error.hidden = !["error", "interrupted"].includes(run.state);
    error.textContent = run.error || "";
    if (form)
      form.querySelector("button").disabled =
        run.state === "running" || pending;
    const history = document.getElementById("snapshot-history");
    history.replaceChildren();
    data.history.forEach((item) => {
      const tr = document.createElement("tr");
      [
        dates(item.started_at),
        dates(item.completed_at),
        names[item.state] || item.state,
        item.rows || 0,
      ].forEach((value) => {
        const td = document.createElement("td");
        td.textContent = value;
        tr.append(td);
      });
      history.append(tr);
    });
  }
  async function poll() {
    try {
      const response = await fetch(root.dataset.status, {
        headers: { Accept: "application/json" },
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Статус недоступен.");
      render(data);
    } catch (error) {
      document.getElementById("snapshot-state").textContent =
        "Не удалось проверить статус. Повторная проверка — через 3 секунды.";
    }
  }
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    pending = true;
    form.querySelector("button").disabled = true;
    try {
      const response = await fetch(location.href, {
        method: "POST",
        body: new FormData(form),
        headers: { Accept: "application/json" },
      });
      const data = await response.json();
      if (!response.ok)
        throw new Error(data.error || "Не удалось запустить обновление.");
      await poll();
    } catch (error) {
      document.getElementById("snapshot-state").textContent = error.message;
    } finally {
      pending = false;
      await poll();
    }
  });
  poll();
  setInterval(poll, 3000);
})();
