/*
 * День 12: два агента, один вопрос, два профиля.
 * Профиль дорожки выбирается в списке над лентой; карточка под ним
 * показывает, что именно ушло в промпт; строка под лентой — что агент
 * учёл сам: обращение, список, код, длина.
 */
(() => {
  const form = document.querySelector("#agent-form");
  if (!form) return;

  const messageField = document.querySelector("#agent-message");
  const submitButton = document.querySelector("#agent-submit");
  const resetButton = document.querySelector("#agent-reset");
  const scenarioButton = document.querySelector("#day12-scenario");
  const newProfileButton = document.querySelector("#day12-new-profile");
  const editor = document.querySelector("#day12-editor");
  const profileForm = document.querySelector("#day12-profile-form");
  const status = document.querySelector("#agent-status");
  const turnTemplate = document.querySelector("#agent-turn-template");
  const memoryCounts = document.querySelector("[data-memory-counts]");
  const initialScript = document.querySelector("#agent-initial");

  const urls = {
    chat: form.dataset.chatUrl,
    state: form.dataset.stateUrl,
    reset: form.dataset.resetUrl,
    lane: form.dataset.laneUrl,
    profile: form.dataset.profileUrl,
  };

  const LANE_KEYS = ["a", "b"];
  let current = null;

  /* ── Свой выпадающий список (как в дне 11), но с подменой пунктов ────── */

  const setupSelect = (root, onPick) => {
    const input = root.querySelector("input[type=hidden]");
    const button = root.querySelector("[data-select-button]");
    const label = root.querySelector("[data-select-value]");
    const list = root.querySelector("[role=listbox]");
    let options = [];

    const isOpen = () => !list.hidden;
    const close = ({ focusButton = false } = {}) => {
      list.hidden = true;
      button.setAttribute("aria-expanded", "false");
      if (focusButton) button.focus();
    };
    const open = () => {
      if (!options.length) return;
      list.hidden = false;
      button.setAttribute("aria-expanded", "true");
      (options.find((option) => option.dataset.value === input.value) || options[0]).focus();
    };
    const mark = () => {
      options.forEach((option) => option.setAttribute("aria-selected", String(option.dataset.value === input.value)));
    };
    const pick = (option) => {
      const changed = option.dataset.value !== input.value;
      input.value = option.dataset.value;
      label.textContent = option.dataset.label;
      mark();
      close({ focusButton: true });
      if (changed) onPick(option.dataset.value);
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
    document.addEventListener("click", (event) => {
      if (isOpen() && !root.contains(event.target)) close();
    });

    return {
      setOptions(items) {
        list.textContent = "";
        options = items.map((item) => {
          const option = document.createElement("li");
          option.className = "day12-select__option";
          option.setAttribute("role", "option");
          option.tabIndex = -1;
          option.dataset.value = item.id;
          option.dataset.label = item.title;
          option.innerHTML = `<span>${escapeHtml(item.title)}</span><small>${item.preset ? "встроенный" : "свой"} · ≈${item.tokens}</small>`;
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
          list.append(option);
          return option;
        });
        mark();
      },
      setValue(value) {
        input.value = value;
        const option = options.find((item) => item.dataset.value === value);
        label.textContent = option ? option.dataset.label : value;
        mark();
      },
      setDisabled(disabled) {
        button.disabled = disabled;
        if (disabled) close();
      },
    };
  };

  const lanes = {};
  LANE_KEYS.forEach((key) => {
    const section = document.querySelector(`.day12-lane[data-lane="${key}"]`);
    lanes[key] = {
      key,
      section,
      log: section.querySelector("[data-lane-log]"),
      empty: section.querySelector("[data-log-empty]"),
      card: section.querySelector("[data-lane-card]"),
      prompt: section.querySelector("[data-lane-prompt]"),
      check: section.querySelector("[data-lane-check]"),
      select: setupSelect(section.querySelector("[data-select]"), (profile) => bindLane(key, profile)),
      snapshot: null,
    };
  });

  /* ── Потоковая отрисовка и ведение экрана — как в дне 9 ─────────────── */

  const rawBodies = new WeakMap();
  const pendingRender = new Set();
  let renderFrame = null;

  const renderRaw = (output) => {
    const raw = rawBodies.get(output) || "";
    if (typeof window.renderMarkdown === "function") output.innerHTML = window.renderMarkdown(raw);
    else output.textContent = raw;
  };

  const FOLLOW_MARGIN = 90;
  const followTargets = new Set();
  let followEnabled = false;
  let lastAutoScroll = -1;

  const lowestTarget = () => {
    let bottom = -Infinity;
    followTargets.forEach((target) => {
      if (target.offsetParent === null) return;
      bottom = Math.max(bottom, target.getBoundingClientRect().bottom);
    });
    return bottom;
  };

  const followBottom = () => {
    if (!followEnabled || !followTargets.size) return;
    const overflow = lowestTarget() - (window.innerHeight - FOLLOW_MARGIN);
    if (overflow <= 1) return;
    window.scrollBy({ top: overflow, behavior: "auto" });
    lastAutoScroll = Math.round(window.scrollY);
  };

  const syncFollowToUser = () => {
    if (followTargets.size) followEnabled = lowestTarget() - window.innerHeight < 160;
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

  /* ── Ленты ──────────────────────────────────────────────────────────── */

  const addTurn = (lane, role, text, { reveal = true, restored = false } = {}) => {
    lane.empty.hidden = true;
    const turn = turnTemplate.content.firstElementChild.cloneNode(true);
    turn.dataset.turn = role;
    turn.querySelector("[data-turn-role]").textContent = role === "user" ? "вы" : "агент";
    if (restored) turn.classList.add("is-restored");
    setBody(turn.querySelector("[data-turn-body]"), text);
    lane.log.append(turn);
    if (reveal) revealBlock(turn);
    return turn;
  };

  const formatTurnMeta = (turn, finishReason) =>
    `≈${turn.question_tokens} вопрос · ${turn.context_tokens ?? "—"} контекст · ${turn.answer_tokens ?? "—"} ответ${finishReason ? ` · ${finishReason}` : ""}`;

  const clearLog = (lane) => {
    lane.log.querySelectorAll("[data-turn]").forEach((node) => node.remove());
    lane.empty.hidden = false;
  };

  const renderLaneLog = (lane, snapshot, { restored = false } = {}) => {
    clearLog(lane);
    const turns = snapshot.turns || [];
    let turnIndex = 0;
    (snapshot.messages || []).forEach((message) => {
      const turn = addTurn(lane, message.role, message.content, { reveal: false, restored });
      if (message.role === "assistant") {
        turnIndex += 1;
        const stats = turns[turnIndex - 1];
        if (stats) turn.querySelector("[data-turn-meta]").textContent = formatTurnMeta(stats);
      }
    });
  };

  /* ── Карточка профиля, промпт, признаки ─────────────────────────────── */

  const escapeHtml = (text) =>
    String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);

  const setText = (root, selector, value) => {
    root.querySelectorAll(selector).forEach((node) => {
      node.textContent = value;
    });
  };

  const profileById = (id) => (current?.profiles || []).find((profile) => profile.id === id) || null;

  const renderCard = (lane, profile) => {
    const set = (key, value) => setText(lane.card, `[data-card="${key}"]`, value);
    if (!profile) {
      ["address", "tone", "format", "limits", "pipeline"].forEach((key) => set(key, "—"));
      return;
    }
    set("address", profile.address);
    set("tone", profile.tone);
    set("format", profile.format);
    set("limits", profile.limits.length ? profile.limits.join("; ") : "—");
    set("pipeline", profile.pipeline.length ? profile.pipeline.map((step, i) => `${i + 1}) ${step}`).join(" ") : "—");
  };

  // Что агент учёл сам — по последнему ответу дорожки.
  const describeAddress = (check) => {
    if (check.address_ok === true) return `обращение: ${check.address} ✓`;
    if (check.address_ok === false) return `обращение: ${check.address} ✗ (профиль просил ${check.expected_address})`;
    if (check.expected_address && check.address === "нет") return `обращения нет (профиль: ${check.expected_address})`;
    return `обращение: ${check.address}`;
  };

  const describeCheck = (check) => {
    if (!check) return "";
    const parts = [
      describeAddress(check),
      check.named ? "по имени" : null,
      check.list_items ? `список: ${check.list_items}` : "без списка",
      check.has_code ? "код: да" : "код: нет",
      `${check.paragraphs} абз.`,
      `≈${check.tokens} токенов`,
    ];
    return "учёл сам → " + parts.filter(Boolean).join(" · ");
  };

  const renderLaneStats = (lane, snapshot) => {
    lane.snapshot = snapshot;
    const profile = profileById(snapshot.profile);
    const set = (key, value) => setText(lane.section, `[data-lane-stat="${key}"]`, value);
    set("profile_tokens", profile ? profile.tokens : 0);
    set("requests", snapshot.usage?.requests ?? 0);
    set("total", snapshot.usage?.total_tokens ?? 0);
    lane.select.setValue(snapshot.profile);
    renderCard(lane, profile);
    lane.prompt.textContent = snapshot.system_text || "";
    lane.check.hidden = !snapshot.check;
    lane.check.textContent = describeCheck(snapshot.check);
  };

  const renderMemory = (memory) => {
    if (!memoryCounts) return;
    const counts = memory?.counts || {};
    const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
    memoryCounts.textContent = total
      ? `chat ${counts.chat ?? 0} · task ${counts.task ?? 0} · user ${counts.user ?? 0} · ≈${memory.tokens} токенов`
      : "слои пусты — сходите на день 11, если хотите увидеть профиль поверх памяти";
  };

  const renderState = (state, { rebuildLogs = false, restored = false } = {}) => {
    if (!state) return;
    current = state;
    LANE_KEYS.forEach((key) => {
      lanes[key].select.setOptions(state.profiles || []);
    });
    LANE_KEYS.forEach((key) => {
      const snapshot = state.lanes?.[key];
      if (!snapshot) return;
      if (rebuildLogs) renderLaneLog(lanes[key], snapshot, { restored });
      renderLaneStats(lanes[key], snapshot);
    });
    renderMemory(state.memory);
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
    if (newProfileButton) newProfileButton.disabled = isLoading;
    if (scenarioButton) scenarioButton.disabled = isLoading || scenarioRunning;
    LANE_KEYS.forEach((key) => lanes[key].select.setDisabled(isLoading));
    if (profileForm) profileForm.querySelectorAll("input, textarea, button").forEach((el) => (el.disabled = isLoading));
  };

  const sendToLane = async (lane, message) => {
    addTurn(lane, "user", message);
    const turn = addTurn(lane, "assistant", "");
    const body = turn.querySelector("[data-turn-body]");
    const meta = turn.querySelector("[data-turn-meta]");
    body.classList.add("is-streaming");
    followTargets.add(body);
    followEnabled = true;
    let overflow = null;

    try {
      const response = await fetch(urls.chat, {
        method: "POST",
        headers: { Accept: "application/x-ndjson", "Content-Type": "application/json" },
        body: JSON.stringify({ message, lane: lane.key }),
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
        } else if (event.type === "done") {
          finished = true;
          setBody(body, event.reply.content);
          meta.textContent = event.turn ? formatTurnMeta(event.turn, event.reply.finish_reason) : "";
          renderLaneStats(lane, event.snapshot);
          if (event.save_error) status.textContent = event.save_error;
        } else if (event.type === "error") {
          overflow = event.overflow || null;
          throw new Error(event.error);
        }
      });
      if (!finished) throw new Error("Поток оборвался до конца ответа");
      return true;
    } catch (error) {
      turn.classList.add(overflow ? "is-overflow" : "is-failed");
      setBody(body, overflow ? `Переполнение контекста: ${error.message}` : `Ошибка: ${error.message}`);
      meta.textContent = overflow ? `≈${overflow.estimated} > лимит ${overflow.limit}` : "запрос откатился из стека";
      return false;
    } finally {
      body.classList.remove("is-streaming");
      followTargets.delete(body);
    }
  };

  // Один вопрос — обеим дорожкам параллельно.
  const sendToAll = async (message) => {
    setLoading(true);
    status.textContent = "Агенты думают…";
    try {
      const results = await Promise.all(LANE_KEYS.map((key) => sendToLane(lanes[key], message)));
      await refreshState();
      const failed = LANE_KEYS.filter((key, i) => !results[i]);
      status.textContent = failed.length ? `Не ответила дорожка ${failed.map((k) => k.toUpperCase()).join(", ")}` : "Готово";
      return failed.length === 0;
    } finally {
      setLoading(false);
      followEnabled = false;
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
    await sendToAll(message);
  });

  messageField.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  /* ── Профили ────────────────────────────────────────────────────────── */

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

  const bindLane = (lane, profile) =>
    apply(() => postJson(urls.lane, { lane, profile }), `Дорожка ${lane.toUpperCase()}: профиль «${profileById(profile)?.title || profile}»`);

  const openEditor = () => {
    if (!editor) return;
    editor.hidden = false;
    revealBlock(editor);
    profileForm?.querySelector("input[name=id]")?.focus();
  };

  const closeEditor = () => {
    if (editor) editor.hidden = true;
  };

  if (newProfileButton) newProfileButton.addEventListener("click", openEditor);
  editor?.querySelector("[data-editor-close]")?.addEventListener("click", closeEditor);

  if (profileForm) {
    profileForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(profileForm);
      const lines = (name) =>
        String(data.get(name) || "")
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean);
      const payload = {
        id: String(data.get("id") || "").trim().toLowerCase(),
        title: String(data.get("title") || "").trim(),
        address: String(data.get("address") || "").trim(),
        tone: String(data.get("tone") || "").trim(),
        format: String(data.get("format") || "").trim(),
        limits: lines("limits"),
        pipeline: lines("pipeline"),
      };
      apply(async () => {
        const state = await postJson(urls.profile, payload);
        closeEditor();
        return state;
      }, `Профиль «${payload.title}» сохранён — выберите его у дорожки`);
    });
  }

  resetButton.addEventListener("click", async () => {
    if (busy) return;
    setLoading(true);
    status.textContent = "Начинаю новый диалог…";
    try {
      renderState(await postJson(urls.reset, {}), { rebuildLogs: true });
      status.textContent = "Новый диалог: стеки пусты, профили и память на месте";
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
          status.textContent = `${scenario.title}: вопрос ${i + 1} из ${messages.length}…`;
          const ok = await sendToAll(messages[i]);
          if (!ok) break;
        }
        status.textContent = "Сценарий пройден: сравните ответы дорожек";
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
      renderState(initial, { rebuildLogs: true, restored: Boolean(initial.lanes?.a?.storage) });
    } catch {
      // Слепок не разобрался — ленты останутся пустыми.
    }
  }
})();
