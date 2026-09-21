/*
 * День 14: инварианты в два рубежа.
 * Справа — список правил с выключателями; после каждого ответа в ленте
 * отметка: отказал ли агент сам, что поймала регулярка, что — ревизор.
 * Внизу журнал по ходам: кто удержал правило.
 */
(() => {
  const form = document.querySelector("#agent-form");
  if (!form) return;

  const messageField = document.querySelector("#agent-message");
  const submitButton = document.querySelector("#agent-submit");
  const resetButton = document.querySelector("#agent-reset");
  const addButton = document.querySelector("#day14-add");
  const clearButton = document.querySelector("#day14-clear");
  const scenarioButton = document.querySelector("#day14-scenario");
  const addForm = document.querySelector("#day14-form");
  const status = document.querySelector("#agent-status");
  const log = document.querySelector("#agent-log");
  const emptyNote = log.querySelector("[data-log-empty]");
  const turnTemplate = document.querySelector("#agent-turn-template");
  const markerTemplate = document.querySelector("#day14-marker-template");
  const listBox = document.querySelector("[data-invariants]");
  const checksBody = document.querySelector("[data-checks]");
  const checksEmpty = document.querySelector("[data-checks-empty]");
  const hint = document.querySelector("[data-hint]");
  const systemBox = document.querySelector("[data-system-text]");
  const initialScript = document.querySelector("#agent-initial");

  const urls = {
    chat: form.dataset.chatUrl,
    state: form.dataset.stateUrl,
    reset: form.dataset.resetUrl,
    add: form.dataset.addUrl,
    delete: form.dataset.deleteUrl,
    toggle: form.dataset.toggleUrl,
    clear: form.dataset.clearUrl,
  };

  let current = null;

  /* ── Свой выпадающий список — как в днях 11–12 ──────────────────────── */

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
      (options.find((o) => o.dataset.value === input.value) || options[0]).focus();
    };
    const pick = (option) => {
      input.value = option.dataset.value;
      label.textContent = option.textContent;
      options.forEach((o) => o.setAttribute("aria-selected", String(o === option)));
      close({ focusButton: true });
    };
    const move = (from, step) => options[(options.indexOf(from) + step + options.length) % options.length].focus();
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

  /* ── Потоковая отрисовка и ведение экрана ──────────────────────────── */

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
    marker.classList.toggle("is-caught", kind === "caught");
    marker.classList.toggle("is-held", kind === "held");
    marker.querySelector("[data-marker-text]").textContent = text;
  };

  const ruleTitle = (id) => (current?.invariants || []).find((item) => item.id === id)?.rule || id;

  // Итог проверки одной строкой: отказал сам / чисто / поймали.
  const describeCheck = (record) => {
    const parts = [];
    if (record.refused) parts.push(record.named.length ? `отказал сам, назвал: ${record.named.join(", ")}` : "отказал сам, правило не назвал");
    else parts.push("не отказал");
    if (record.regex?.length) parts.push(`регулярка: тревога ${record.regex.map((v) => `${v.invariant} («${v.evidence}»)`).join(", ")}`);
    else parts.push("регулярка: чисто");
    if (record.reviewer_error) parts.push(`ревизор не ответил: ${record.reviewer_error}`);
    else if (record.reviewer === null || record.reviewer === undefined) parts.push("ревизор не вызывался");
    else if (record.reviewer.length) parts.push(`ревизор подтвердил: ${record.reviewer.map((v) => `${v.invariant} — ${v.why}`).join("; ")}`);
    else parts.push(record.regex?.length ? "ревизор: снял тревогу" : "ревизор: чисто");
    parts.push(`итог: ${VERDICTS[record.verdict] || record.verdict}`);
    if (record.input_tokens != null) parts.push(`${record.input_tokens} → ${record.output_tokens ?? "—"} токенов`);
    return parts.join(" · ");
  };

  const VERDICTS = {
    held: "удержал сам",
    caught: "уступил — поймал код",
    false_alarm: "регулярка сработала, ревизор снял",
    clean: "конфликта нет",
  };

  const checkKind = (record) => {
    if (record.verdict === "caught") return "caught";
    if (record.verdict === "held") return "held";
    return "done";
  };

  const formatTurnMeta = (turn, finishReason) =>
    `≈${turn.question_tokens} вопрос · ${turn.context_tokens ?? "—"} контекст · ${turn.answer_tokens ?? "—"} ответ${finishReason ? ` · ${finishReason}` : ""}`;

  const clearLog = () => {
    log.querySelectorAll("[data-turn], [data-marker]").forEach((node) => node.remove());
    emptyNote.hidden = false;
  };

  const renderLog = (state, { restored = false } = {}) => {
    clearLog();
    const turns = state.turns || [];
    const byTurn = new Map();
    (state.checks || []).forEach((record) => byTurn.set(record.after_turn, record));
    let turnIndex = 0;
    (state.messages || []).forEach((message) => {
      const turn = addTurn(message.role, message.content, { reveal: false, restored });
      if (message.role === "assistant") {
        turnIndex += 1;
        const stats = turns[turnIndex - 1];
        if (stats) turn.querySelector("[data-turn-meta]").textContent = formatTurnMeta(stats);
        const record = byTurn.get(turnIndex);
        if (record) addMarker(describeCheck(record), checkKind(record));
      }
    });
  };

  /* ── Панель ─────────────────────────────────────────────────────────── */

  const escapeHtml = (text) =>
    String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);

  const setText = (selector, value) => {
    document.querySelectorAll(selector).forEach((node) => {
      node.textContent = value;
    });
  };

  const renderInvariants = (items) => {
    listBox.textContent = "";
    items.forEach((item, index) => {
      const row = document.createElement("li");
      row.className = "day14-item";
      row.classList.toggle("is-off", !item.enabled);
      row.innerHTML =
        `<span class="day14-item__num">${index + 1}</span>` +
        `<span class="day14-item__body">` +
        `<span class="day14-item__kind">${escapeHtml(item.kind_title)}${item.preset ? "" : " · свой"}</span>` +
        `<span class="day14-item__rule">${escapeHtml(item.rule)}</span>` +
        `<small class="day14-item__check">${item.checkable ? `регулярка: <code>${escapeHtml(item.check)}</code>` : "только ревизор"}</small>` +
        `</span>`;
      const tools = document.createElement("span");
      tools.className = "day14-item__tools";
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.textContent = item.enabled ? "[x]" : "[ ]";
      toggle.title = item.enabled ? "Выключить: останется в списке, но не уйдёт в промпт" : "Включить";
      toggle.addEventListener("click", () => apply(() => postJson(urls.toggle, { id: item.id, enabled: !item.enabled }), item.enabled ? `«${item.id}» выключен` : `«${item.id}» включён`));
      tools.append(toggle);
      if (!item.preset) {
        const drop = document.createElement("button");
        drop.type = "button";
        drop.textContent = "×";
        drop.title = "Удалить";
        drop.addEventListener("click", () => apply(() => postJson(urls.delete, { id: item.id }, "DELETE"), `«${item.id}» удалён`));
        tools.append(drop);
      }
      row.prepend(tools);
      listBox.append(row);
    });
  };

  const renderChecks = (checks) => {
    checksBody.textContent = "";
    const list = checks || [];
    if (checksEmpty) checksEmpty.hidden = list.length > 0;
    list.forEach((record, index) => {
      const row = document.createElement("tr");
      const verdict = VERDICTS[record.verdict] || record.verdict;
      row.className = record.verdict === "caught" ? "is-caught" : record.verdict === "held" ? "is-held" : "";
      row.innerHTML =
        `<td>${index + 1}</td>` +
        `<td class="day14-table__q">${escapeHtml(record.question)}</td>` +
        `<td>${record.refused ? "да" : "нет"}</td>` +
        `<td>${record.named?.length ? escapeHtml(record.named.join(", ")) : "—"}</td>` +
        `<td>${record.regex?.length ? escapeHtml(record.regex.map((v) => `${v.invariant} («${v.evidence}»)`).join(", ")) : "чисто"}</td>` +
        `<td>${record.reviewer_error ? "ошибка" : record.reviewer == null ? "—" : record.reviewer.length ? escapeHtml(record.reviewer.map((v) => v.invariant).join(", ")) : "чисто"}</td>` +
        `<td>${verdict}</td>`;
      checksBody.append(row);
    });
  };

  const renderDebug = (state) => {
    const set = (key, value) => setText(`[data-debug="${key}"]`, value);
    const summary = state.summary || {};
    ["turns", "refused", "named", "alarms", "false_alarms", "caught"].forEach((key) => set(key, summary[key] ?? 0));
    set("invariants_tokens", state.invariants_tokens ?? 0);
    set("side_requests", state.side?.requests ?? 0);
    set("side_tokens", (state.side?.input_tokens || 0) + (state.side?.output_tokens || 0));
    if (systemBox) systemBox.textContent = state.system_text || "";
    if (hint) {
      const active = (state.invariants || []).filter((item) => item.enabled).length;
      hint.textContent = `в промпте: ${active} из ${(state.invariants || []).length} инвариантов`;
    }
  };

  const renderState = (state, { rebuildLog = false, restored = false } = {}) => {
    if (!state) return;
    current = state;
    if (rebuildLog) renderLog(state, { restored });
    renderInvariants(state.invariants || []);
    renderChecks(state.checks);
    renderDebug(state);
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
    if (addButton) addButton.disabled = isLoading;
    if (clearButton) clearButton.disabled = isLoading;
    if (scenarioButton) scenarioButton.disabled = isLoading || scenarioRunning;
    document.querySelectorAll(".day14-item__tools button, #day14-form input, #day14-form textarea, #day14-form button").forEach((el) => {
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
        } else if (event.type === "invariants_inspected") {
          const seen = event.inspection;
          const text = seen.refused
            ? `отказал сам${seen.named.length ? `, назвал: ${seen.named.join(", ")}` : ""} · регулярка: ${seen.violations.length ? "поймала" : "чисто"}`
            : `не отказал · регулярка: ${seen.violations.length ? `поймала ${seen.violations.map((v) => v.invariant).join(", ")}` : "чисто"}`;
          pendingMarker = addMarker(`${text} · ревизор смотрит…`, "pending");
          revealBlock(pendingMarker);
        } else if (event.type === "invariants_checked") {
          setMarker(pendingMarker || addMarker("", "pending"), describeCheck(event.record), checkKind(event.record));
          pendingMarker = null;
        } else if (event.type === "invariants_failed") {
          setMarker(pendingMarker || addMarker("", "pending"), `проверка не записалась: ${event.error}`, "failed");
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
      meta.textContent = overflow ? `≈${overflow.estimated} > лимит ${overflow.limit}` : "запрос откатился из стека";
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

  /* ── Инварианты: добавить, выключить, удалить ───────────────────────── */

  const apply = async (work, message) => {
    if (busy) return false;
    setLoading(true);
    try {
      renderState(await work());
      if (message) status.textContent = message;
      return true;
    } catch (error) {
      status.textContent = error.message || "Не удалось";
      return false;
    } finally {
      setLoading(false);
    }
  };

  if (addButton && addForm) {
    addButton.addEventListener("click", () => {
      addForm.hidden = !addForm.hidden;
      if (!addForm.hidden) addForm.querySelector("input[name=id]")?.focus();
    });
    addForm.querySelector("[data-form-close]")?.addEventListener("click", () => {
      addForm.hidden = true;
    });
    addForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(addForm);
      const payload = {
        id: String(data.get("id") || "").trim().toLowerCase(),
        rule: String(data.get("rule") || "").trim(),
        kind: String(data.get("kind") || "decision"),
        check: String(data.get("check") || "").trim() || null,
      };
      apply(async () => {
        const state = await postJson(urls.add, payload);
        addForm.querySelectorAll("input[type=text], textarea").forEach((field) => {
          field.value = "";
        });
        addForm.hidden = true;
        return state;
      }, `Инвариант «${payload.id}» добавлен`);
    });
  }

  if (clearButton) {
    clearButton.addEventListener("click", () => apply(() => postJson(urls.clear, undefined, "DELETE"), "Журнал очищен"));
  }

  resetButton.addEventListener("click", async () => {
    if (busy) return;
    setLoading(true);
    status.textContent = "Начинаю новый диалог…";
    try {
      renderState(await postJson(urls.reset, {}), { rebuildLog: true });
      status.textContent = "Новый диалог: стек пуст, инварианты и журнал на месте";
    } catch (error) {
      status.textContent = error.message || "Не удалось сбросить диалог";
    } finally {
      setLoading(false);
      messageField.focus({ preventScroll: true });
    }
  });

  /* ── Сценарий ───────────────────────────────────────────────────────── */

  if (scenarioButton) {
    scenarioButton.addEventListener("click", async () => {
      if (busy || scenarioRunning) return;
      scenarioRunning = true;
      scenarioButton.disabled = true;
      try {
        const response = await fetch(scenarioButton.dataset.scenarioUrl);
        const scenario = await readJson(response);
        if (!response.ok) throw new Error(scenario?.error || "Сценарий не загрузился");
        const messages = scenario.messages || [];
        for (let i = 0; i < messages.length; i += 1) {
          status.textContent = `${scenario.title}: реплика ${i + 1} из ${messages.length}…`;
          const ok = await ask(messages[i]);
          if (!ok) return;
          await new Promise((resolve) => setTimeout(resolve, 900));
        }
        status.textContent = "Сценарий пройден: смотрите журнал внизу";
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
