/*
 * День 18: планировщик.
 * Страница ничего не запускает по расписанию — это делает cron. Она только
 * показывает состояние и раз в 15 секунд спрашивает его заново: новая
 * сводка, пришедшая по расписанию, появляется во входящих сама и
 * подсвечивается. Кнопки делают шаги тика вручную.
 */
(() => {
  const urls = document.querySelector("#day18-urls");
  if (!urls) return;

  const collectButton = document.querySelector("#day18-collect");
  const summaryButton = document.querySelector("#day18-summary");
  const clearButton = document.querySelector("#day18-clear");
  const status = document.querySelector("#day18-status");
  const inboxBox = document.querySelector("[data-inbox]");
  const inboxMeta = document.querySelector("[data-inbox-meta]");
  const chartBox = document.querySelector("[data-chart]");
  const chartMeta = document.querySelector("[data-chart-meta]");
  const ticksBox = document.querySelector("[data-ticks]");
  const cronState = document.querySelector("[data-cron-state]");
  const cronLine = document.querySelector("[data-cron-line]");
  const cronInstall = document.querySelector("[data-cron-install]");
  const cronUninstall = document.querySelector("[data-cron-uninstall]");
  const schedule = document.querySelector("[data-schedule]");

  const POLL_MS = 15000;
  const SVG = "http://www.w3.org/2000/svg";
  const baseTitle = document.title;

  const FRESH_MS = 120000;
  const freshUntil = new Map();

  let seen = null;
  let unread = 0;
  let busy = false;

  /* ── Мелочи ────────────────────────────────────────────────────────── */

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const svg = (tag, attrs) => {
    const node = document.createElementNS(SVG, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  };

  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const rub = (value) => `${Number(value).toFixed(3).replace(".", ",")} ₽`;
  const ago = (ts, now) => {
    const minutes = Math.max(0, Math.round((now - ts) / 60));
    return minutes === 0 ? "только что" : `${minutes} мин назад`;
  };

  const fold = (title, text) => {
    const details = el("details", "day18-fold");
    details.append(el("summary", "", title));
    details.append(el("pre", "", text));
    return details;
  };

  /* ── Расписание ────────────────────────────────────────────────────── */

  const renderSchedule = (state) => {
    const cron = state.cron;
    schedule.classList.toggle("is-on", cron.installed);
    if (cron.error) {
      cronState.textContent = `crontab не прочитан: ${cron.error}`;
    } else if (cron.installed) {
      cronState.textContent = `● включено: тик раз в ${cron.every} мин, сводка раз в ${state.summary_every} мин`;
    } else {
      cronState.textContent = "○ выключено — тики не идут, пока не поставите строку в crontab";
    }
    cronLine.textContent = cron.line || "строки дня 18 в crontab нет";
    cronInstall.textContent = cron.install;
    cronUninstall.textContent = cron.uninstall;

    const lastTick = state.ticks[0];
    document.querySelector('[data-fact="last_tick"]').textContent = lastTick
      ? `${clock(lastTick.ts)} (${ago(lastTick.ts, state.now)}) · ${lastTick.trigger === "cron" ? "cron" : "вручную"} · ${lastTick.ok ? rub(lastTick.price) : "ошибка"}`
      : "ещё не было";
    document.querySelector('[data-fact="next_summary"]').textContent = state.next_summary_ts
      ? `≈ ${clock(state.next_summary_ts)} — придёт сама`
      : cron.installed
        ? "после первого тика"
        : "только вручную — расписание выключено";
    document.querySelector('[data-fact="snapshots"]').textContent = String(state.snapshots_total);
  };

  /* ── Входящие ──────────────────────────────────────────────────────── */

  const renderInbox = (summaries) => {
    // Новая — та, которой не было при прошлой отрисовке. Подсветка держится
    // две минуты, иначе следующий опрос её сразу бы снял.
    const nowMs = Date.now();
    if (seen !== null) {
      const arrived = summaries.filter((item) => !seen.has(item.id));
      arrived.forEach((item) => freshUntil.set(item.id, nowMs + FRESH_MS));
      if (arrived.length && document.hidden) unread += arrived.length;
    }
    seen = new Set(summaries.map((item) => item.id));
    const fresh = new Set([...freshUntil].filter(([, until]) => until > nowMs).map(([id]) => id));
    document.title = unread ? `(${unread}) ${baseTitle}` : baseTitle;

    inboxBox.replaceChildren();
    inboxMeta.textContent = summaries.length ? `${summaries.length} · новые сверху` : "";
    if (!summaries.length) {
      inboxBox.append(el("li", "day18-inbox__empty", "Сводок пока нет. Они придут сами по расписанию — или нажмите «Сводка сейчас»."));
      return;
    }

    for (const item of summaries) {
      const card = el("li", `day18-letter${item.ok ? "" : " is-error"}${fresh.has(item.id) ? " is-fresh" : ""}`);
      const head = el("div", "day18-letter__head");
      head.append(el("strong", "", clock(item.ts)));
      head.append(el("span", "day18-letter__period", `сводка за ${item.minutes} мин`));
      head.append(el("span", `day18-letter__trigger${item.trigger === "cron" ? " is-cron" : ""}`, item.trigger === "cron" ? "по расписанию" : "вручную"));
      if (fresh.has(item.id)) head.append(el("span", "day18-letter__new", "новая"));
      card.append(head);

      const body = el("div", "day18-letter__body md-body");
      if (typeof window.renderMarkdown === "function") body.innerHTML = window.renderMarkdown(item.text);
      else body.textContent = item.text;
      card.append(body);

      const stats = item.stats || {};
      const calls = item.calls || [];
      const meta = [
        stats.llm_calls !== undefined ? `запросов к модели ${stats.llm_calls}` : null,
        calls.length ? `вызовы: ${calls.map((call) => `${call.name}(${JSON.stringify(call.arguments)})`).join(", ")}` : "без вызовов",
        stats.prompt_tokens !== undefined ? `вход ${stats.prompt_tokens} ток.` : null,
      ].filter(Boolean);
      card.append(el("p", "day18-letter__meta", meta.join(" · ")));
      calls.forEach((call) => card.append(fold(`что вернул ${call.name}`, call.preview || "")));
      inboxBox.append(card);
    }
  };

  /* ── График ────────────────────────────────────────────────────────── */

  const renderChart = (chart) => {
    chartBox.replaceChildren();
    const points = chart.points;
    const official = chart.official;
    chartMeta.textContent = official ? `ЦБ на ${official.date}: ${rub(official.value)}` : "";

    if (points.length < 2) {
      chartBox.append(el("p", "day18-chart__empty", points.length ? `Один снимок: ${rub(points[0].price)}. Для линии нужно два.` : "Снимков за час нет."));
      return;
    }

    const width = 320;
    const height = 120;
    const pad = { left: 8, right: 8, top: 14, bottom: 16 };
    const prices = points.map((point) => point.price);
    let low = Math.min(...prices);
    let high = Math.max(...prices);
    if (high - low < 0.01) {
      low -= 0.01;
      high += 0.01;
    }
    const first = points[0].ts;
    const span = Math.max(1, points[points.length - 1].ts - first);
    const x = (ts) => pad.left + ((ts - first) / span) * (width - pad.left - pad.right);
    const y = (price) => pad.top + (1 - (price - low) / (high - low)) * (height - pad.top - pad.bottom);

    const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Курс доллара за последний час" });
    const line = points.map((point, index) => `${index ? "L" : "M"}${x(point.ts).toFixed(1)} ${y(point.price).toFixed(1)}`).join(" ");
    root.append(svg("path", { d: line, class: "day18-chart__line" }));

    const lastPoint = points[points.length - 1];
    root.append(svg("circle", { cx: x(lastPoint.ts), cy: y(lastPoint.price), r: 2.5, class: "day18-chart__dot" }));

    const label = (text, px, py, anchor) => {
      const node = svg("text", { x: px, y: py, "text-anchor": anchor, class: "day18-chart__label" });
      node.textContent = text;
      root.append(node);
    };
    label(`макс ${rub(Math.max(...prices))}`, pad.left, 10, "start");
    label(`мин ${rub(Math.min(...prices))}`, pad.left, height - 4, "start");
    label(clock(first), width - pad.right, height - 4, "end");
    label(`сейчас ${rub(lastPoint.price)}`, width - pad.right, 10, "end");

    chartBox.append(root);
  };

  /* ── Журнал тиков ──────────────────────────────────────────────────── */

  const renderTicks = (ticks) => {
    ticksBox.replaceChildren();
    if (!ticks.length) {
      ticksBox.append(el("li", "day18-ticks__empty", "Тиков ещё не было."));
      return;
    }
    for (const tick of ticks) {
      const item = el("li", `day18-tick${tick.ok ? "" : " is-error"}`);
      item.append(el("span", "day18-tick__time", clock(tick.ts)));
      item.append(el("span", "day18-tick__trigger", tick.trigger === "cron" ? "cron" : "вручную"));
      item.append(el("span", "day18-tick__value", tick.ok ? rub(tick.price) : tick.error || "ошибка"));
      item.append(el("span", "day18-tick__ms", `${tick.ms} мс`));
      if (tick.summary_id) item.append(el("span", "day18-tick__summary", "→ сводка"));
      ticksBox.append(item);
    }
  };

  const render = (state) => {
    renderSchedule(state);
    renderInbox(state.summaries);
    renderChart(state.chart);
    renderTicks(state.ticks);
  };

  /* ── Опрос и кнопки ────────────────────────────────────────────────── */

  const refresh = async () => {
    if (busy) return;
    try {
      const response = await fetch(urls.dataset.stateUrl);
      if (response.ok) render(await response.json());
    } catch {
      // Сервер лаборатории перезапускается — спросим в следующий раз.
    }
  };

  const act = async (button, url, body, working, done) => {
    if (busy) return;
    busy = true;
    [collectButton, summaryButton, clearButton].forEach((item) => (item.disabled = true));
    status.textContent = working;
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      const data = await response.json().catch(() => ({}));
      if (data.state) {
        busy = false;
        render(data.state);
      }
      status.textContent = response.ok ? done(data) : data.error || data.tick?.error || data.summary?.text || `HTTP ${response.status}`;
    } catch (error) {
      status.textContent = error.message;
    } finally {
      busy = false;
      [collectButton, summaryButton, clearButton].forEach((item) => (item.disabled = false));
    }
  };

  collectButton.addEventListener("click", () =>
    act(collectButton, urls.dataset.collectUrl, null, "MCP: collect_rate…", (data) => `снят курс ${rub(data.tick.price)} за ${data.tick.ms} мс`),
  );
  summaryButton.addEventListener("click", () =>
    act(summaryButton, urls.dataset.summaryUrl, { minutes: 10 }, "агент пишет сводку за 10 минут…", () => "сводка во входящих"),
  );
  clearButton.addEventListener("click", () =>
    act(clearButton, urls.dataset.clearUrl, null, "стираю…", (data) => `стёрто сводок: ${data.removed}`),
  );

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      unread = 0;
      document.title = baseTitle;
      refresh();
    }
  });

  try {
    render(JSON.parse(document.querySelector("#day18-initial").textContent));
  } catch {
    // Слепок не разобрался — первый опрос всё нарисует.
  }
  setInterval(refresh, POLL_MS);
})();
