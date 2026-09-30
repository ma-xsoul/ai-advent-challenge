/*
 * День 20: оркестрация MCP.
 * Лента — как в днях 17 и 19, но у каждого вызова плашка сервера: модель
 * называет инструмент «сервер__инструмент», агент по префиксу отправляет
 * вызов туда. Справа — карточки серверов, маршрут последнего хода и сверка
 * кода: свой ли сервер, нужные ли серверы, перешли ли данные между ними.
 * Всё, что пришло от серверов и из файлов, — только textContent.
 */
(() => {
  const form = document.querySelector("#agent-form");
  if (!form) return;

  const messageField = document.querySelector("#agent-message");
  const submitButton = document.querySelector("#agent-submit");
  const resetButton = document.querySelector("#agent-reset");
  const scenarioButton = document.querySelector("#day20-scenario");
  const status = document.querySelector("#agent-status");
  const log = document.querySelector("#agent-log");
  const emptyNote = log.querySelector("[data-log-empty]");
  const turnTemplate = document.querySelector("#agent-turn-template");
  const serversMeta = document.querySelector("[data-servers-meta]");
  const routeBox = document.querySelector("[data-route]");
  const routeMeta = document.querySelector("[data-route-meta]");
  const checksBox = document.querySelector("[data-checks]");
  const reportsBox = document.querySelector("[data-reports]");
  const fileBox = document.querySelector("[data-file]");
  const initialScript = document.querySelector("#agent-initial");

  const urls = {
    chat: form.dataset.chatUrl,
    state: form.dataset.stateUrl,
    reset: form.dataset.resetUrl,
    servers: form.dataset.serversUrl,
  };

  let busy = false;
  let scenarioRunning = false;

  /* ── Прокрутка за лентой ───────────────────────────────────────────── */

  const FOLLOW_MARGIN = 90;
  let followEnabled = false;
  let lastAutoScroll = -1;

  const nearBottom = () => log.getBoundingClientRect().bottom - window.innerHeight < 160;

  const followBottom = () => {
    if (!followEnabled) return;
    const overflow = log.getBoundingClientRect().bottom - (window.innerHeight - FOLLOW_MARGIN);
    if (overflow <= 1) return;
    window.scrollBy({ top: overflow, behavior: "auto" });
    lastAutoScroll = Math.round(window.scrollY);
  };

  const syncFollow = () => {
    if (busy) followEnabled = nearBottom();
  };

  window.addEventListener(
    "scroll",
    () => {
      if (Math.abs(Math.round(window.scrollY) - lastAutoScroll) > 2) syncFollow();
    },
    { passive: true },
  );
  window.addEventListener("wheel", syncFollow, { passive: true });
  window.addEventListener("touchmove", syncFollow, { passive: true });

  /* ── Мелочи ────────────────────────────────────────────────────────── */

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const fold = (title, text) => {
    const details = el("details", "day20-fold");
    details.append(el("summary", "", title));
    details.append(el("pre", "", text));
    return details;
  };

  const compactArgs = (value) => (typeof value === "string" ? value : JSON.stringify(value));
  const seconds = (ms) => `${(ms / 1000).toFixed(1)} с`;

  const append = (node) => {
    emptyNote.hidden = true;
    log.append(node);
    followBottom();
    return node;
  };

  /* ── Лента ─────────────────────────────────────────────────────────── */

  const addTurn = (role, text, { restored = false } = {}) => {
    const turn = turnTemplate.content.firstElementChild.cloneNode(true);
    turn.dataset.turn = role;
    turn.querySelector("[data-turn-role]").textContent = role === "user" ? "вы" : "агент";
    if (restored) turn.classList.add("is-restored");
    const body = turn.querySelector("[data-turn-body]");
    if (role === "assistant" && typeof window.renderMarkdown === "function") body.innerHTML = window.renderMarkdown(text);
    else body.textContent = text;
    return append(turn);
  };

  const addMarker = (text, kind = "info") => append(el("p", `day20-marker day20-marker--${kind}`, text));

  // «github__search_repositories» → плашка сервера и имя инструмента.
  const SEPARATOR = "__";
  const splitName = (name, server) => {
    const index = name.indexOf(SEPARATOR);
    if (index < 0) return { server: server || null, tool: name };
    return { server: server || name.slice(0, index), tool: name.slice(index + SEPARATOR.length) };
  };
  const tag = (server) => el("span", `day20-tag day20-tag--${server || "none"}`, server || "нет сервера");

  const tools = (count) => {
    const tens = count % 100;
    const ones = count % 10;
    if (tens >= 11 && tens <= 14) return `${count} инструментов`;
    if (ones === 1) return `${count} инструмент`;
    if (ones >= 2 && ones <= 4) return `${count} инструмента`;
    return `${count} инструментов`;
  };

  const addCall = (call) => {
    const card = el("div", "day20-call is-pending");
    card.dataset.callId = call.id;

    const head = el("div", "day20-call__head");
    const { server, tool } = splitName(call.name, call.server);
    head.append(el("span", "day20-call__arrow", "→"));
    head.append(tag(server));
    head.append(el("strong", "day20-call__name", tool));
    head.append(el("code", "day20-call__args", compactArgs(call.arguments)));
    card.append(head);

    const result = el("div", "day20-call__result");
    result.append(el("span", "day20-call__arrow", "←"));
    result.append(el("span", "day20-call__status", "вызываю через MCP…"));
    card.append(result);
    return append(card);
  };

  const fillCall = (card, call) => {
    card.classList.remove("is-pending");
    card.classList.toggle("is-error", !call.ok);
    const statusLine = card.querySelector(".day20-call__status");
    statusLine.textContent = call.ok
      ? `результат · ${call.chars} симв. · ≈ ${call.tokens} ток. модели · ${call.ms} мс`
      : `ошибка инструмента · ${call.ms} мс`;
    card.append(fold(call.ok ? "что вернул сервер" : "текст ошибки — его и увидит модель", call.preview));
    followBottom();
  };

  const turnMeta = (stats) =>
    `запросов к модели ${stats.llm_calls} · вызовов ${stats.tool_calls} · вход ${stats.prompt_tokens} ток. · ${seconds(stats.ms)}`;

  /* ── Панель ────────────────────────────────────────────────────────── */

  const renderSummary = (state) => {
    Object.entries(state.summary || {}).forEach(([key, value]) => {
      const cell = document.querySelector(`[data-summary="${key}"]`);
      if (cell) cell.textContent = String(value);
    });
    renderFlow(state.flow);
    renderReports(state.reports || []);
    const file = state.file;
    fileBox.textContent = file && file.exists ? `файл диалога: ${file.file} · ${file.bytes} байт` : "";
  };

  const renderFlow = (flow) => {
    routeBox.replaceChildren();
    checksBox.replaceChildren();
    routeMeta.textContent = "";
    if (!flow) {
      routeBox.append(el("li", "day20-route__empty", "Маршрута ещё не было."));
      return;
    }
    const facts = [
      flow.path.length ? `путь: ${flow.path.join(" → ")}` : "без вызовов",
      flow.expected.length ? `по просьбе нужны: ${flow.expected.join(", ")}` : null,
      flow.idle.length ? `не понадобились: ${flow.idle.join(", ")}` : null,
      flow.failed.length ? `не ответили: ${flow.failed.map((item) => item.title).join(", ")}` : null,
    ].filter(Boolean);
    routeMeta.textContent = `ход ${flow.turn} · ${facts.join(" · ")}`;

    if (!flow.steps.length) routeBox.append(el("li", "day20-route__empty", "Модель ответила без инструментов."));
    for (const step of flow.steps) {
      const item = el("li", `day20-route__step${step.ok ? "" : " is-error"}`);
      const head = el("div", "day20-route__head");
      head.append(el("span", "day20-route__round", `круг ${step.round}`));
      head.append(tag(step.server));
      head.append(el("strong", "", step.tool));
      head.append(el("small", "day20-route__ms", `${step.ms} мс`));
      item.append(head);
      item.append(el("code", "day20-route__args", compactArgs(step.arguments)));
      routeBox.append(item);
    }
    for (const check of flow.checks) {
      const item = el("li", `day20-check${check.ok ? " is-ok" : " is-error"}`);
      item.append(el("span", "day20-check__mark", check.ok ? "✓" : "✗"));
      const text = el("div", "day20-check__text");
      text.append(el("strong", "", check.title));
      text.append(el("small", "", check.detail));
      item.append(text);
      checksBox.append(item);
    }
  };

  const renderReports = (reports) => {
    reportsBox.replaceChildren();
    if (!reports.length) {
      reportsBox.append(el("li", "day20-reports__empty", "Отчётов ещё нет."));
      return;
    }
    for (const report of reports) {
      const item = el("li", "day20-report");
      const when = new Date(report.modified * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
      item.append(el("strong", "day20-report__name", report.file));
      item.append(el("small", "", `${when} · ${report.bytes} байт`));
      item.append(fold("открыть", report.content + (report.truncated ? "\n…(дальше обрезано)" : "")));
      reportsBox.append(item);
    }
  };

  const renderServers = (data) => {
    const total = data.servers.filter((card) => card.ok).length;
    serversMeta.textContent = `на связи ${total} из ${data.servers.length} · ≈ ${data.schema_tokens} ток. схем всех серверов в каждом запросе`;
    for (const card of data.servers) {
      const item = document.querySelector(`[data-server="${card.key}"]`);
      if (!item) continue;
      item.classList.toggle("is-error", !card.ok);
      item.querySelector("[data-server-meta]").textContent = card.ok
        ? `${tools(card.tools.length)} · ≈ ${card.schema_tokens} ток. · ${card.elapsed_ms} мс`
        : `не ответил: ${card.error}`;
      item.querySelectorAll(".day20-server__tools").forEach((node) => node.remove());
      if (card.ok) {
        const list = el("ul", "day20-server__tools");
        card.tools.forEach((tool) => {
          const chip = el("li", "", splitName(tool.name).tool);
          chip.title = tool.description || "";
          list.append(chip);
        });
        item.append(list);
      }
    }
  };

  const loadServers = async () => {
    try {
      const response = await fetch(urls.servers);
      renderServers(await response.json());
    } catch (error) {
      serversMeta.textContent = `серверы не опрошены: ${error.message}`;
    }
  };

  /* ── Восстановление после перезагрузки ─────────────────────────────── */

  const rebuild = (state) => {
    log.querySelectorAll(":scope > :not([data-log-empty])").forEach((node) => node.remove());
    emptyNote.hidden = Boolean(state.messages && state.messages.length);
    const calls = new Map((state.calls || []).map((call) => [call.id, call]));
    const turns = state.turns || [];
    let turnIndex = -1;

    for (const message of state.messages || []) {
      if (message.role === "user") {
        turnIndex += 1;
        addTurn("user", message.content, { restored: true });
      } else if (message.role === "assistant" && message.tool_calls) {
        if (message.content && message.content.trim()) addMarker(`модель: ${message.content.trim()}`, "model");
        for (const raw of message.tool_calls) {
          const call = calls.get(raw.id);
          if (!call) continue;
          fillCall(addCall(call), call);
        }
      } else if (message.role === "assistant") {
        const turn = addTurn("assistant", message.content, { restored: true });
        if (turns[turnIndex]) turn.querySelector("[data-turn-meta]").textContent = turnMeta(turns[turnIndex]);
      }
    }
    renderSummary(state);
  };

  /* ── Ход ───────────────────────────────────────────────────────────── */

  const readNdjson = async (body, onEvent) => {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      lines.filter((line) => line.trim()).forEach((line) => onEvent(JSON.parse(line)));
      if (done) break;
    }
    if (buffer.trim()) onEvent(JSON.parse(buffer));
  };

  const setBusy = (value) => {
    busy = value;
    submitButton.disabled = value;
    resetButton.disabled = value;
    if (scenarioButton) scenarioButton.disabled = value || scenarioRunning;
  };

  const ask = async (text) => {
    setBusy(true);
    followEnabled = true;
    addTurn("user", text);
    status.textContent = "поднимаю MCP-сервер…";

    const cards = new Map();
    let answerTurn = null;
    let failed = false;

    try {
      const response = await fetch(urls.chat, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      if (!response.ok || !response.body) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `HTTP ${response.status}`);
      }

      await readNdjson(response.body, (event) => {
        if (event.type === "connected") {
          const cards = event.servers || [];
          const line = cards.map((card) => (card.ok ? `${card.key} ✓ ${card.tools.length}` : `${card.key} ✗`)).join(" · ");
          addMarker(`MCP: ${line} · ≈ ${event.schema_tokens} ток. схем · ${event.ms} мс`, "connect");
          cards.filter((card) => !card.ok).forEach((card) => addMarker(`${card.title} не ответил: ${card.error}`, "model"));
          status.textContent = "модель думает…";
        } else if (event.type === "model") {
          if (event.content) addMarker(`модель: ${event.content}`, "model");
          status.textContent = `модель просит: ${event.calls.map((call) => call.name).join(", ")}`;
        } else if (event.type === "tool_call") {
          cards.set(event.id, addCall(event));
          status.textContent = `вызываю ${event.name}…`;
        } else if (event.type === "tool_result") {
          const card = cards.get(event.id);
          if (card) fillCall(card, event);
          status.textContent = "модель читает результат…";
        } else if (event.type === "answer") {
          answerTurn = addTurn("assistant", event.content);
        } else if (event.type === "done") {
          if (answerTurn) answerTurn.querySelector("[data-turn-meta]").textContent = turnMeta(event.stats);
          if (event.state) renderSummary(event.state);
          status.textContent = "";
        } else if (event.type === "error") {
          throw new Error(event.error);
        }
      });
    } catch (error) {
      failed = true;
      const turn = addTurn("assistant", `Ход не завершён: ${error.message}`);
      turn.classList.add("is-failed");
      status.textContent = "";
    } finally {
      cards.forEach((card) => {
        if (card.classList.contains("is-pending")) card.querySelector(".day20-call__status").textContent = "ответа не было";
      });
      followEnabled = false;
      setBusy(false);
    }
    return !failed;
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = messageField.value.trim();
    if (!text || busy) return;
    messageField.value = "";
    await ask(text);
    messageField.focus();
  });

  messageField.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  resetButton.addEventListener("click", async () => {
    if (busy) return;
    const response = await fetch(urls.reset, { method: "POST" });
    const state = await response.json();
    rebuild(state);
    emptyNote.hidden = false;
    status.textContent = "история стёрта";
  });

  if (scenarioButton) {
    scenarioButton.addEventListener("click", async () => {
      if (busy || scenarioRunning) return;
      scenarioRunning = true;
      scenarioButton.disabled = true;
      try {
        const response = await fetch(scenarioButton.dataset.scenarioUrl);
        const scenario = await response.json();
        if (!response.ok) throw new Error(scenario.error || "Сценарий не загрузился");
        for (let i = 0; i < scenario.messages.length; i += 1) {
          const ok = await ask(scenario.messages[i]);
          if (!ok) return;
          status.textContent = `${scenario.title}: реплика ${i + 1} из ${scenario.messages.length}`;
          await new Promise((resolve) => setTimeout(resolve, 900));
        }
        status.textContent = "Сценарий пройден: маршрут и сверка — справа";
      } catch (error) {
        status.textContent = error.message || "Сценарий не прошёл";
      } finally {
        scenarioRunning = false;
        scenarioButton.disabled = busy;
      }
    });
  }

  if (initialScript) {
    try {
      rebuild(JSON.parse(initialScript.textContent));
    } catch {
      // Слепок не разобрался — лента останется пустой.
    }
  }
  loadServers();
})();
