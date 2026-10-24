(() => {
  "use strict";
  const root = document.getElementById("dealer-dashboard");
  if (!root) return;
  const form = document.getElementById("dashboard-filters"),
    result = document.getElementById("dashboard-result"),
    status = document.getElementById("dashboard-status"),
    error = document.getElementById("dashboard-error");
  const fmt = (value) =>
    value === null || value === undefined
      ? "—"
      : new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(
          Number(value),
        );
  const pct = (value) =>
    value === null || value === undefined
      ? "—"
      : fmt(Number(value) * 100) + "%";
  const colors = {
    S: "#aab7d2",
    M: "#70b9b5",
    L: "#8e84ce",
    XL: "#5e4db4",
    C: "#aab7d2",
    B: "#70b9b5",
    A: "#5e4db4",
    "Нет продаж": "#d6dce6",
    Новые: "#58a789",
    Вернулись: "#d1a15d",
    Ушли: "#bb8496",
    "S + M": "#70b9b5",
  };
  const node = (tag, text, cls) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  };
  const svgNode = (tag, attrs, text) => {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v));
    if (text !== undefined) el.textContent = text;
    return el;
  };
  const clear = (id) => {
    const el = document.getElementById(id);
    el.replaceChildren();
    return el;
  };
  const empty = (id) =>
    clear(id).append(
      node("p", "Нет данных по выбранным условиям.", "dash-empty"),
    );
  let report = null,
    controller = null,
    requestNumber = 0;
  const initial = new URLSearchParams(location.search);
  for (const field of ["method", "scope", "measure", "period_mode"])
    if (
      initial.has(field) &&
      [...form.elements[field].options].some(
        (o) => o.value === initial.get(field),
      )
    )
      form.elements[field].value = initial.get(field);
  function selected(name) {
    return [...form.elements[name].selectedOptions]
      .map((o) => o.value)
      .filter(Boolean);
  }
  function params() {
    const data = new URLSearchParams(new FormData(form));
    if (!report) {
      for (const name of [
        "branch",
        "brand",
        "equipment",
        "start",
        "end",
        "dealer",
        "cohort",
        "collapse_low",
        "search",
      ]) {
        data.delete(name);
        initial.getAll(name).forEach((v) => data.append(name, v));
      }
    }
    return data;
  }
  function fill(name, values, current) {
    const select = form.elements[name];
    select.replaceChildren();
    values.forEach((item) => {
      const value =
          typeof item === "object" ? String(item.value) : String(item),
        label = typeof item === "object" ? item.label : String(item);
      const option = new Option(label, value);
      option.selected = current.includes(value);
      select.add(option);
    });
  }
  function table(id, headers, rows) {
    const wrap = clear(id),
      scroll = node("div", undefined, "dash-table-scroll"),
      t = node("table", undefined, "dash-table"),
      head = node("thead"),
      tr = node("tr");
    headers.forEach((h) => tr.append(node("th", h)));
    head.append(tr);
    t.append(head);
    const body = node("tbody");
    rows.forEach((row) => {
      const r = node("tr");
      row.forEach((value) => {
        const cell = node("td");
        if (value instanceof Node) cell.append(value);
        else cell.textContent = String(value ?? "—");
        r.append(cell);
      });
      body.append(r);
    });
    t.append(body);
    scroll.append(t);
    wrap.append(scroll);
    if (!rows.length) wrap.append(node("p", "Нет данных.", "dash-empty"));
    return body;
  }
  function legend(target, groups) {
    const row = node("div", undefined, "dash-legend");
    groups.forEach((group) => {
      const span = node("span"),
        dot = node("i");
      dot.style.background = colors[group] || "#8e84ce";
      span.append(dot, document.createTextNode(group));
      row.append(span);
    });
    target.append(row);
  }
  function bars(id, rows, groups, share) {
    const wrap = clear(id);
    legend(wrap, groups);
    const chart = node("div", undefined, "dash-bars");
    const max = Math.max(
      ...rows.map((row) =>
        row.groups.reduce((sum, g) => sum + Math.max(0, Number(g.value)), 0),
      ),
      1,
    );
    rows.forEach((row) => {
      const r = node("div", undefined, "dash-bar-row"),
        track = node("div", undefined, "dash-bar-track"),
        total = row.groups.reduce(
          (sum, g) => sum + Math.max(0, Number(g.value)),
          0,
        );
      r.append(node("span", row.label));
      row.groups.forEach((g) => {
        const val = Math.max(0, Number(g.value)),
          part = node("button", undefined, "dash-bar-part");
        part.type = "button";
        part.style.background = colors[g.group] || "#8e84ce";
        part.style.width = (100 * val) / (share ? total || 1 : max) + "%";
        part.title =
          g.group +
          ": " +
          fmt(g.value) +
          (share ? " · " + pct(val / (total || 1)) : "");
        part.setAttribute("aria-label", row.label + " · " + part.title);
        if (val / (total || 1) > 0.12)
          part.textContent = share ? pct(val / (total || 1)) : fmt(val);
        part.addEventListener("click", () => {
          form.elements.cohort.value = g.group;
          if (row.branch)
            [...form.elements.branch.options].forEach(
              (option) => (option.selected = option.value === row.branch),
            );
          activate("flows");
          load();
        });
        track.append(part);
      });
      r.append(track, node("span", fmt(row.total ?? total)));
      chart.append(r);
    });
    wrap.append(chart);
  }
  function activate(tab) {
    document.querySelectorAll("[data-tab]").forEach((button) => {
      const yes = button.dataset.tab === tab;
      button.setAttribute("aria-selected", String(yes));
      button.tabIndex = yes ? 0 : -1;
    });
    document
      .querySelectorAll("[data-panel]")
      .forEach((panel) => (panel.hidden = panel.dataset.panel !== tab));
    const url = new URL(location.href);
    url.hash = tab;
    history.replaceState(null, "", url);
  }
  function sankey(data) {
    const wrap = clear("sankey-chart");
    if (!data.flows.length) {
      empty("sankey-chart");
      return;
    }
    const width = 1000,
      height = 360,
      svg = svgNode("svg", {
        viewBox: `0 0 ${width} ${height}`,
        role: "img",
        "aria-label": "Перетоки дилеров между выбранными периодами",
      }),
      sources = [...new Set(data.flows.map((f) => f.source))],
      targets = [...new Set(data.flows.map((f) => f.target))];
    const all = data.flows.reduce((sum, f) => sum + f.count, 0),
      scale =
        (height - 40 - 12 * Math.max(sources.length, targets.length)) /
        (all || 1);
    function positions(names, field) {
      let offset = 20;
      const map = {};
      names.forEach((name) => {
        const count = data.flows
          .filter((f) => f[field] === name)
          .reduce((sum, f) => sum + f.count, 0);
        map[name] = { y: offset, size: count * scale, used: 0, count };
        offset += count * scale + 12;
      });
      return map;
    }
    const left = positions(sources, "source"),
      right = positions(targets, "target");
    data.flows.forEach((flow) => {
      const a = left[flow.source],
        b = right[flow.target],
        size = flow.count * scale,
        ay = a.y + a.used,
        by = b.y + b.used;
      a.used += size;
      b.used += size;
      const path = svgNode("path", {
        d: `M 160 ${ay} C 450 ${ay},550 ${by},840 ${by} L 840 ${by + size} C 550 ${by + size},450 ${ay + size},160 ${ay + size} Z`,
        fill: colors[flow.source] || "#8e84ce",
        opacity: ".4",
        tabindex: "0",
        role: "button",
        "aria-label": `${flow.source} → ${flow.target}: ${flow.count} дилеров`,
      });
      path.append(
        svgNode(
          "title",
          {},
          `${flow.source} → ${flow.target}: ${flow.count} дилеров; ${fmt(flow.quantity)} шт.`,
        ),
      );
      const highlight = () => {
        document
          .querySelectorAll("#transition-matrix td")
          .forEach((cell) =>
            cell.classList.toggle(
              "highlight",
              cell.dataset.source === flow.source &&
                cell.dataset.target === flow.target,
            ),
          );
      };
      path.addEventListener("click", highlight);
      path.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          highlight();
        }
      });
      svg.append(path);
    });
    [
      [left, 140, 5],
      [right, 840, 865],
    ].forEach(([map, x, tx]) =>
      Object.entries(map).forEach(([name, pos]) => {
        svg.append(
          svgNode("rect", {
            x,
            y: pos.y,
            width: 20,
            height: Math.max(pos.size, 1),
            rx: 2,
            fill: colors[name] || "#8e84ce",
          }),
        );
        svg.append(
          svgNode(
            "text",
            { x: tx, y: pos.y + pos.size / 2 + 4 },
            `${name} · ${fmt(pos.count)}`,
          ),
        );
      }),
    );
    wrap.append(svg);
  }
  function waterfall(data) {
    const wrap = clear("bridge-chart"),
      items = [
        {
          label: String(data.start),
          value: Number(data.bridge.start),
          total: true,
        },
        ...data.bridge.items.map((i) => ({ ...i, value: Number(i.value) })),
        {
          label: String(data.end),
          value: Number(data.bridge.end),
          total: true,
        },
      ];
    let running = Number(data.bridge.start);
    const segments = items.map((item, index) => {
      const from = item.total ? 0 : running,
        to = item.total ? item.value : running + item.value;
      if (!item.total) running = to;
      return { ...item, from, to, index };
    });
    const values = segments.flatMap((i) => [i.from, i.to]),
      min = Math.min(0, ...values),
      max = Math.max(1, ...values),
      scale = 230 / (max - min),
      y = (v) => 260 - (v - min) * scale,
      width = Math.max(800, items.length * 120),
      step = (width - 80) / items.length,
      svg = svgNode("svg", {
        viewBox: `0 0 ${width} 330`,
        class: "dash-waterfall",
        role: "img",
        "aria-label": "Мост изменения продаж",
      });
    svg.append(
      svgNode("line", {
        x1: 30,
        x2: width - 20,
        y1: y(0),
        y2: y(0),
        stroke: "#cdd5e2",
      }),
    );
    segments.forEach((item) => {
      const x = 45 + item.index * step,
        w = Math.min(step - 18, 85),
        rect = svgNode("rect", {
          x,
          y: Math.min(y(item.from), y(item.to)),
          width: w,
          height: Math.max(1, Math.abs(y(item.to) - y(item.from))),
          rx: 3,
          fill: item.total
            ? "#6554c0"
            : item.value >= 0
              ? "#61aa94"
              : "#c07f92",
        });
      rect.append(svgNode("title", {}, `${item.label}: ${fmt(item.value)}`));
      svg.append(
        rect,
        svgNode(
          "text",
          {
            x: x + w / 2,
            y: Math.min(y(item.from), y(item.to)) - 8,
            "text-anchor": "middle",
          },
          fmt(item.value),
        ),
      );
      const label = svgNode("text", {
        x: x + w / 2,
        y: 285,
        "text-anchor": "middle",
      });
      item.label
        .split(" ")
        .reduce(
          (lines, word) => {
            const last = lines[lines.length - 1];
            if (last.length + word.length > 15) lines.push(word);
            else lines[lines.length - 1] += (last ? " " : "") + word;
            return lines;
          },
          [""],
        )
        .forEach((line, index) =>
          label.append(
            svgNode("tspan", { x: x + w / 2, dy: index ? 14 : 0 }, line),
          ),
        );
      svg.append(label);
    });
    const scroll = node("div", undefined, "dash-table-scroll");
    scroll.append(svg);
    wrap.append(scroll);
  }
  function dealerPlot(data) {
    const wrap = clear("dealer-volume-chart"),
      values = data.dealer.periods.map((p) => Number(p.quantity)),
      max = Math.max(300, ...values),
      min = Math.min(0, ...values),
      scale = 220 / (max - min),
      y = (v) => 250 - (v - min) * scale,
      width = 900,
      step = 750 / Math.max(1, values.length - 1),
      svg = svgNode("svg", {
        viewBox: `0 0 ${width} 300`,
        role: "img",
        "aria-label": "Штуки по годам и границы групп размера",
      });
    svg.style.width = "100%";
    [10, 50, 250].forEach((value) => {
      svg.append(
        svgNode("line", {
          x1: 60,
          x2: 840,
          y1: y(value),
          y2: y(value),
          stroke: "#cdd5e2",
          "stroke-dasharray": "5 4",
        }),
        svgNode(
          "text",
          { x: 5, y: y(value) + 4, "font-size": 11 },
          value + " шт.",
        ),
      );
    });
    svg.append(
      svgNode("polyline", {
        points: values.map((v, i) => 60 + i * step + "," + y(v)).join(" "),
        fill: "none",
        stroke: "#6554c0",
        "stroke-width": 3,
      }),
    );
    values.forEach((value, i) => {
      const dot = svgNode("circle", {
        cx: 60 + i * step,
        cy: y(value),
        r: 5,
        fill: "#6554c0",
      });
      dot.append(
        svgNode(
          "title",
          {},
          data.dealer.periods[i].label + ": " + fmt(value) + " шт.",
        ),
      );
      svg.append(
        dot,
        svgNode(
          "text",
          {
            x: 60 + i * step,
            y: 282,
            "text-anchor": "middle",
            "font-size": 11,
          },
          data.dealer.periods[i].label,
        ),
      );
    });
    wrap.append(svg);
  }
  function render(data, query) {
    const current = {};
    ["branch", "brand", "equipment"].forEach((name) => {
      current[name] = query.getAll(name);
      fill(name, data.filters[name], current[name]);
    });
    ["start", "end"].forEach((name) =>
      fill(
        name,
        data.periods.map((p) => ({ value: p.year, label: p.label })),
        [String(data[name])],
      ),
    );
    fill(
      "cohort",
      [{ value: "", label: "Все группы" }, ...data.groups],
      [query.get("cohort") || ""],
    );
    fill(
      "dealer",
      [
        { value: "", label: "Выберите дилера" },
        ...data.dealer_options.map((d) => ({
          value: d.code,
          label: d.name + " (" + d.code + ")",
        })),
      ],
      [query.get("dealer") || ""],
    );
    form.elements.collapse_low.checked = query.get("collapse_low") === "1";
    const rules = clear("dashboard-rules");
    Object.values(data.rules).forEach((rule) => rules.append(node("p", rule)));
    const latest =
        data.overview.find((period) => period.year === data.end) ||
        data.overview[data.overview.length - 1],
      kpis = clear("dashboard-kpis");
    [
      ["Активные дилеры", fmt(latest.dealers), "Чистое количество > 0"],
      ["Штуки", fmt(latest.quantity), latest.label],
      [
        "Верхняя группа " + data.groups.at(-1),
        fmt(latest.top),
        "Число дилеров",
      ],
      [
        "Удержание верхней группы 2023",
        pct(latest.retention_2023),
        "В той же верхней группе сейчас",
      ],
    ].forEach(([label, value, note]) => {
      const card = node("div", undefined, "dash-kpi");
      card.append(
        node("span", label),
        node("strong", value),
        node("small", note),
      );
      kpis.append(card);
    });
    bars(
      "dealer-count-chart",
      data.overview.map((p) => ({
        label: p.label,
        groups: p.groups.map((g) => ({ group: g.group, value: g.count })),
      })),
      data.groups,
      false,
    );
    bars(
      "dealer-share-chart",
      data.overview.map((p) => ({ label: p.label, groups: p.groups })),
      data.groups,
      true,
    );
    table(
      "overview-table",
      [
        "Период",
        "Начало",
        "Конец, не включая",
        "Дилеров",
        "Штуки",
        data.amount_label,
      ],
      data.overview.map((p) => [
        p.label,
        p.start,
        p.end,
        fmt(p.dealers),
        fmt(p.quantity),
        fmt(p.amount),
      ]),
    );
    sankey(data);
    const sources = [...new Set(data.flows.map((f) => f.source))],
      targets = [...new Set(data.flows.map((f) => f.target))];
    const matrix = table(
      "transition-matrix",
      ["Из / в", ...targets],
      sources.map((source) => [
        source,
        ...targets.map((target) => {
          const flow = data.flows.find(
              (f) => f.source === source && f.target === target,
            ),
            cell = node("span", flow ? fmt(flow.count) + " дил." : "—");
          if (flow) {
            cell.append(node("small", fmt(flow.quantity) + " шт."));
            cell.title = data.amount_label + ": " + fmt(flow.amount);
          }
          return cell;
        }),
      ]),
    );
    [...matrix.children].forEach((row, i) =>
      [...row.children].slice(1).forEach((cell, j) => {
        cell.dataset.source = sources[i];
        cell.dataset.target = targets[j];
      }),
    );
    table(
      "trajectory-table",
      [
        "Траектория",
        "Дилеров",
        ...data.trajectory_periods.map((y) => "Штуки · " + y),
      ],
      data.trajectories.map((t) => [
        t.path.join(" → "),
        fmt(t.count),
        ...t.values.map(fmt),
      ]),
    );
    waterfall(data);
    document.getElementById("bridge-check").textContent =
      (data.bridge.balanced ? "✓ Мост сходится. " : "⚠ Мост не сошёлся. ") +
      fmt(data.bridge.start) +
      " → " +
      fmt(data.bridge.end) +
      "; изменение " +
      fmt(data.bridge.difference) +
      (data.measure === "quantity" ? " шт." : "");
    const bridgeGroups = [
      ...data.groups,
      ...(data.bridge.items.some((item) =>
        Object.hasOwn(item.groups, "Нет продаж"),
      )
        ? ["Нет продаж"]
        : []),
    ];
    table(
      "bridge-table",
      ["Причина", "Изменение", "Дилеров", ...bridgeGroups],
      data.bridge.items.map((i) => [
        i.label,
        fmt(i.value),
        fmt(i.count),
        ...bridgeGroups.map((g) => fmt(i.groups[g] || 0)),
      ]),
    );
    table(
      "cohort-heatmap",
      ["Стартовая группа", "Размер когорты", ...data.trajectory_periods],
      data.cohorts.map((c) => [
        c.group,
        fmt(c.size),
        ...c.periods.map((p) => {
          const cell = node("span", pct(p.retained));
          cell.append(node("small", fmt(p.quantity) + " шт."));
          cell.style.display = "block";
          cell.style.background =
            p.retained === null
              ? "#f4f6fa"
              : `rgba(101,84,192,${0.08 + 0.4 * Number(p.retained)})`;
          cell.style.padding = "9px";
          return cell;
        }),
      ]),
    );
    table(
      "origins-table",
      ["Группа", "Происхождение", "Дилеров"],
      data.origins.flatMap((g) =>
        Object.entries(g.origins).map(([name, count]) => [
          g.group,
          name,
          fmt(count),
        ]),
      ),
    );
    table(
      "arrivals-table",
      ["Год прихода", "Пришло", "Активны в конце", "В верхней группе"],
      data.arrivals
        .filter((c) => c.year <= data.end)
        .map((c) => [c.year, fmt(c.size), fmt(c.alive), fmt(c.top)]),
    );
    bars(
      "branches-chart",
      data.branches.map((b) => ({
        label: b.branch,
        branch: b.branch,
        total: b.total,
        groups: b.groups.map((g) => ({ group: g.group, value: g.share })),
      })),
      data.groups,
      true,
    );
    table(
      "branch-heatmap",
      [
        "Филиал",
        "Чистый объём",
        "Доля топ-5",
        ...data.groups.map((g) => g + " · доля / Δ п.п."),
      ],
      data.branches.map((b) => [
        b.branch,
        fmt(b.total),
        pct(b.top5_share),
        ...b.groups.map((g) => {
          const span = node("span", pct(g.share));
          span.append(
            node(
              "small",
              (Number(g.delta) > 0 ? "+" : "") +
                fmt(Number(g.delta) * 100) +
                " п.п.",
            ),
          );
          span.style.display = "block";
          span.style.background = `rgba(101,84,192,${0.04 + 0.4 * Math.max(0, Math.min(1, Number(g.share)))})`;
          span.style.padding = "8px";
          return span;
        }),
      ]),
    );
    document.getElementById("dealer-search-count").textContent =
      `Найдено дилеров: ${fmt(data.dealer_matches)}. В списке первые ${data.dealer_options.length}. Уточните поиск, если нужного дилера нет.`;
    const card = clear("dealer-card");
    if (!data.dealer) {
      card.append(
        node("p", "Выберите дилера, чтобы увидеть его историю.", "dash-empty"),
      );
    } else {
      card.append(
        node(
          "h3",
          data.dealer.names.join(" / ") + " (" + data.dealer.code + ")",
        ),
        node("p", data.dealer.locations.join(" · ")),
      );
      const plot = node("div");
      plot.id = "dealer-volume-chart";
      card.append(plot);
      dealerPlot(data);
      const timeline = node("div");
      timeline.id = "dealer-timeline";
      card.append(timeline);
      table(
        "dealer-timeline",
        ["Период", "Штуки", data.amount_label, "Группы и статус"],
        data.dealer.periods.map((p) => [
          p.label,
          fmt(p.quantity),
          fmt(p.amount),
          p.groups
            .map((g) => `${g.branch}: ${g.group} · ${g.status}`)
            .join("; "),
        ]),
      );
      const breakdown = node("div");
      breakdown.id = "dealer-breakdown";
      card.append(breakdown);
      table(
        "dealer-breakdown",
        [
          "Разрез",
          "Значение",
          data.measure === "quantity" ? "Штуки" : data.amount_label,
        ],
        [
          ...data.dealer.equipment.map((i) => [
            "Оборудование",
            i.label,
            fmt(i.value),
          ]),
          ...data.dealer.brands.map((i) => ["Бренд", i.label, fmt(i.value)]),
          ...data.dealer.branches.map((i) => ["Филиал", i.label, fmt(i.value)]),
        ],
      );
    }
    const exported = new URLSearchParams(query);
    exported.set("start", data.start);
    exported.set("end", data.end);
    document.getElementById("dashboard-export").href =
      root.dataset.export + "?" + exported;
    document
      .getElementById("dashboard-export")
      .setAttribute("aria-disabled", "false");
    result.hidden = false;
    status.textContent = `Обновлено ${new Date(data.loaded_at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })} МСК · ${fmt(data.row_count)} месячных строк · ${data.scope === "branch" ? "дилер учитывается отдельно в каждом филиале" : "единый код дилера по компании"}${data.empty ? " · Активных дилеров по фильтрам нет" : ""}`;
  }
  async function load(extra) {
    controller?.abort();
    controller = new AbortController();
    const seq = ++requestNumber;
    const query = params();
    if (extra)
      Object.entries(extra).forEach(([key, value]) => query.set(key, value));
    else if (report?.search) query.set("search", report.search);
    error.hidden = true;
    result.hidden = true;
    status.textContent =
      "Загружаем данные и пересчитываем группы… Первый запрос может занять до двух минут.";
    root.setAttribute("aria-busy", "true");
    document
      .getElementById("dashboard-export")
      .setAttribute("aria-disabled", "true");
    try {
      const response = await fetch(root.dataset.source + "?" + query, {
        signal: controller.signal,
        headers: { Accept: "application/json" },
      });
      const data = await response.json();
      if (!response.ok)
        throw new Error(data.error || "Не удалось загрузить отчёт.");
      if (seq !== requestNumber) return;
      if (!data.periods?.length) {
        status.textContent = "Нет завершённых периодов с 2021 года.";
        return;
      }
      render(data, query);
      report = data;
      report.search = query.get("search") || "";
      const url = new URL(location.href);
      url.search = query.toString();
      history.replaceState(null, "", url);
    } catch (reason) {
      if (reason.name === "AbortError") return;
      status.textContent = "Данные не загружены.";
      error.querySelector("span").textContent =
        reason.message || "Ошибка подключения. Повторите запрос.";
      error.hidden = false;
    } finally {
      if (seq === requestNumber) root.setAttribute("aria-busy", "false");
    }
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    load();
  });
  document
    .getElementById("dealer-search")
    .addEventListener("submit", (event) => {
      event.preventDefault();
      load({ search: event.target.elements.search.value });
    });
  document.getElementById("dashboard-reset").addEventListener("click", () => {
    form.reset();
    ["branch", "brand", "equipment"].forEach((name) =>
      [...form.elements[name].options].forEach((o) => (o.selected = false)),
    );
    form.elements.start.value = "2021";
    form.elements.end.value = report?.periods.at(-1)?.year || "";
    form.elements.dealer.value = "";
    form.elements.cohort.value = "";
    load({ search: "" });
  });
  document
    .getElementById("dashboard-retry")
    .addEventListener("click", () => load());
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => activate(button.dataset.tab));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowRight", "ArrowLeft"].includes(event.key)) return;
      event.preventDefault();
      const buttons = [...document.querySelectorAll("[data-tab]")],
        index = buttons.indexOf(button),
        next =
          buttons[
            (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) %
              buttons.length
          ];
      activate(next.dataset.tab);
      next.focus();
    });
  });
  activate(
    [...document.querySelectorAll("[data-tab]")].some(
      (b) => "#" + b.dataset.tab === location.hash,
    )
      ? location.hash.slice(1)
      : "overview",
  );
  load();
})();
