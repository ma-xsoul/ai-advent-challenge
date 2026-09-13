/*
 * Чат с агентом — общий для дней 6+. Обязательные элементы: форма
 * #agent-form, лента #agent-log, дебаг-панель [data-debug]. Необязательные,
 * если день их даёт: кнопка #agent-restart и поля [data-storage].
 */
(() => {
  const form = document.querySelector("#agent-form");
  if (!form) return;

  const messageField = document.querySelector("#agent-message");
  const submitButton = document.querySelector("#agent-submit");
  const resetButton = document.querySelector("#agent-reset");
  const restartButton = document.querySelector("#agent-restart");
  const status = document.querySelector("#agent-status");
  const log = document.querySelector("#agent-log");
  const emptyNote = log.querySelector("[data-log-empty]");
  const turnTemplate = document.querySelector("#agent-turn-template");
  const stackView = document.querySelector("#agent-stack");
  const initialScript = document.querySelector("#agent-initial");
  const turnsBody = document.querySelector("[data-turns]");
  const turnsEmpty = document.querySelector("[data-turns-empty]");
  const gaugeFill = document.querySelector("[data-gauge-fill]");
  const limitForm = document.querySelector("#agent-limit-form");
  const limitField = document.querySelector("#agent-limit");
  const scenarioButton = document.querySelector("#agent-scenario");
  const chatUrl = form.dataset.chatUrl;
  const resetUrl = form.dataset.resetUrl;
  const restartUrl = form.dataset.restartUrl;
  const limitUrl = form.dataset.limitUrl;

  // Ответ приходит потоком: сырой текст храним отдельно от разметки
  // и перерисовываем не чаще одного раза за кадр.
  const rawBodies = new WeakMap();
  const pendingRender = new Set();
  let renderFrame = null;

  const renderRaw = (output) => {
    const raw = rawBodies.get(output) || "";
    if (typeof window.renderMarkdown === "function") {
      output.innerHTML = window.renderMarkdown(raw);
    } else {
      output.textContent = raw;
    }
  };

  /* Ведение экрана за печатающимся ответом — та же механика, что в дне 5. */
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
    const overflow =
      followTarget.getBoundingClientRect().bottom - (window.innerHeight - FOLLOW_MARGIN);
    if (overflow <= 1) return;
    window.scrollBy({ top: overflow, behavior: "auto" });
    lastAutoScroll = Math.round(window.scrollY);
  };

  const startFollowing = (element) => {
    followTarget = element;
    followEnabled = true;
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

  const appendBody = (output, chunk) => {
    setBody(output, (rawBodies.get(output) || "") + chunk);
  };

  const revealBlock = (element) => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    element.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest" });
  };

  const addTurn = (role, text, { reveal = true } = {}) => {
    emptyNote.hidden = true;
    const turn = turnTemplate.content.firstElementChild.cloneNode(true);
    turn.dataset.turn = role;
    turn.querySelector("[data-turn-role]").textContent = role === "user" ? "вы" : "агент";
    setBody(turn.querySelector("[data-turn-body]"), text);
    log.append(turn);
    if (reveal) revealBlock(turn);
    return turn;
  };

  const clearLog = () => {
    log.querySelectorAll("[data-turn]").forEach((turn) => turn.remove());
    emptyNote.hidden = false;
  };

  // Лента строится из стека агента: так после перезапуска сервера страница
  // показывает восстановленный диалог, а не пустоту.
  const renderMessages = (messages, { restored = false } = {}) => {
    clearLog();
    (messages || []).forEach((message) => {
      const turn = addTurn(message.role, message.content, { reveal: false });
      if (restored) turn.classList.add("is-restored");
    });
  };

  const setText = (selector, value) => {
    document.querySelectorAll(selector).forEach((target) => {
      target.textContent = value;
    });
  };

  // Дебаг-панель — зеркало agent.snapshot(): обновляется после каждого хода.
  const renderSnapshot = (snapshot) => {
    if (!snapshot) return;
    const set = (key, value) => setText(`[data-debug="${key}"]`, value);
    set("name", snapshot.name);
    set("model", snapshot.config.model);
    set("temperature", snapshot.config.temperature);
    set("max_tokens", snapshot.config.max_tokens);
    set("thinking", snapshot.config.thinking ? "enabled" : "disabled");
    set("system_prompt", snapshot.config.system_prompt || "—");
    set("stack", snapshot.messages.length);
    set("requests", snapshot.usage.requests);
    set("input_tokens", snapshot.usage.input_tokens);
    set("output_tokens", snapshot.usage.output_tokens);
    set("total_tokens", snapshot.usage.total_tokens);
    set("agents_in_registry", snapshot.agents_in_registry ?? "—");
    if (stackView) stackView.textContent = JSON.stringify(snapshot.messages, null, 2);
    renderStorage(snapshot.storage);
    renderContext(snapshot.context);
    renderTurns(snapshot.turns);
  };

  // Заполнение контекста: оценка стека против лимита агента.
  const renderContext = (context) => {
    if (!context) return;
    setText('[data-debug="context_estimated"]', context.estimated ?? 0);
    setText('[data-debug="context_limit"]', context.limit ?? "∞");
    if (limitField && document.activeElement !== limitField) {
      limitField.value = context.limit ?? "";
    }
    if (gaugeFill) {
      const ratio = context.limit ? Math.min(1, (context.estimated || 0) / context.limit) : 0;
      gaugeFill.style.setProperty("--fill", String(ratio));
      gaugeFill.classList.toggle("is-hot", ratio >= 0.8);
      gaugeFill.classList.toggle("is-full", ratio >= 1);
    }
  };

  // Таблица ходов — три числа задания: вопрос (≈), стек, ответ.
  const renderTurns = (turns) => {
    if (!turnsBody) return;
    turnsBody.textContent = "";
    const list = turns || [];
    if (turnsEmpty) turnsEmpty.hidden = list.length > 0;
    const maxContext = Math.max(1, ...list.map((t) => t.context_tokens || 0));
    list.forEach((turn) => {
      const row = document.createElement("tr");
      const ratio = (turn.context_tokens || 0) / maxContext;
      row.innerHTML =
        `<td>${turn.index}</td>` +
        `<td>≈ ${turn.question_tokens}</td>` +
        `<td>${turn.context_tokens ?? "—"}</td>` +
        `<td>${turn.answer_tokens ?? "—"}</td>` +
        `<td>${turn.total_tokens}</td>` +
        `<td class="day8-table__bar"><span style="--w: ${ratio}"></span></td>`;
      turnsBody.append(row);
    });
  };

  const formatTurnMeta = (reply, turn) => {
    if (turn) {
      return `≈${turn.question_tokens} вопрос · ${turn.context_tokens ?? "—"} стек · ${turn.answer_tokens ?? "—"} ответ · ${reply.finish_reason}`;
    }
    return `${reply.input_tokens ?? "—"} → ${reply.output_tokens ?? "—"} токенов · ${reply.finish_reason}`;
  };

  // Панель хранилища есть только у дней с диском.
  const renderStorage = (storage) => {
    const set = (key, value) => setText(`[data-storage="${key}"]`, value);
    if (!storage) {
      set("file", "—");
      set("saved_at", "—");
      set("messages", "—");
      set("bytes", "—");
      return;
    }
    set("file", storage.file || "—");
    document.querySelectorAll('[data-storage="file"]').forEach((el) => {
      el.title = storage.path || "";
    });
    set("saved_at", storage.saved_at ? formatTime(storage.saved_at) : "—");
    set("messages", storage.messages ?? "—");
    set("bytes", typeof storage.bytes === "number" ? `${storage.bytes} Б` : "—");
  };

  const formatTime = (iso) => {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString("ru-RU");
  };

  let scenarioRunning = false;

  const setLoading = (isLoading) => {
    messageField.disabled = isLoading;
    submitButton.disabled = isLoading;
    resetButton.disabled = isLoading;
    if (restartButton) restartButton.disabled = isLoading;
    if (scenarioButton) scenarioButton.disabled = isLoading || scenarioRunning;
  };

  const readJson = async (response) => {
    try {
      return await response.json();
    } catch {
      return {};
    }
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = messageField.value.trim();
    if (!message) {
      messageField.focus();
      return;
    }
    messageField.value = "";
    await sendMessage(message);
  });

  // Возвращает true, если ответ дошёл; false — если ход оборвался.
  const sendMessage = async (message) => {
    addTurn("user", message);
    const turn = addTurn("assistant", "");
    const body = turn.querySelector("[data-turn-body]");
    const meta = turn.querySelector("[data-turn-meta]");
    body.classList.add("is-streaming");
    startFollowing(body);
    setLoading(true);
    status.textContent = "Агент думает…";
    // Объявлен до try: блочная область let не пустила бы его в catch.
    let overflow = null;

    try {
      const response = await fetch(chatUrl, {
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
      await readNdjson(response.body, (streamEvent) => {
        if (streamEvent.type === "delta") {
          appendBody(body, streamEvent.content);
        } else if (streamEvent.type === "done") {
          finished = true;
          setBody(body, streamEvent.reply.content);
          meta.textContent = formatTurnMeta(streamEvent.reply, streamEvent.turn);
          renderSnapshot(streamEvent.snapshot);
          if (streamEvent.save_error) status.textContent = streamEvent.save_error;
        } else if (streamEvent.type === "error") {
          overflow = streamEvent.overflow || null;
          renderSnapshot(streamEvent.snapshot);
          throw new Error(streamEvent.error);
        }
      });
      if (!finished) throw new Error("Поток оборвался до конца ответа");
      status.textContent = "Готово";
      return true;
    } catch (error) {
      turn.classList.add(overflow ? "is-overflow" : "is-failed");
      setBody(body, overflow ? `Переполнение контекста: ${error.message}` : `Ошибка: ${error.message}`);
      meta.textContent = overflow
        ? `≈${overflow.estimated} > лимит ${overflow.limit} · запрос не отправлен`
        : "запрос откатился из стека";
      status.textContent = overflow ? "Переполнение" : "Ошибка";
      return false;
    } finally {
      body.classList.remove("is-streaming");
      setLoading(false);
      followEnabled = false;
      followTarget = null;
      messageField.focus({ preventScroll: true });
    }
  };

  // Лимит контекста: новый агент с тем же стеком, но другим окном.
  if (limitForm && limitUrl) {
    limitForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const raw = limitField.value.trim();
      const limit = raw === "" ? null : Number(raw);
      setLoading(true);
      status.textContent = limit === null ? "Снимаю лимит…" : `Ставлю лимит ${limit}…`;
      try {
        const response = await fetch(limitUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ limit }),
        });
        const data = await readJson(response);
        if (!response.ok) throw new Error(data?.error || "Не удалось сменить лимит");
        renderSnapshot(data);
        status.textContent = limit === null ? "Лимит снят" : `Лимит ${limit} токенов`;
      } catch (error) {
        status.textContent = error.message || "Не удалось сменить лимит";
      } finally {
        setLoading(false);
      }
    });
  }

  // Сценарий: реплики уходят по одной через обычный чат, стоп на первой ошибке.
  if (scenarioButton && scenarioButton.dataset.scenarioUrl) {
    scenarioButton.addEventListener("click", async () => {
      scenarioRunning = true;
      scenarioButton.disabled = true;
      try {
        const response = await fetch(scenarioButton.dataset.scenarioUrl);
        const scenario = await readJson(response);
        if (!response.ok || !Array.isArray(scenario.messages)) {
          throw new Error(scenario?.error || "Сценарий не загрузился");
        }
        for (let i = 0; i < scenario.messages.length; i += 1) {
          status.textContent = `Сценарий: реплика ${i + 1} из ${scenario.messages.length}…`;
          const ok = await sendMessage(scenario.messages[i]);
          if (!ok) {
            status.textContent = `Сценарий остановлен на реплике ${i + 1} из ${scenario.messages.length}`;
            return;
          }
        }
        status.textContent = `Сценарий пройден: ${scenario.messages.length} реплик`;
      } catch (error) {
        status.textContent = error.message || "Сценарий не выполнен";
      } finally {
        scenarioRunning = false;
        scenarioButton.disabled = false;
      }
    });
  }

  resetButton.addEventListener("click", async () => {
    setLoading(true);
    status.textContent = "Создаю нового агента…";
    try {
      const response = await fetch(resetUrl, { method: "POST" });
      const snapshot = await readJson(response);
      if (!response.ok) throw new Error(snapshot?.error || "Не удалось сбросить агента");
      clearLog();
      renderSnapshot(snapshot);
      status.textContent = "Новый агент, стек пуст";
    } catch (error) {
      status.textContent = error.message || "Не удалось сбросить агента";
    } finally {
      setLoading(false);
      messageField.focus({ preventScroll: true });
    }
  });

  // Имитация перезапуска: агент выкидывается из памяти и читается с диска.
  if (restartButton && restartUrl) {
    restartButton.addEventListener("click", async () => {
      setLoading(true);
      status.textContent = "Выгружаю агента из памяти и читаю с диска…";
      try {
        const response = await fetch(restartUrl, { method: "POST" });
        const data = await readJson(response);
        if (!response.ok) throw new Error(data?.error || "Не удалось перезапустить агента");
        renderMessages(data.messages, { restored: true });
        renderSnapshot(data);
        status.textContent = data.messages.length
          ? `Восстановлено из файла: ${data.messages.length} сообщений`
          : "На диске ничего не было — агент новый";
      } catch (error) {
        status.textContent = error.message || "Не удалось перезапустить агента";
      } finally {
        setLoading(false);
        messageField.focus({ preventScroll: true });
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

  messageField.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  // Страница открылась — показать то, что у агента уже есть.
  if (initialScript) {
    try {
      const initial = JSON.parse(initialScript.textContent);
      renderMessages(initial.messages, { restored: Boolean(initial.storage) });
      renderSnapshot(initial);
    } catch {
      // Слепок не разобрался — лента останется пустой, панель покажет серверные значения.
    }
  }
})();
