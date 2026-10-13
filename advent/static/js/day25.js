/*
 * День 25: мини-чат с RAG и памятью задачи.
 * Ход: user → memory (панель справа) → query → retrieved → answer.
 * У ответа видно, каким запросом искали, сколько нашлось, источники,
 * цитаты с проверкой и отметки хода: источники, цитаты, цель.
 * Сценарий — те же события по каждому из 12 ходов плюс итог в конце.
 * Тексты реплик, цитат и памяти — только через textContent; ответ
 * модели — через общий renderMarkdown, он экранирует HTML.
 */
(() => {
  const form = document.querySelector("#day25-form");
  if (!form) return;

  const input = document.querySelector("#day25-message");
  const sendButton = form.querySelector("button[type=submit]");
  const resetButton = form.querySelector("[data-reset]");
  const errorBox = document.querySelector("#day25-error");
  const list = document.querySelector("[data-messages]");
  const memoryBox = document.querySelector("[data-memory]");
  const memoryMeta = document.querySelector("[data-memory-meta]");
  const scenarios = document.querySelector("[data-scenarios]");
  const summaryBox = document.querySelector("[data-summary]");
  const initial = JSON.parse(document.querySelector("#day25-initial").textContent);
  const QUOTE_MARKS = { exact: "✓", near: "✓", elsewhere: "≈", partial: "≈", stitched: "≈", missing: "✗" };

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const markdown = (node, text) => {
    if (typeof window.renderMarkdown === "function") node.innerHTML = window.renderMarkdown(text);
    else node.textContent = text;
  };

  const number = (value) => Number(value).toLocaleString("ru-RU");
  const seconds = (ms) => `${(ms / 1000).toFixed(1).replace(".", ",")} с`;

  const showError = (text) => {
    errorBox.textContent = text;
    errorBox.hidden = !text;
  };

  async function* streamEvents(body) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) if (line.trim()) yield JSON.parse(line);
      if (done) break;
    }
    if (buffer.trim()) yield JSON.parse(buffer);
  }

  async function post(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return response;
  }

  /* ── Память задачи ─────────────────────────────────────────────────── */

  let shownMemory = { goal: "", clarified: [], constraints: [], terms: [] };

  function renderMemory(memory, animate) {
    const field = (name) => memoryBox.querySelector(`[data-memory-field="${name}"] dd`);
    const goal = field("goal");
    if (memory.goal) {
      goal.replaceChildren(el("p", "day25-goal", memory.goal));
      const since = el("small", "", `держится с хода ${memory.goal_since}`);
      goal.append(since);
      for (const change of memory.goal_changes || []) goal.append(el("small", "day25-changed", `ход ${change.turn}: сменилась с «${change.from}»`));
      if (animate && memory.goal !== shownMemory.goal) goal.firstChild.classList.add("is-new");
    } else goal.replaceChildren(el("p", "day25-muted", "появится после первого сообщения"));

    for (const name of ["clarified", "constraints", "terms"]) {
      const items = memory[name] || [];
      const box = field(name);
      if (!items.length) {
        box.replaceChildren(el("p", "day25-muted", "—"));
        continue;
      }
      const ul = el("ul");
      for (const item of items) {
        const li = el("li", "", item);
        if (animate && !(shownMemory[name] || []).includes(item)) li.classList.add("is-new");
        ul.append(li);
      }
      box.replaceChildren(ul);
    }
    const filled = ["clarified", "constraints", "terms"].reduce((sum, name) => sum + (memory[name] || []).length, 0);
    memoryMeta.textContent = memory.goal ? `цель + ${filled} пунктов` : "пусто";
    shownMemory = memory;
  }

  /* ── Сообщения ─────────────────────────────────────────────────────── */

  function userBubble(message) {
    const item = el("li", "day25-message day25-message--user");
    item.append(el("span", "day25-message__turn", `ход ${message.turn}`), el("p", "", message.content));
    return item;
  }

  function pendingBubble() {
    const item = el("li", "day25-message day25-message--assistant is-pending");
    item.append(el("p", "day25-message__meta", "обновляю память задачи и готовлю запрос…"));
    return item;
  }

  function quotesFold(quotes) {
    const found = quotes.filter((quote) => ["exact", "near", "elsewhere"].includes(quote.status)).length;
    const fold = el("details", "day25-fold day25-quotes");
    fold.append(el("summary", "", `цитаты ${found}/${quotes.length} нашлись в кусках`));
    const ol = el("ol");
    for (const quote of quotes) {
      const li = el("li", `is-${quote.status}`);
      li.append(el("span", "day25-quote__mark", `${QUOTE_MARKS[quote.status]} [${quote.n}]`), el("blockquote", "", quote.text));
      ol.append(li);
    }
    fold.append(ol);
    return fold;
  }

  function fillAssistant(item, message) {
    item.className = `day25-message day25-message--assistant is-${message.status}`;
    item.replaceChildren();
    const meta = el("p", "day25-message__meta");
    meta.append(el("span", "day25-label", "запрос"), document.createTextNode(message.query));
    const relevance = message.relevance === null ? "всё ниже порога" : `релевантность ${message.relevance}/10`;
    meta.append(el("small", "", ` · ${relevance}`));
    item.append(meta);

    const body = el("div", "markdown-body day25-message__body");
    markdown(body, message.content);
    item.append(body);
    if (message.clarify) item.append(el("p", "day25-clarify", message.clarify));

    if (message.sources.length) {
      const sources = el("ul", "day25-sources");
      for (const source of message.sources) {
        const chip = el("li", "day25-source");
        chip.title = `${source.section} · chunk_id ${source.chunk_id}`;
        chip.append(el("b", "", `[${source.n}]`), document.createTextNode(` ${source.source}`));
        sources.append(chip);
      }
      item.append(sources);
    }
    if (message.quotes.length) item.append(quotesFold(message.quotes));

    const checks = message.checks;
    const marks = el("p", "day25-checks");
    const add = (ok, text) => marks.append(el("span", ok ? "is-ok" : "is-bad", `${ok ? "✓" : "✗"} ${text}`));
    if (checks.unknown) add(true, "«не знаю» вместо выдумки");
    else {
      add(checks.has_sources, "источники");
      add(checks.quotes_total > 0 && checks.quotes_verified === checks.quotes_total, `цитаты ${checks.quotes_verified}/${checks.quotes_total}`);
    }
    add(checks.goal_kept, "цель держится");
    marks.append(el("span", "day25-checks__cost", `${seconds(message.ms)} · ${number(message.tokens)} ток.`));
    item.append(marks);
  }

  function assistantBubble(message) {
    const item = el("li");
    fillAssistant(item, message);
    return item;
  }

  function renderChat(chat) {
    list.replaceChildren();
    for (const message of chat.messages) list.append(message.role === "user" ? userBubble(message) : assistantBubble(message));
    if (!chat.messages.length) list.append(el("li", "day25-empty", "Спросите что-нибудь о проекте — или прогоните длинный сценарий ниже."));
    renderMemory(chat.memory, false);
    if (chat.summary) renderSummary(chat.summary);
  }

  const scrollToEnd = () => list.lastElementChild?.scrollIntoView({ block: "nearest", behavior: "smooth" });

  // Общий разбор событий хода — и для сообщения, и для сценария.
  let pending = null;
  function onTurnEvent(event) {
    if (event.type === "user") {
      list.querySelector(".day25-empty")?.remove();
      list.append(userBubble(event));
      pending = pendingBubble();
      list.append(pending);
      scrollToEnd();
    } else if (event.type === "memory") {
      renderMemory(event, true);
    } else if (event.type === "query" && pending) {
      pending.querySelector(".day25-message__meta").textContent = `ищу в базе: ${event.query}`;
    } else if (event.type === "retrieved" && pending) {
      const relevance = event.relevance === null ? "всё ниже порога" : `лучший кусок ${event.relevance}/10`;
      pending.querySelector(".day25-message__meta").textContent += ` · ${event.context} кусков в контексте, ${relevance} · пишу ответ…`;
    } else if (event.type === "answer" && pending) {
      fillAssistant(pending, event);
      pending = null;
      scrollToEnd();
    } else if (event.type === "error") showError(event.error);
  }

  /* ── Итог сценария ─────────────────────────────────────────────────── */

  function renderSummary(summary) {
    const set = (key, text, ok) => {
      const cell = summaryBox.querySelector(`[data-summary-row="${key}"] [data-summary-cell]`);
      cell.textContent = text;
      cell.className = ok === undefined ? "" : ok ? "is-ok" : "is-bad";
    };
    set("turns", String(summary.turns));
    set("with_sources", `${summary.with_sources} из ${summary.turns}`, summary.with_sources + summary.unknown === summary.turns);
    set("unknown", String(summary.unknown));
    set("quotes", `${summary.quotes_verified} из ${summary.quotes_total}`, summary.quotes_verified === summary.quotes_total);
    set("goal_kept", `${summary.goal_kept} из ${summary.turns}`, summary.goal_kept === summary.turns);
    set("goal_changes", String(summary.goal_changes), summary.goal_changes === 0);
    set("recap_ok", summary.recap_ok ? "✓ да" : "✗ нет", summary.recap_ok);
    set("tokens", number(summary.tokens));
    summaryBox.hidden = false;
  }

  /* ── Кнопки ────────────────────────────────────────────────────────── */

  let busy = false;
  const setBusy = (value) => {
    busy = value;
    input.disabled = value;
    sendButton.disabled = value;
    resetButton.disabled = value;
    scenarios.querySelectorAll("[data-run]").forEach((button) => (button.disabled = value));
  };

  async function run(url, payload, onEvent) {
    if (busy) return;
    showError("");
    setBusy(true);
    try {
      const response = await post(url, payload);
      for await (const event of streamEvents(response.body)) onEvent(event);
    } catch (error) {
      showError(error.message);
      pending?.remove();
      pending = null;
    } finally {
      setBusy(false);
      input.focus();
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message) return;
    input.value = "";
    run(form.dataset.chatUrl, { message }, onTurnEvent);
  });

  resetButton.addEventListener("click", async () => {
    if (busy) return;
    try {
      const response = await post(form.dataset.resetUrl);
      const chat = await response.json();
      summaryBox.hidden = true;
      scenarios.querySelectorAll("[data-progress]").forEach((node) => (node.textContent = ""));
      renderChat(chat);
    } catch (error) {
      showError(error.message);
    }
  });

  scenarios.addEventListener("click", (event) => {
    const button = event.target.closest("[data-run]");
    if (!button) return;
    const card = button.closest("[data-scenario]");
    const progress = card.querySelector("[data-progress]");
    scenarios.querySelectorAll("[data-progress]").forEach((node) => (node.textContent = ""));
    summaryBox.hidden = true;
    let total = 12;
    run(scenarios.dataset.scenarioUrl, { key: button.dataset.run }, (event) => {
      if (event.type === "scenario_start") {
        total = event.total;
        renderChat({ messages: [], memory: { goal: "", clarified: [], constraints: [], terms: [] }, summary: null });
        list.replaceChildren();
        list.scrollIntoView({ block: "start", behavior: "smooth" });
      } else if (event.type === "user") progress.textContent = `ход ${event.turn} из ${total}…`;
      else if (event.type === "scenario_summary") {
        progress.textContent = `готово: ${event.turns} ходов`;
        renderSummary(event);
      }
      onTurnEvent(event);
    });
  });

  renderChat(initial);
})();
