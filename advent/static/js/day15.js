/*
 * День 15: контролируемые переходы.
 * Кнопки этапов запрашивают любой переход — разрешает таблица. Отказ
 * приходит как 409 с причиной и ложится в журнал красным. Модель может
 * попросить переход строкой [переход: …] — её тоже прогоняют через ворота.
 */
(() => {
  const form = document.querySelector("#agent-form");
  if (!form) return;

  const messageField = document.querySelector("#agent-message");
  const submitButton = document.querySelector("#agent-submit");
  const resetButton = document.querySelector("#agent-reset");
  const newTaskButton = document.querySelector("#day15-new-task");
  const scenarioButton = document.querySelector("#day15-scenario");
  const status = document.querySelector("#agent-status");
  const log = document.querySelector("#agent-log");
  const emptyNote = log.querySelector("[data-log-empty]");
  const turnTemplate = document.querySelector("#agent-turn-template");
  const markerTemplate = document.querySelector("#day15-marker-template");
  const stepper = document.querySelector("[data-stepper]");
  const planBox = document.querySelector("[data-plan]");
  const journalBox = document.querySelector("[data-journal]");
  const buttonsBox = document.querySelector("[data-buttons]");
  const retellBox = document.querySelector("[data-retell]");
  const stageHint = document.querySelector("[data-stage-hint]");
  const jumpBox = document.querySelector("[data-jump]");
  const verdictBox = document.querySelector("[data-verdict]");
  const systemBox = document.querySelector("[data-system-text]");
  const fileBox = document.querySelector("[data-task-file]");
  const initialScript = document.querySelector("#agent-initial");

  const urls = {
    chat: form.dataset.chatUrl,
    state: form.dataset.stateUrl,
    reset: form.dataset.resetUrl,
    transition: form.dataset.transitionUrl,
    pause: form.dataset.pauseUrl,
    resume: form.dataset.resumeUrl,
    newTask: form.dataset.newTaskUrl,
  };

  const RESUME_MESSAGE = "Продолжаем.";
  const STAGE_TITLES = { planning: "планирование", execution: "выполнение", validation: "проверка", done: "готово" };

  let current = null;

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
    marker.classList.toggle("is-cut", kind === "cut");
    marker.classList.toggle("is-refused", kind === "refused");
    marker.classList.toggle("is-held", kind === "held");
    marker.querySelector("[data-marker-text]").textContent = text;
  };

  const describeRecord = (record) => {
    const cost = ` · ${record.input_tokens ?? "—"} → ${record.output_tokens ?? "—"} токенов`;
    return `наблюдатель (${STAGE_TITLES[record.stage] || record.stage}): ${record.applied}${cost}`;
  };

  const ACTORS = { user: "кнопка", model: "модель", observer: "наблюдатель" };

  // Переход или отказ одной строкой: кто просил, куда, почему.
  const describeTransition = (t) =>
    t.refused
      ? `отказано ${ACTORS[t.actor] || t.actor}: ${t.from} → ${t.to} — ${t.reason}`
      : `переход ${t.from} → ${t.to} (${ACTORS[t.actor] || t.actor}): ${t.reason}`;

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
    (state.task?.observer || []).forEach((record) => {
      const list = byTurn.get(record.after_turn) || [];
      list.push({ text: describeRecord(record), kind: "done" });
      byTurn.set(record.after_turn, list);
    });
    (state.task?.log || [])
      .filter((t) => t.actor !== "user" && (t.refused || t.from !== t.to))
      .forEach((t) => {
        const list = byTurn.get(t.turn) || [];
        list.push({ text: describeTransition(t), kind: t.refused ? "refused" : "done" });
        byTurn.set(t.turn, list);
      });
    let turnIndex = 0;
    (state.messages || []).forEach((message) => {
      const turn = addTurn(message.role, message.content, { reveal: false, restored });
      if (message.role === "assistant") {
        turnIndex += 1;
        const stats = turns[turnIndex - 1];
        if (stats) turn.querySelector("[data-turn-meta]").textContent = formatTurnMeta(stats);
        (byTurn.get(turnIndex) || []).forEach((m) => addMarker(m.text, m.kind));
      }
    });
  };

  /* ── Панель задачи ──────────────────────────────────────────────────── */

  const escapeHtml = (text) =>
    String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);

  const setText = (selector, value) => {
    document.querySelectorAll(selector).forEach((node) => {
      node.textContent = value;
    });
  };

  const renderTask = (task) => {
    const set = (key, value) => setText(`[data-task="${key}"]`, value);
    stepper.querySelectorAll("li").forEach((item) => {
      const index = ["planning", "execution", "validation", "done"].indexOf(item.dataset.stage);
      const currentIndex = task ? ["planning", "execution", "validation", "done"].indexOf(task.stage) : -1;
      // done — конечное состояние, а не этап с работой: пришли в него —
      // значит всё пройдено, и галочка стоит на всех четырёх.
      const finished = Boolean(task) && task.stage === "done";
      item.classList.toggle("is-current", Boolean(task) && item.dataset.stage === task.stage);
      item.classList.toggle("is-passed", Boolean(task) && (index < currentIndex || finished));
      item.classList.toggle("is-paused", Boolean(task) && task.paused && item.dataset.stage === task.stage);
    });

    if (!task) {
      set("title", "—");
      set("goal", "Задачи ещё нет.");
      set("stage", "—");
      set("step", "—");
      set("expected", "—");
      set("allowed", "—");
      if (stageHint) stageHint.textContent = "задачи нет — первая реплика её создаст";
      planBox.innerHTML = '<li class="day15-plan__empty">Плана ещё нет.</li>';
      journalBox.innerHTML = '<li class="day15-journal__empty">Переходов ещё не было.</li>';
      renderButtons(null);
      return;
    }

    set("title", task.title);
    set("goal", task.goal);
    set("stage", `${task.stage} · ${task.stage_title.toLowerCase()}${task.paused ? " · на паузе" : ""}`);
    const stepText =
      task.stage === "execution" && task.steps.length
        ? `${task.current + 1} из ${task.steps.length} — ${task.steps[task.current].text}`
        : task.stage === "planning"
          ? task.steps.length
            ? `план из ${task.steps.length} шагов, ждёт утверждения`
            : "плана ещё нет"
          : "—";
    set("step", stepText);
    set("expected", task.expected);
    set("allowed", task.paused ? "— (пауза)" : task.allowed?.length ? task.allowed.join(", ") : "никуда");
    if (stageHint) {
      stageHint.textContent = task.paused
        ? `задача на паузе на этапе ${STAGE_TITLES[task.stage]}`
        : `этап: ${STAGE_TITLES[task.stage]} · ожидаем: ${task.expected}`;
    }

    planBox.textContent = "";
    if (!task.steps.length) {
      planBox.innerHTML = '<li class="day15-plan__empty">Плана ещё нет — агент предложит его на этапе планирования.</li>';
    }
    task.steps.forEach((step, index) => {
      const row = document.createElement("li");
      const isCurrent = task.stage === "execution" && index === task.current;
      row.classList.toggle("is-done", step.done);
      row.classList.toggle("is-current", isCurrent);
      row.innerHTML =
        `<span class="day15-plan__mark">${step.done ? "[x]" : isCurrent ? "[>]" : "[ ]"}</span>` +
        `<span class="day15-plan__text">${escapeHtml(step.text)}${step.note ? `<small>${escapeHtml(step.note)}</small>` : ""}</span>`;
      planBox.append(row);
    });

    journalBox.textContent = "";
    if (!task.log.length) journalBox.innerHTML = '<li class="day15-journal__empty">Переходов ещё не было.</li>';
    task.log.forEach((item) => {
      const row = document.createElement("li");
      row.classList.toggle("is-refused", Boolean(item.refused));
      const arrow = item.refused ? `✗ ${item.from} → ${item.to}` : item.from === item.to ? `${item.from} ‖` : `${item.from} → ${item.to}`;
      row.innerHTML =
        `<span class="day15-journal__arrow">${escapeHtml(arrow)}</span>` +
        `<span>${escapeHtml(item.reason)} <em>· ${escapeHtml(ACTORS[item.actor] || item.actor)}</em></span>` +
        `<small>ход ${item.turn}</small>`;
      journalBox.append(row);
    });

    renderButtons(task);
  };

  // Пауза и продолжение — не переходы, а заморозка. А кнопки этапов
  // показываются все: любой можно запросить, разрешит таблица.
  const renderButtons = (task) => {
    const allow = new Set();
    if (task) {
      if (task.paused) allow.add("resume");
      else if (task.stage !== "done") allow.add("pause");
    }
    buttonsBox.querySelectorAll("[data-action]").forEach((button) => {
      button.hidden = !allow.has(button.dataset.action);
    });
    buttonsBox.hidden = allow.size === 0;
    if (jumpBox) {
      const allowed = new Set(task && !task.paused ? task.allowed || [] : []);
      jumpBox.querySelectorAll("[data-jump-to]").forEach((button) => {
        const target = button.dataset.jumpTo;
        button.classList.toggle("is-allowed", allowed.has(target));
        button.classList.toggle("is-current", Boolean(task) && task.stage === target);
        button.disabled = !task || busy || task.stage === target;
      });
      jumpBox.hidden = !task;
    }
  };

  const renderRetell = (state) => {
    if (!retellBox) return;
    const check = state.retelling;
    retellBox.hidden = !check;
    if (!check) return;
    retellBox.classList.toggle("is-bad", check.retold);
    retellBox.textContent = check.retold
      ? `пересказал задачу заново ✗ — ${check.phrases.length ? `«${check.phrases.join("», «")}»` : `${Math.round(check.share * 100)}% слов цели повторены`}`
      : `без пересказа ✓ — продолжил с места (${Math.round(check.share * 100)}% слов цели в ответе)`;
  };

  const renderDebug = (state) => {
    const set = (key, value) => setText(`[data-debug="${key}"]`, value);
    set("stack", (state.messages || []).length);
    set("task_tokens", state.task?.tokens ?? 0);
    set("side_requests", state.side?.requests ?? 0);
    const summary = state.summary || {};
    ["transitions", "refused", "refused_user", "refused_model", "refused_observer"].forEach((key) => set(key, summary[key] ?? 0));
    if (systemBox) systemBox.textContent = state.system_text || "";
    if (fileBox) {
      const file = state.task_file;
      fileBox.textContent = file ? `${file.file.replace(/^.*?-task/, "…-task")} · ${file.exists ? `${file.bytes} B` : "нет файла"}` : "";
    }
  };

  const renderState = (state, { rebuildLog = false, restored = false } = {}) => {
    if (!state) return;
    current = state;
    if (rebuildLog) renderLog(state, { restored });
    renderTask(state.task);
    renderRetell(state);
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

  const postJson = async (url, payload) => {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload ?? {}),
    });
    const data = await readJson(response);
    // 409 с полным состоянием — отказ ворот: не ошибка, а результат.
    if (response.status === 409 && data?.transition) return data;
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
    buttonsBox.querySelectorAll("button").forEach((button) => {
      button.disabled = isLoading;
    });
    if (jumpBox) jumpBox.querySelectorAll("button").forEach((button) => {
      button.disabled = isLoading || !current?.task || button.classList.contains("is-current");
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
        } else if (event.type === "task_created") {
          const marker = addMarker(`задача создана: «${event.task.title}» · этап planning`, "cut");
          log.insertBefore(marker, turn.previousElementSibling);
          renderTask(event.task);
        } else if (event.type === "task_transition") {
          const marker = addMarker(describeTransition(event.record), event.record.refused ? "refused" : "done");
          revealBlock(marker);
          renderTask(event.task);
        } else if (event.type === "task_jump_check") {
          if (event.refused && !event.wanted) addMarker("агент отказал в прыжке и остался на этапе", "held");
        } else if (event.type === "task_observing") {
          pendingMarker = addMarker(`наблюдатель смотрит на ответ (${STAGE_TITLES[event.stage] || event.stage})…`, "pending");
          revealBlock(pendingMarker);
        } else if (event.type === "task_updated") {
          setMarker(pendingMarker || addMarker("", "pending"), describeRecord(event.record));
          pendingMarker = null;
          renderTask(event.task);
        } else if (event.type === "task_failed") {
          setMarker(pendingMarker || addMarker("", "pending"), `наблюдатель не ответил: ${event.error} — автомат остался где был`, "failed");
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

  /* ── Переходы по кнопкам ────────────────────────────────────────────── */

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

  const showVerdict = (transition) => {
    if (!verdictBox) return;
    verdictBox.hidden = !transition;
    if (!transition) return;
    verdictBox.classList.toggle("is-refused", Boolean(transition.refused));
    verdictBox.textContent = describeTransition(transition);
  };

  // Запросить переход: легальный сделается, нелегальный — отказ с причиной.
  const jump = (target) =>
    apply(async () => {
      const state = await postJson(urls.transition, { to: target });
      showVerdict(state.transition);
      return state;
    }, null).then((ok) => {
      const t = current?.transition;
      if (t) status.textContent = t.refused ? `Отказано: ${t.reason}` : `Переход ${t.from} → ${t.to}`;
      return ok && !t?.refused;
    });

  const ACTIONS = {
    approve: () => jump("execution"),
    accept: () => jump("done"),
    jump: (target) => jump(target),
    pause: () => apply(() => postJson(urls.pause), "Пауза: состояние сохранено"),
    // Продолжение — не только флаг: агент сразу получает реплику и должен
    // ответить следующим шагом, а не вопросом «о чём была задача».
    resume: async () => {
      const ok = await apply(() => postJson(urls.resume), "Продолжаем…");
      if (ok) await ask(RESUME_MESSAGE);
      return ok;
    },
  };

  buttonsBox.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => ACTIONS[button.dataset.action]?.());
  });
  if (jumpBox) {
    jumpBox.querySelectorAll("[data-jump-to]").forEach((button) => {
      button.addEventListener("click", () => jump(button.dataset.jumpTo));
    });
  }

  if (newTaskButton) {
    newTaskButton.addEventListener("click", () =>
      apply(() => postJson(urls.newTask), "Задача снята: следующая реплика создаст новую"),
    );
  }

  const resetDialogue = async () => {
    setLoading(true);
    status.textContent = "Начинаю новый диалог…";
    try {
      renderState(await postJson(urls.reset), { rebuildLog: true });
      addMarker("новый диалог: стек пуст, задача на месте", "cut");
      status.textContent = "Новый диалог: стек пуст, задача осталась";
      return true;
    } catch (error) {
      status.textContent = error.message || "Не удалось сбросить диалог";
      return false;
    } finally {
      setLoading(false);
      messageField.focus({ preventScroll: true });
    }
  };

  resetButton.addEventListener("click", () => {
    if (!busy) resetDialogue();
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
        const steps = scenario.steps || [];
        for (let i = 0; i < steps.length; i += 1) {
          const step = steps[i];
          status.textContent = `${scenario.title}: шаг ${i + 1} из ${steps.length} (${step.kind})…`;
          let ok = true;
          if (step.kind === "say") ok = await ask(step.text);
          else if (step.kind === "reset") ok = await resetDialogue();
          else if (step.kind === "jump") {
            // Отказ ворот — ожидаемый результат, сценарий идёт дальше.
            await jump(step.text);
          } else ok = await ACTIONS[step.kind]?.();
          if (!ok) return;
          // Пауза между шагами — чтобы на видео было видно, что произошло.
          await new Promise((resolve) => setTimeout(resolve, 900));
        }
        status.textContent = "Сценарий пройден: смотрите журнал переходов справа — отказы красным";
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
