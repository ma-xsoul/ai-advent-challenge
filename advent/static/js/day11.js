/*
 * День 11: три слоя памяти вместо одного стека.
 * Панель показывает, что лежит в каждом слое; переключатели меняют, кто
 * раскладывает и какие слои уходят в промпт; кнопки слоёв убивают их по
 * отдельности — в этом и видна разница в сроках жизни.
 */
(() => {
  const form = document.querySelector("#agent-form");
  if (!form) return;

  const messageField = document.querySelector("#agent-message");
  const submitButton = document.querySelector("#agent-submit");
  const resetButton = document.querySelector("#agent-reset");
  const newTaskButton = document.querySelector("#day11-new-task");
  const scenarioButton = document.querySelector("#day11-scenario");
  const modeForm = document.querySelector("#day11-mode");
  const enabledForm = document.querySelector("#day11-enabled");
  const manualForm = document.querySelector("#day11-manual");
  const status = document.querySelector("#agent-status");
  const log = document.querySelector("#agent-log");
  const emptyNote = log.querySelector("[data-log-empty]");
  const turnTemplate = document.querySelector("#agent-turn-template");
  const markerTemplate = document.querySelector("#day11-marker-template");
  const panelsBox = document.querySelector("[data-panels]");
  const filesBox = document.querySelector("[data-files]");
  const modeHint = document.querySelector("[data-mode-hint]");
  const initialScript = document.querySelector("#agent-initial");

  const urls = {
    chat: form.dataset.chatUrl,
    state: form.dataset.stateUrl,
    reset: form.dataset.resetUrl,
    mode: form.dataset.modeUrl,
    layers: form.dataset.layersUrl,
    memory: form.dataset.memoryUrl,
    move: form.dataset.moveUrl,
    clear: form.dataset.clearUrl,
    newTask: form.dataset.newTaskUrl,
  };

  const SCOPES = ["chat", "task", "user"];
  const MODE_HINTS = {
    agent: "раскладывает нарезатель — отдельный запрос после каждого хода",
    heuristic: "раскладывает эвристика — по словам-маркерам, без запросов",
    manual: "ничего не раскладывается само — только кнопкой",
  };

  let current = null;

  /* ── Потоковая отрисовка и ведение экрана — как в дне 10 ─────────────── */

  const rawBodies = new WeakMap();
  const pendingRender = new Set();
  let renderFrame = null;

  const renderRaw = (output) => {
    const raw = rawBodies.get(output) || "";
    if (typeof window.renderMarkdown === "function") output.innerHTML = window.renderMarkdown(raw);
    else output.textContent = raw;
  };

  const FOLLOW_MARGIN = 90;
  let followTarget = null;
  let followEnabled = false;
  let lastAutoScroll = -1;

  const nearTargetBottom = () => {
    if (!followTarget || followTarget.offsetParent === null) return false;
    return followTarget.getBoundingClientRect().bottom - window.innerHeight < 160;
  };

  const followBottom = () => {
    if (!followEnabled || !followTarget || followTarget.offsetParent === null) return;
    const overflow = followTarget.getBoundingClientRect().bottom - (window.innerHeight - FOLLOW_MARGIN);
    if (overflow <= 1) return;
    window.scrollBy({ top: overflow, behavior: "auto" });
    lastAutoScroll = Math.round(window.scrollY);
  };

  const syncFollowToUser = () => {
    if (followTarget) followEnabled = nearTargetBottom();
  };

  window.addEventListener(
    "scroll",
    () => {
      if (Math.abs(Math.round(window.scrollY) - lastAutoScroll) > 2) syncFollowToUser();
    },
    { passive: true },
  );
  window.addEventListener("wheel", syncFollowToUser, { passive: true });
  window.addEventListener("touchmove", syncFollowToUser, { passive: true });

  const scheduleRender = (output) => {
    pendingRender.add(output);
    if (renderFrame !== null) return;
    renderFrame = window.requestAnimationFrame(() => {
      renderFrame = null;
      pendingRender.forEach(renderRaw);
      pendingRender.clear();
      followBottom();
    });
  };

  const setBody = (output, text) => {
    rawBodies.set(output, text);
    scheduleRender(output);
  };

  const appendBody = (output, chunk) => setBody(output, (rawBodies.get(output) || "") + chunk);

  const revealBlock = (element) => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    element.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest" });
  };

  /* ── Лента ──────────────────────────────────────────────────────────── */

  const addTurn = (role, text, { reveal = true, restored = false } = {}) => {
    emptyNote.hidden = true;
    const turn = turnTemplate.content.firstElementChild.cloneNode(true);
    turn.dataset.turn = role;
    turn.querySelector("[data-turn-role]").textContent = role === "user" ? "вы" : "агент";
    if (restored) turn.classList.add("is-restored");
    setBody(turn.querySelector("[data-turn-body]"), text);
    log.append(turn);
    if (reveal) revealBlock(turn);
    return turn;
  };

  const addMarker = (text, kind = "done") => {
    emptyNote.hidden = true;
    const marker = markerTemplate.content.firstElementChild.cloneNode(true);
    setMarker(marker, text, kind);
    log.append(marker);
    return marker;
  };

  const setMarker = (marker, text, kind = "done") => {
    marker.classList.toggle("is-pending", kind === "pending");
    marker.classList.toggle("is-failed", kind === "failed");
    marker.classList.toggle("is-cut", kind === "cut");
    marker.querySelector("[data-marker-text]").textContent = text;
  };

  // Раскладка одной реплики в человеческом виде: что и куда легло.
  const describeUpdate = (update) => {
    const parts = [];
    SCOPES.forEach((scope) => {
      const added = update.added?.[scope] || [];
      const changed = update.changed?.[scope] || [];
      if (added.length) parts.push(`${scope}: +${added.join(", ")}`);
      if (changed.length) parts.push(`${scope}: ~${changed.join(", ")}`);
    });
    if (update.forgotten?.length) parts.push(`забыто: ${update.forgotten.join(", ")}`);
    const diff = parts.length ? parts.join(" · ") : "ничего нового";
    const cost =
      update.mode === "agent"
        ? ` · ${update.input_tokens ?? "—"} → ${update.output_tokens ?? "—"} токенов`
        : " · без запроса";
    return `память (${update.mode}): ${diff} · слои ≈${update.memory_tokens} токенов${cost}`;
  };

  const formatTurnMeta = (turn, finishReason) =>
    `≈${turn.question_tokens} вопрос · ${turn.context_tokens ?? "—"} контекст · ${turn.answer_tokens ?? "—"} ответ${finishReason ? ` · ${finishReason}` : ""}`;

  const clearLog = () => {
    log.querySelectorAll("[data-turn], [data-marker]").forEach((node) => node.remove());
    emptyNote.hidden = false;
  };

  const renderLog = (snapshot, { restored = false } = {}) => {
    clearLog();
    const messages = snapshot.messages || [];
    const turns = snapshot.turns || [];
    const byTurn = new Map();
    (snapshot.updates || []).forEach((update) => {
      const list = byTurn.get(update.after_turn) || [];
      list.push(describeUpdate(update));
      byTurn.set(update.after_turn, list);
    });
    let turnIndex = 0;
    messages.forEach((message) => {
      const turn = addTurn(message.role, message.content, { reveal: false, restored });
      if (message.role === "assistant") {
        turnIndex += 1;
        const stats = turns[turnIndex - 1];
        if (stats) turn.querySelector("[data-turn-meta]").textContent = formatTurnMeta(stats);
        (byTurn.get(turnIndex) || []).forEach((text) => addMarker(text));
      }
    });
  };

  /* ── Панели слоёв ───────────────────────────────────────────────────── */

  const escapeHtml = (text) =>
    String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);

  const setText = (key, value) => {
    document.querySelectorAll(`[data-debug="${key}"]`).forEach((node) => {
      node.textContent = value;
    });
  };

  const renderDebug = (state) => {
    setText("stack", (state.messages || []).length);
    setText("memory_tokens", state.memory?.tokens ?? 0);
    setText("requests", state.usage?.requests ?? 0);
    setText("side_requests", state.side?.requests ?? 0);
    // Расход нарезателя идёт мимо AgentUsage: он не «из них», а сверху.
    const dialogue = state.usage?.total_tokens ?? 0;
    const side = (state.side?.input_tokens || 0) + (state.side?.output_tokens || 0);
    setText("total_tokens", dialogue);
    setText("side_tokens", side);
    setText("all_tokens", dialogue + side);
  };

  const renderPanels = (state) => {
    if (!panelsBox) return;
    panelsBox.textContent = "";
    const last = (state.updates || []).at(-1);
    (state.memory?.layers || []).forEach((layer) => {
      const touched = new Set([...(last?.added?.[layer.scope] || []), ...(last?.changed?.[layer.scope] || [])]);
      const box = document.createElement("section");
      box.className = "day11-panel";
      box.classList.toggle("is-off", !layer.enabled);

      // Заголовок и счётчики — разными строками: в узкой колонке они
      // иначе дерутся за место и «Долговременная» обрезается многоточием.
      const head = document.createElement("header");
      head.innerHTML =
        `<span class="day11-panel__scope">${escapeHtml(layer.scope)}</span>` +
        `<span class="day11-panel__title">${escapeHtml(layer.title.replace(/ память$/, ""))}</span>`;
      const clear = document.createElement("button");
      clear.type = "button";
      clear.className = "day11-panel__clear";
      clear.textContent = "[x]";
      clear.title = `Очистить слой (${layer.life})`;
      clear.addEventListener("click", () => clearScope(layer.scope));
      head.append(clear);
      box.append(head);

      const life = document.createElement("p");
      life.className = "day11-panel__life";
      life.innerHTML =
        `<span>${escapeHtml(layer.enabled ? layer.life : `${layer.life} · выключен`)}</span>` +
        `<small>${layer.items.length}/${layer.limit} · ≈${layer.tokens}</small>`;
      box.append(life);

      if (!layer.items.length) {
        const empty = document.createElement("p");
        empty.className = "day11-panel__empty";
        empty.textContent = "Пусто.";
        box.append(empty);
      }

      layer.items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "day11-item";
        row.classList.toggle("is-changed", touched.has(item.key));
        row.innerHTML =
          `<span class="day11-item__key" title="${escapeHtml(item.source)}, ход ${item.turn}">${escapeHtml(item.key)}</span>` +
          `<span class="day11-item__value">${escapeHtml(item.value)}</span>`;
        const tools = document.createElement("span");
        tools.className = "day11-item__tools";
        SCOPES.filter((scope) => scope !== layer.scope).forEach((scope) => {
          const move = document.createElement("button");
          move.type = "button";
          move.textContent = `→${scope}`;
          move.title = `Переложить в ${scope}`;
          move.addEventListener("click", () => moveItem(layer.scope, scope, item.key));
          tools.append(move);
        });
        const drop = document.createElement("button");
        drop.type = "button";
        drop.textContent = "×";
        drop.title = "Забыть";
        drop.addEventListener("click", () => forgetItem(layer.scope, item.key));
        tools.append(drop);
        row.append(tools);
        box.append(row);
      });

      panelsBox.append(box);
    });
  };

  const renderFiles = (state) => {
    if (!filesBox) return;
    filesBox.textContent = "";
    (state.files || []).forEach((file) => {
      const row = document.createElement("li");
      row.classList.toggle("is-missing", !file.exists);
      // Имя файла начинается с длинного id сессии — в узкой панели от него
      // остаётся каша. Показываем хвост, по которому слой и различается.
      const short = file.file.replace(/^.*?-mem-/, "…-mem-");
      row.title = file.file;
      row.innerHTML = `<code>${escapeHtml(short)}</code><small>${file.exists ? `${file.bytes} B` : "нет файла"}</small>`;
      filesBox.append(row);
    });
  };

  const renderControls = (state) => {
    if (modeForm) {
      const input = modeForm.querySelector(`input[value="${state.mode}"]`);
      if (input) input.checked = true;
    }
    if (enabledForm) {
      enabledForm.querySelectorAll("input[name=enabled]").forEach((input) => {
        input.checked = (state.enabled || []).includes(input.value);
      });
    }
    if (modeHint) modeHint.textContent = MODE_HINTS[state.mode] || "";
  };

  const renderState = (state, { rebuildLog = false, restored = false } = {}) => {
    if (!state) return;
    current = state;
    if (rebuildLog) renderLog(state, { restored });
    renderDebug(state);
    renderPanels(state);
    renderFiles(state);
    renderControls(state);
  };

  /* ── Запросы ────────────────────────────────────────────────────────── */

  const readJson = async (response) => {
    try {
      return await response.json();
    } catch {
      return {};
    }
  };

  const postJson = async (url, payload, method = "POST") => {
    const response = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: payload === undefined ? undefined : JSON.stringify(payload),
    });
    const data = await readJson(response);
    if (!response.ok) throw new Error(data?.error || "Запрос не удался");
    return data;
  };

  const refreshState = async () => {
    try {
      const response = await fetch(urls.state);
      const state = await readJson(response);
      if (response.ok) renderState(state);
    } catch {
      // Панель обновится после следующего хода.
    }
  };

  /* ── Ходы ───────────────────────────────────────────────────────────── */

  let busy = false;
  let scenarioRunning = false;

  const setLoading = (isLoading) => {
    busy = isLoading;
    messageField.disabled = isLoading;
    submitButton.disabled = isLoading;
    resetButton.disabled = isLoading;
    if (newTaskButton) newTaskButton.disabled = isLoading;
    if (scenarioButton) scenarioButton.disabled = isLoading || scenarioRunning;
    document
      .querySelectorAll("#day11-mode input, #day11-enabled input, #day11-manual input, #day11-manual button, .day11-panel button, .day11-item button")
      .forEach((el) => {
        el.disabled = isLoading;
      });
  };

  const sendMessage = async (message) => {
    addTurn("user", message);
    const turn = addTurn("assistant", "");
    const body = turn.querySelector("[data-turn-body]");
    const meta = turn.querySelector("[data-turn-meta]");
    body.classList.add("is-streaming");
    followTarget = body;
    followEnabled = true;
    let overflow = null;
    let pendingMarker = null;

    try {
      const response = await fetch(urls.chat, {
        method: "POST",
        headers: { Accept: "application/x-ndjson", "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      if (!response.ok) {
        const data = await readJson(response);
        throw new Error(data?.error || "Не удалось получить ответ");
      }
      if (!response.body) throw new Error("Браузер не поддерживает потоковые ответы");

      let finished = false;
      await readNdjson(response.body, (event) => {
        if (event.type === "delta") {
          appendBody(body, event.content);
        } else if (event.type === "memory_routing") {
          pendingMarker = addMarker(`раскладываю по слоям (${event.mode})…`, "pending");
          revealBlock(pendingMarker);
        } else if (event.type === "memory_routed") {
          setMarker(pendingMarker || addMarker("", "pending"), describeUpdate(event.update));
          pendingMarker = null;
          if (current) current.memory = event.memory;
          renderPanels({ ...(current || {}), memory: event.memory, updates: [event.update] });
        } else if (event.type === "memory_failed") {
          setMarker(pendingMarker || addMarker("", "pending"), `память не обновилась: ${event.error} — слои прежние`, "failed");
          pendingMarker = null;
        } else if (event.type === "done") {
          finished = true;
          setBody(body, event.reply.content);
          meta.textContent = event.turn ? formatTurnMeta(event.turn, event.reply.finish_reason) : "";
          renderState(event.snapshot);
          if (event.save_error) status.textContent = event.save_error;
        } else if (event.type === "error") {
          overflow = event.overflow || null;
          throw new Error(event.error);
        }
      });
      if (!finished) throw new Error("Поток оборвался до конца ответа");
      return true;
    } catch (error) {
      if (pendingMarker) setMarker(pendingMarker, `не удалось: ${error.message}`, "failed");
      turn.classList.add(overflow ? "is-overflow" : "is-failed");
      setBody(body, overflow ? `Переполнение контекста: ${error.message}` : `Ошибка: ${error.message}`);
      meta.textContent = overflow ? `≈${overflow.estimated} > лимит ${overflow.limit} · запрос не отправлен` : "запрос откатился из стека";
      return false;
    } finally {
      body.classList.remove("is-streaming");
      followTarget = null;
      followEnabled = false;
    }
  };

  const ask = async (message) => {
    setLoading(true);
    status.textContent = "Агент думает…";
    try {
      const ok = await sendMessage(message);
      await refreshState();
      status.textContent = ok ? "Готово" : "Ход не состоялся";
      return ok;
    } finally {
      setLoading(false);
      messageField.focus({ preventScroll: true });
    }
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = messageField.value.trim();
    if (!message || busy) {
      messageField.focus();
      return;
    }
    messageField.value = "";
    await ask(message);
  });

  messageField.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  /* ── Свой выпадающий список ─────────────────────────────────────────── */

  // Нативный <option> не стилизуется: попап рисует операционная система.
  // Поэтому список свой — кнопка плюс role="listbox", значение уезжает в
  // скрытый input, чтобы FormData собиралась как раньше.
  const setupSelect = (root) => {
    if (!root) return;
    const input = root.querySelector("input[type=hidden]");
    const button = root.querySelector("[data-select-button]");
    const label = root.querySelector("[data-select-value]");
    const list = root.querySelector("[role=listbox]");
    const options = [...list.querySelectorAll("[role=option]")];

    const isOpen = () => !list.hidden;

    const close = ({ focusButton = false } = {}) => {
      list.hidden = true;
      button.setAttribute("aria-expanded", "false");
      if (focusButton) button.focus();
    };

    const open = () => {
      list.hidden = false;
      button.setAttribute("aria-expanded", "true");
      (options.find((option) => option.dataset.value === input.value) || options[0]).focus();
    };

    const pick = (option) => {
      input.value = option.dataset.value;
      label.textContent = option.dataset.value;
      options.forEach((item) => item.setAttribute("aria-selected", String(item === option)));
      close({ focusButton: true });
    };

    const move = (from, step) => {
      const index = options.indexOf(from);
      options[(index + step + options.length) % options.length].focus();
    };

    button.addEventListener("click", () => (isOpen() ? close() : open()));
    button.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        open();
      }
    });

    options.forEach((option) => {
      option.addEventListener("click", () => pick(option));
      option.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          pick(option);
        } else if (event.key === "ArrowDown") {
          event.preventDefault();
          move(option, 1);
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          move(option, -1);
        } else if (event.key === "Escape" || event.key === "Tab") {
          close({ focusButton: event.key === "Escape" });
        }
      });
    });

    document.addEventListener("click", (event) => {
      if (isOpen() && !root.contains(event.target)) close();
    });
  };

  setupSelect(document.querySelector("[data-select]"));

  /* ── Слои: режим, видимость, ручная правка ──────────────────────────── */

  const apply = async (work, message) => {
    if (busy) return;
    setLoading(true);
    try {
      renderState(await work());
      if (message) status.textContent = message;
    } catch (error) {
      status.textContent = error.message || "Не удалось";
    } finally {
      setLoading(false);
    }
  };

  if (modeForm) {
    modeForm.addEventListener("change", () => {
      const mode = modeForm.querySelector("input[name=mode]:checked")?.value;
      apply(() => postJson(urls.mode, { mode }), `Раскладка: ${mode}`);
    });
  }

  if (enabledForm) {
    enabledForm.addEventListener("change", () => {
      const enabled = [...enabledForm.querySelectorAll("input[name=enabled]:checked")].map((input) => input.value);
      const off = SCOPES.filter((scope) => !enabled.includes(scope));
      apply(
        () => postJson(urls.layers, { enabled }),
        off.length ? `В промпт не уходят: ${off.join(", ")}` : "В промпт уходят все три слоя",
      );
    });
  }

  if (manualForm) {
    manualForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(manualForm);
      const scope = data.get("scope");
      const key = String(data.get("key") || "").trim();
      const value = String(data.get("value") || "").trim();
      if (!key || !value) return;
      apply(async () => {
        const state = await postJson(urls.memory, { scope, key, value });
        // Чистим только поля: выбранный слой оставляем — подряд обычно
        // кладут несколько записей в один и тот же.
        manualForm.querySelectorAll("input[type=text]").forEach((field) => {
          field.value = "";
        });
        return state;
      }, `Записано руками в ${scope}: ${key}`);
    });
  }

  const moveItem = (from, to, key) =>
    apply(() => postJson(urls.move, { from, to, key }), `«${key}»: ${from} → ${to}`);

  const forgetItem = (scope, key) =>
    apply(() => postJson(urls.memory, { scope, key }, "DELETE"), `Забыто из ${scope}: ${key}`);

  const clearScope = (scope) =>
    apply(() => postJson(urls.clear, { scope }), `Слой ${scope} очищен`);

  if (newTaskButton) {
    newTaskButton.addEventListener("click", () =>
      apply(() => postJson(urls.newTask, {}), "Новая задача: рабочий слой очищен, диалог и пользователь на месте"),
    );
  }

  resetButton.addEventListener("click", async () => {
    if (busy) return;
    setLoading(true);
    status.textContent = "Начинаю новый диалог…";
    try {
      renderState(await postJson(urls.reset, {}), { rebuildLog: true });
      status.textContent = "Новый диалог: стек и краткосрочный слой пусты, остальные два уцелели";
    } catch (error) {
      status.textContent = error.message || "Не удалось сбросить диалог";
    } finally {
      setLoading(false);
      messageField.focus({ preventScroll: true });
    }
  });

  /* ── Сценарий ───────────────────────────────────────────────────────── */

  const runMessages = async (messages, label) => {
    for (let i = 0; i < messages.length; i += 1) {
      status.textContent = `${label}: реплика ${i + 1} из ${messages.length}…`;
      const ok = await ask(messages[i]);
      if (!ok) return false;
    }
    return true;
  };

  if (scenarioButton) {
    scenarioButton.addEventListener("click", async () => {
      if (busy || scenarioRunning) return;
      scenarioRunning = true;
      scenarioButton.disabled = true;
      try {
        const response = await fetch(scenarioButton.dataset.scenarioUrl);
        const scenario = await readJson(response);
        if (!response.ok) throw new Error(scenario?.error || "Сценарий не загрузился");

        if (!(await runMessages(scenario.messages || [], scenario.title))) return;

        // Середина сценария: сбрасываем диалог. Краткосрочный слой умирает
        // вместе с ним, рабочий и долговременный обязаны уцелеть — дальше
        // мы это и спрашиваем.
        status.textContent = "Новый диалог: проверяем, что переживёт сброс…";
        renderState(await postJson(urls.reset, {}), { rebuildLog: true });
        addMarker("новый диалог: стек и краткосрочный слой очищены, рабочий и долговременный остались", "cut");

        await runMessages(scenario.after_reset || [], "После сброса");
        status.textContent = "Сценарий пройден: сравните ответ до и после сброса";
      } catch (error) {
        status.textContent = error.message || "Сценарий не прошёл";
      } finally {
        scenarioRunning = false;
        scenarioButton.disabled = false;
      }
    });
  }

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

  if (initialScript) {
    try {
      const initial = JSON.parse(initialScript.textContent);
      renderState(initial, { rebuildLog: true, restored: Boolean(initial.storage) });
    } catch {
      // Слепок не разобрался — лента останется пустой.
    }
  }
})();
