/*
 * День 9: две дорожки — без сжатия и со сжатием — на одном поле ввода.
 * Каждая реплика уходит обоим агентам параллельно, ответы текут в свои
 * ленты, а после хода страница перечитывает состояние и обновляет сравнение.
 */
(() => {
  const form = document.querySelector("#agent-form");
  if (!form) return;

  const messageField = document.querySelector("#agent-message");
  const submitButton = document.querySelector("#agent-submit");
  const resetButton = document.querySelector("#agent-reset");
  const scenarioButtons = [...document.querySelectorAll("[data-scenario-url]")];
  const compressButton = document.querySelector("#day9-compress");
  const policyForm = document.querySelector("#day9-policy");
  const keepField = document.querySelector("#day9-keep");
  const everyField = document.querySelector("#day9-every");
  const limitField = document.querySelector("#day9-limit");
  const status = document.querySelector("#agent-status");
  const turnTemplate = document.querySelector("#agent-turn-template");
  const markerTemplate = document.querySelector("#day9-marker-template");
  const turnsBody = document.querySelector("[data-turns]");
  const turnsEmpty = document.querySelector("[data-turns-empty]");
  const summaryBox = document.querySelector("[data-summary]");
  const initialScript = document.querySelector("#agent-initial");
  const urls = {
    chat: form.dataset.chatUrl,
    state: form.dataset.stateUrl,
    reset: form.dataset.resetUrl,
    policy: form.dataset.policyUrl,
    compress: form.dataset.compressUrl,
  };

  const LANE_KEYS = ["plain", "summary"];
  const REASONS = { every: "по счётчику", overflow: "не влезали в лимит", manual: "по кнопке" };

  const lanes = {};
  LANE_KEYS.forEach((key) => {
    const section = document.querySelector(`.day9-lane[data-lane="${key}"]`);
    lanes[key] = {
      key,
      section,
      log: section.querySelector("[data-lane-log]"),
      empty: section.querySelector("[data-log-empty]"),
      snapshot: null,
    };
  });

  /* ── Потоковая отрисовка: сырой текст отдельно, разметка раз в кадр ───── */

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

  /* Экран едет за нижним из печатающихся ответов — та же механика, что в дне 5. */
  const FOLLOW_MARGIN = 90;
  const followTargets = new Set();
  let followEnabled = false;
  let lastAutoScroll = -1;

  const lowestBottom = () => {
    let bottom = -Infinity;
    followTargets.forEach((target) => {
      if (target.offsetParent === null) return;
      bottom = Math.max(bottom, target.getBoundingClientRect().bottom);
    });
    return bottom;
  };

  const followBottom = () => {
    if (!followEnabled || followTargets.size === 0) return;
    const overflow = lowestBottom() - (window.innerHeight - FOLLOW_MARGIN);
    if (overflow <= 1) return;
    window.scrollBy({ top: overflow, behavior: "auto" });
    lastAutoScroll = Math.round(window.scrollY);
  };

  const syncFollowToUser = () => {
    if (followTargets.size) followEnabled = lowestBottom() - window.innerHeight < 160;
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

  /* ── Лента дорожки ──────────────────────────────────────────────────────── */

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

  const addMarker = (lane, text, kind = "done") => {
    lane.empty.hidden = true;
    const marker = markerTemplate.content.firstElementChild.cloneNode(true);
    marker.classList.toggle("is-pending", kind === "pending");
    marker.classList.toggle("is-failed", kind === "failed");
    marker.querySelector("[data-marker-text]").textContent = text;
    lane.log.append(marker);
    return marker;
  };

  const setMarker = (marker, text, kind = "done") => {
    marker.classList.toggle("is-pending", kind === "pending");
    marker.classList.toggle("is-failed", kind === "failed");
    marker.querySelector("[data-marker-text]").textContent = text;
  };

  const describeCompression = (record) => {
    const cost = `${record.input_tokens ?? "—"} → ${record.output_tokens ?? "—"} токенов`;
    const truncated = record.truncated ? " · ⚠ конспект оборван по потолку токенов" : "";
    return `история сжата: ${record.compressed} сообщ. → конспект ≈${record.summary_tokens} токенов · ${cost} · ${REASONS[record.reason] || record.reason} · в контексте осталось ${record.kept}${truncated}`;
  };

  const clearLog = (lane) => {
    lane.log.querySelectorAll("[data-turn], [data-marker]").forEach((node) => node.remove());
    lane.empty.hidden = false;
  };

  // Лента строится из стека агента и записей о сжатии: отметка встаёт
  // после того хода, за которым история свернулась.
  const renderLaneLog = (lane, snapshot, { restored = false } = {}) => {
    clearLog(lane);
    const messages = snapshot.messages || [];
    const compressions = snapshot.compressions || [];
    const turns = snapshot.turns || [];
    const byTurn = new Map();
    compressions.forEach((record) => {
      const list = byTurn.get(record.after_turn) || [];
      list.push(record);
      byTurn.set(record.after_turn, list);
    });
    (byTurn.get(0) || []).forEach((record) => addMarker(lane, describeCompression(record)));
    let turnIndex = 0;
    messages.forEach((message) => {
      const turn = addTurn(lane, message.role, message.content, { reveal: false, restored });
      if (message.role === "assistant") {
        turnIndex += 1;
        const stats = turns[turnIndex - 1];
        if (stats) turn.querySelector("[data-turn-meta]").textContent = formatTurnMeta(stats);
        (byTurn.get(turnIndex) || []).forEach((record) => addMarker(lane, describeCompression(record)));
      }
    });
  };

  const formatTurnMeta = (turn, finishReason) =>
    `≈${turn.question_tokens} вопрос · ${turn.context_tokens ?? "—"} стек · ${turn.answer_tokens ?? "—"} ответ${finishReason ? ` · ${finishReason}` : ""}`;

  /* ── Панели: статистика дорожек, сравнение, конспект, таблица ходов ─── */

  const setText = (root, selector, value) => {
    root.querySelectorAll(selector).forEach((target) => {
      target.textContent = value;
    });
  };

  const renderLaneStats = (lane, snapshot) => {
    lane.snapshot = snapshot;
    const set = (key, value) => setText(lane.section, `[data-lane-stat="${key}"]`, value);
    set("memory", snapshot.messages.length);
    set("context", snapshot.context?.messages ?? snapshot.messages.length);
    set("estimated", snapshot.context?.estimated ?? 0);
    set("total", snapshot.usage.total_tokens);
    set("compressions", (snapshot.compressions || []).length);
  };

  const formatSigned = (value) => (value > 0 ? `−${value}` : `+${Math.abs(value)}`);

  const renderCompare = (compare) => {
    if (!compare) return;
    const root = document.querySelector(".day9-compare");
    LANE_KEYS.forEach((key) => {
      const lane = compare.lanes[key];
      ["turns", "requests", "summary_requests", "input_tokens", "output_tokens", "summary_tokens", "total_tokens"].forEach((field) => {
        setText(root, `[data-cmp="${key}.${field}"]`, lane[field]);
      });
      setText(root, `[data-cmp="${key}.facts"]`, lane.checked ? `${lane.facts_found} / ${lane.facts_total}` : "—");
    });

    // Факты: две отметки на строку.
    compare.facts.forEach((fact) => {
      const row = root.querySelector(`[data-fact="${CSS.escape(fact.label)}"]`);
      if (!row) return;
      LANE_KEYS.forEach((key) => {
        const mark = row.querySelector(`[data-fact-lane="${key}"]`);
        const checked = compare.lanes[key].checked;
        mark.textContent = checked ? (fact[key] ? "✓" : "✗") : "·";
        mark.classList.toggle("is-found", checked && fact[key]);
        mark.classList.toggle("is-missing", checked && !fact[key]);
      });
      row.classList.toggle("is-lost", compare.lanes.plain.checked && compare.lanes.summary.checked && fact.plain && !fact.summary);
    });

    // Вердикт: экономия и уцелевшие факты.
    const tokens = root.querySelector('[data-verdict="tokens"]');
    const tokensNote = root.querySelector('[data-verdict="tokens-note"]');
    const plain = compare.lanes.plain;
    const summary = compare.lanes.summary;
    tokens.classList.remove("is-good", "is-bad");
    if (!plain.total_tokens || !summary.total_tokens) {
      tokens.textContent = "—";
      tokensNote.textContent = "прогоните сценарий на обеих дорожках";
    } else {
      const percent = compare.saved_percent === null ? "" : ` (${formatSigned(compare.saved_percent)} %)`;
      tokens.textContent = `${formatSigned(compare.saved_tokens)} токенов${percent}`;
      if (!compare.same_turns) {
        tokensNote.textContent = `дорожки прошли разное число ходов: ${plain.turns} и ${summary.turns} — сравнение расхода условное`;
      } else if (!summary.summary_requests) {
        tokensNote.textContent = "сжатия ещё не было — разница только в длине ответов";
      } else {
        tokens.classList.add(compare.saved_tokens >= 0 ? "is-good" : "is-bad");
        tokensNote.textContent = compare.saved_tokens >= 0
          ? `со сжатием дешевле: ${summary.total_tokens} против ${plain.total_tokens}, из них ${summary.summary_tokens} на конспект`
          : `со сжатием дороже: ${summary.summary_tokens} токенов ушло на конспект — на коротком диалоге он съел экономию`;
      }
    }

    const facts = root.querySelector('[data-verdict="facts"]');
    const factsNote = root.querySelector('[data-verdict="facts-note"]');
    facts.classList.remove("is-good", "is-bad");
    if (!plain.checked && !summary.checked) {
      facts.textContent = "—";
      factsNote.textContent = `${compare.facts.length} фактов в сценарии — проверяются по последнему ответу`;
    } else {
      const part = (lane) => (lane.checked ? `${lane.facts_found}/${lane.facts_total}` : "—");
      facts.textContent = `${part(plain)} → ${part(summary)}`;
      if (!plain.facts_found && !summary.facts_found) {
        factsNote.textContent = "в последних ответах фактов сценария нет — прогоните сценарий до конца";
      } else if (plain.checked && summary.checked) {
        facts.classList.add(summary.facts_found >= plain.facts_found ? "is-good" : "is-bad");
        const lost = compare.facts.filter((f) => f.plain && !f.summary).map((f) => f.label);
        factsNote.textContent = lost.length
          ? `потеряно при сжатии: ${lost.join(", ")}`
          : "конспект ничего не потерял";
      }
    }
  };

  const renderSummary = (snapshot) => {
    if (!summaryBox) return;
    const title = summaryBox.querySelector("[data-summary-title]");
    const text = summaryBox.querySelector("[data-summary-text]");
    const summary = snapshot?.summary;
    if (!summary) {
      title.textContent = "Конспект: пока нет";
      text.textContent = "Правый агент ещё не сжимал историю.";
      return;
    }
    title.textContent = `Конспект: ${summary.covers} сообщений свёрнуты · ≈${summary.tokens} токенов · сжатий: ${summary.compressions} · на них ушло ${summary.input_tokens + summary.output_tokens} токенов`;
    text.textContent = summary.text;
  };

  // Таблица ходов: обе дорожки в одной строке, конспект — своей строкой справа.
  const renderTurns = () => {
    if (!turnsBody) return;
    turnsBody.textContent = "";
    const plain = lanes.plain.snapshot?.turns || [];
    const summary = lanes.summary.snapshot?.turns || [];
    const compressions = lanes.summary.snapshot?.compressions || [];
    const count = Math.max(plain.length, summary.length);
    if (turnsEmpty) turnsEmpty.hidden = count > 0;
    const maxContext = Math.max(1, ...plain.map((t) => t.context_tokens || 0), ...summary.map((t) => t.context_tokens || 0));

    const bar = (turn, lane) => {
      if (!turn) return `<td class="day9-bar is-empty" data-lane="${lane}">—</td>`;
      const ratio = (turn.context_tokens || 0) / maxContext;
      return `<td class="day9-bar" data-lane="${lane}"><span class="day9-bar__track"><span class="day9-bar__fill"><span style="--w: ${ratio}"></span></span><span>${turn.context_tokens ?? "—"}</span></span></td>`;
    };
    const cell = (turn, field) => (turn ? `<td>${turn[field] ?? "—"}</td>` : `<td class="is-empty">—</td>`);

    const compressionRow = (record) => {
      const row = document.createElement("tr");
      row.innerHTML =
        `<td>↳ ${REASONS[record.reason] || record.reason}</td>` +
        `<td class="is-empty"></td><td class="is-empty"></td>` +
        `<td class="day9-note" colspan="2">${record.compressed} сообщ. → конспект ≈${record.summary_tokens}</td>` +
        `<td>${record.input_tokens ?? "—"} → ${record.output_tokens ?? "—"}</td>`;
      return row;
    };

    compressions.filter((r) => r.after_turn === 0).forEach((record) => turnsBody.append(compressionRow(record)));
    for (let i = 0; i < count; i += 1) {
      const row = document.createElement("tr");
      row.innerHTML =
        `<td>${i + 1}</td>` +
        bar(plain[i], "plain") +
        cell(plain[i], "answer_tokens") +
        bar(summary[i], "summary") +
        cell(summary[i], "answer_tokens") +
        `<td class="is-empty"></td>`;
      turnsBody.append(row);
      compressions.filter((r) => r.after_turn === i + 1).forEach((record) => turnsBody.append(compressionRow(record)));
    }
  };

  const renderPolicy = (policy) => {
    if (!policy) return;
    const fill = (field, value) => {
      if (field && document.activeElement !== field) field.value = value ?? "";
    };
    fill(keepField, policy.keep_last);
    fill(everyField, policy.every);
    fill(limitField, policy.limit);
  };

  const renderState = (state, { rebuildLogs = false, restored = false } = {}) => {
    if (!state) return;
    LANE_KEYS.forEach((key) => {
      const snapshot = state.lanes?.[key];
      if (!snapshot) return;
      if (rebuildLogs) renderLaneLog(lanes[key], snapshot, { restored });
      renderLaneStats(lanes[key], snapshot);
    });
    renderPolicy(state.policy);
    renderCompare(state.compare);
    renderSummary(state.lanes?.summary);
    renderTurns();
  };

  const readJson = async (response) => {
    try {
      return await response.json();
    } catch {
      return {};
    }
  };

  const refreshState = async () => {
    try {
      const response = await fetch(urls.state);
      const state = await readJson(response);
      if (response.ok) renderState(state);
    } catch {
      // Сравнение обновится после следующего хода.
    }
  };

  /* ── Ходы ───────────────────────────────────────────────────────────────── */

  let busy = false;
  let scenarioRunning = false;

  const setLoading = (isLoading) => {
    busy = isLoading;
    messageField.disabled = isLoading;
    submitButton.disabled = isLoading;
    resetButton.disabled = isLoading;
    if (compressButton) compressButton.disabled = isLoading;
    scenarioButtons.forEach((button) => {
      button.disabled = isLoading || scenarioRunning;
    });
    policyForm.querySelectorAll("button, input").forEach((el) => {
      el.disabled = isLoading;
    });
  };

  // Один ход одной дорожки. Возвращает true, если ответ дошёл.
  const sendToLane = async (lane, message) => {
    addTurn(lane, "user", message);
    const turn = addTurn(lane, "assistant", "");
    const body = turn.querySelector("[data-turn-body]");
    const meta = turn.querySelector("[data-turn-meta]");
    body.classList.add("is-streaming");
    followTargets.add(body);
    followEnabled = true;
    let overflow = null;
    let pendingMarker = null;

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
        } else if (event.type === "compressing") {
          // Сжатие ради лимита идёт до запроса — отметка встаёт перед этим
          // ходом; сжатие по счётчику идёт после ответа — под ним.
          pendingMarker = addMarker(lane, `сжимаю историю: ${event.messages} сообщ. → конспект…`, "pending");
          if (event.reason === "overflow") lane.log.insertBefore(pendingMarker, turn.previousElementSibling);
          revealBlock(pendingMarker);
        } else if (event.type === "compressed") {
          const marker = pendingMarker || addMarker(lane, "", "pending");
          setMarker(marker, describeCompression(event.compression));
          pendingMarker = null;
        } else if (event.type === "compression_failed") {
          const marker = pendingMarker || addMarker(lane, "", "pending");
          setMarker(marker, `конспект не получился: ${event.error} — ход состоялся, попробую на следующем`, "failed");
          pendingMarker = null;
        } else if (event.type === "done") {
          finished = true;
          setBody(body, event.reply.content);
          meta.textContent = event.turn ? formatTurnMeta(event.turn, event.reply.finish_reason) : "";
          renderLaneStats(lane, event.snapshot);
          if (event.save_error) status.textContent = event.save_error;
        } else if (event.type === "error") {
          overflow = event.overflow || null;
          if (event.snapshot) renderLaneStats(lane, event.snapshot);
          throw new Error(event.error);
        }
      });
      if (!finished) throw new Error("Поток оборвался до конца ответа");
      return true;
    } catch (error) {
      if (pendingMarker) setMarker(pendingMarker, `сжатие не удалось: ${error.message}`, "failed");
      turn.classList.add(overflow ? "is-overflow" : "is-failed");
      setBody(body, overflow ? `Переполнение контекста: ${error.message}` : `Ошибка: ${error.message}`);
      meta.textContent = overflow
        ? `≈${overflow.estimated} > лимит ${overflow.limit} · запрос не отправлен`
        : "запрос откатился из стека";
      return false;
    } finally {
      body.classList.remove("is-streaming");
      followTargets.delete(body);
    }
  };

  // Реплика уходит обеим дорожкам сразу; ждём обе, потом перечитываем сравнение.
  const sendToAll = async (message, targets = LANE_KEYS) => {
    setLoading(true);
    status.textContent = "Агенты думают…";
    try {
      const results = await Promise.all(targets.map((key) => sendToLane(lanes[key], message)));
      const outcome = {};
      targets.forEach((key, i) => {
        outcome[key] = results[i];
      });
      await refreshState();
      const failed = targets.filter((key) => !outcome[key]);
      status.textContent = failed.length
        ? `Не ответил: ${failed.map((key) => (key === "plain" ? "без сжатия" : "со сжатием")).join(", ")}`
        : "Готово";
      return outcome;
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

  // Сценарий: реплики уходят по одной обеим дорожкам. Дорожка, которая
  // споткнулась, дальше не идёт — вторая доходит до конца.
  scenarioButtons.forEach((scenarioButton) => {
    scenarioButton.addEventListener("click", async () => {
      if (busy) return;
      scenarioRunning = true;
      scenarioButtons.forEach((button) => {
        button.disabled = true;
      });
      try {
        const response = await fetch(scenarioButton.dataset.scenarioUrl);
        const scenario = await readJson(response);
        if (!response.ok || !Array.isArray(scenario.messages)) {
          throw new Error(scenario?.error || "Сценарий не загрузился");
        }
        let alive = [...LANE_KEYS];
        for (let i = 0; i < scenario.messages.length && alive.length; i += 1) {
          status.textContent = `Сценарий: реплика ${i + 1} из ${scenario.messages.length}…`;
          const outcome = await sendToAll(scenario.messages[i], alive);
          alive = alive.filter((key) => outcome[key]);
        }
        status.textContent = alive.length === LANE_KEYS.length
          ? `Сценарий пройден: ${scenario.messages.length} реплик на обеих дорожках`
          : alive.length
            ? `Сценарий пройден только на дорожке «${alive[0] === "plain" ? "без сжатия" : "со сжатием"}»`
            : "Сценарий остановлен: обе дорожки споткнулись";
      } catch (error) {
        status.textContent = error.message || "Сценарий не выполнен";
      } finally {
        scenarioRunning = false;
        scenarioButtons.forEach((button) => {
          button.disabled = false;
        });
      }
    });
  });

  /* ── Политика, ручное сжатие, сброс ─────────────────────────────────────── */

  policyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const limitRaw = limitField.value.trim();
    const payload = {
      keep_last: Number(keepField.value),
      every: Number(everyField.value),
      limit: limitRaw === "" ? null : Number(limitRaw),
    };
    setLoading(true);
    status.textContent = "Меняю политику…";
    try {
      const response = await fetch(urls.policy, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await readJson(response);
      if (!response.ok) throw new Error(data?.error || "Не удалось сменить политику");
      renderState(data);
      status.textContent = `N = ${payload.keep_last}, сжимать каждые ${payload.every}, лимит ${payload.limit ?? "снят"}`;
    } catch (error) {
      status.textContent = error.message || "Не удалось сменить политику";
    } finally {
      setLoading(false);
    }
  });

  if (compressButton) {
    compressButton.addEventListener("click", async () => {
      if (busy) return;
      const lane = lanes.summary;
      setLoading(true);
      status.textContent = "Сжимаю историю правого агента…";
      const marker = addMarker(lane, "сжимаю историю по кнопке…", "pending");
      revealBlock(marker);
      try {
        const response = await fetch(urls.compress, { method: "POST" });
        const data = await readJson(response);
        if (!response.ok) throw new Error(data?.error || "Не удалось сжать историю");
        if (data.compression) {
          setMarker(marker, describeCompression(data.compression));
          status.textContent = "История сжата";
        } else {
          marker.remove();
          status.textContent = "Сжимать нечего: в контексте только последние N";
        }
        renderState(data.state);
      } catch (error) {
        setMarker(marker, `конспект не получился: ${error.message}`, "failed");
        status.textContent = error.message || "Не удалось сжать историю";
      } finally {
        setLoading(false);
      }
    });
  }

  resetButton.addEventListener("click", async () => {
    if (busy) return;
    setLoading(true);
    status.textContent = "Создаю двух новых агентов…";
    try {
      const response = await fetch(urls.reset, { method: "POST" });
      const state = await readJson(response);
      if (!response.ok) throw new Error(state?.error || "Не удалось сбросить агентов");
      renderState(state, { rebuildLogs: true });
      status.textContent = "Два новых агента, стеки пусты";
    } catch (error) {
      status.textContent = error.message || "Не удалось сбросить агентов";
    } finally {
      setLoading(false);
      messageField.focus({ preventScroll: true });
    }
  });

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

  // Страница открылась — показать то, что у агентов уже есть.
  if (initialScript) {
    try {
      const initial = JSON.parse(initialScript.textContent);
      const restored = Boolean(initial.lanes?.plain?.storage || initial.lanes?.summary?.storage);
      renderState(initial, { rebuildLogs: true, restored });
    } catch {
      // Слепок не разобрался — ленты останутся пустыми.
    }
  }
})();
