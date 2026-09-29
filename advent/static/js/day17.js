/*
 * День 17: первый инструмент MCP.
 * Ход приходит потоком событий: connected → (model → tool_call →
 * tool_result)* → answer → done. Каждый вызов инструмента — карточка в
 * ленте: что попросила модель, с какими аргументами и что вернул сервер.
 * Результаты и описания приходят от GitHub — чужой текст, только textContent;
 * ответ модели — через renderMarkdown, который всё экранирует.
 */
(() => {
  const form = document.querySelector("#agent-form");
  if (!form) return;

  const messageField = document.querySelector("#agent-message");
  const submitButton = document.querySelector("#agent-submit");
  const resetButton = document.querySelector("#agent-reset");
  const scenarioButton = document.querySelector("#day17-scenario");
  const status = document.querySelector("#agent-status");
  const log = document.querySelector("#agent-log");
  const emptyNote = log.querySelector("[data-log-empty]");
  const turnTemplate = document.querySelector("#agent-turn-template");
  const toolsBox = document.querySelector("[data-tools]");
  const toolsMeta = document.querySelector("[data-tools-meta]");
  const journalBox = document.querySelector("[data-journal]");
  const fileBox = document.querySelector("[data-file]");
  const initialScript = document.querySelector("#agent-initial");

  const urls = {
    chat: form.dataset.chatUrl,
    state: form.dataset.stateUrl,
    reset: form.dataset.resetUrl,
    tools: form.dataset.toolsUrl,
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
    const details = el("details", "day17-fold");
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

  const addMarker = (text, kind = "info") => append(el("p", `day17-marker day17-marker--${kind}`, text));

  const addCall = (call) => {
    const card = el("div", "day17-call is-pending");
    card.dataset.callId = call.id;

    const head = el("div", "day17-call__head");
    head.append(el("span", "day17-call__arrow", "→"));
    head.append(el("strong", "day17-call__name", call.name));
    head.append(el("code", "day17-call__args", compactArgs(call.arguments)));
    card.append(head);

    const result = el("div", "day17-call__result");
    result.append(el("span", "day17-call__arrow", "←"));
    result.append(el("span", "day17-call__status", "вызываю через MCP…"));
    card.append(result);
    return append(card);
  };

  const fillCall = (card, call) => {
    card.classList.remove("is-pending");
    card.classList.toggle("is-error", !call.ok);
    const statusLine = card.querySelector(".day17-call__status");
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
    renderJournal(state.calls || []);
    const file = state.file;
    fileBox.textContent = file && file.exists ? `файл: ${file.file} · ${file.bytes} байт` : "";
  };

  const renderJournal = (calls) => {
    journalBox.replaceChildren();
    if (!calls.length) {
      journalBox.append(el("li", "day17-journal__empty", "Вызовов ещё не было."));
      return;
    }
    for (const call of calls) {
      const item = el("li", `day17-journal__item${call.ok ? "" : " is-error"}`);
      item.append(el("span", "day17-journal__where", `ход ${call.turn}.${call.round}`));
      item.append(el("strong", "", call.name));
      item.append(el("code", "", compactArgs(call.arguments)));
      item.append(el("small", "", call.ok ? `ок · ${call.chars} симв. · ${call.ms} мс` : `ошибка · ${call.ms} мс`));
      journalBox.append(item);
    }
  };

  const renderTools = (data) => {
    toolsBox.replaceChildren();
    if (!data.ok) {
      toolsMeta.textContent = `сервер не ответил: ${data.error}`;
      toolsMeta.classList.add("is-error");
      return;
    }
    toolsMeta.classList.remove("is-error");
    toolsMeta.textContent = `${data.tools.length} инструмента · ≈ ${data.schema_tokens} ток. схем в каждом запросе · протокол ${data.protocol_version}`;

    for (const tool of data.tools) {
      const item = el("li", "day17-tool");
      const head = el("div", "day17-tool__head");
      head.append(el("strong", "", tool.name));
      if (tool.title) head.append(el("span", "day17-tool__title", tool.title));
      item.append(head);
      if (tool.description) item.append(el("p", "day17-tool__description", tool.description));

      const params = el("ul", "day17-params");
      for (const param of tool.params) {
        const chip = el("li", `day17-param${param.required ? " is-required" : ""}`);
        chip.append(el("span", "day17-param__name", param.name + (param.required ? "*" : "")));
        chip.append(el("span", "day17-param__type", param.type));
        if (param.description) chip.title = param.description;
        params.append(chip);
      }
      item.append(params);
      item.append(fold("inputSchema", JSON.stringify(tool.schema, null, 2)));
      toolsBox.append(item);
    }
  };

  const loadTools = async () => {
    try {
      const response = await fetch(urls.tools);
      renderTools(await response.json());
    } catch (error) {
      toolsMeta.textContent = `список не получен: ${error.message}`;
      toolsMeta.classList.add("is-error");
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
          addMarker(
            `MCP: ${event.server} · протокол ${event.protocol} · инструментов ${event.tools.length} · ≈ ${event.schema_tokens} ток. схем · ${event.ms} мс`,
            "connect",
          );
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
        if (card.classList.contains("is-pending")) card.querySelector(".day17-call__status").textContent = "ответа не было";
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
        status.textContent = "Сценарий пройден: журнал вызовов — справа";
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
  loadTools();
})();
