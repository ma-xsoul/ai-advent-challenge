/*
 * День 10: один диалог, три стратегии контекста и ветки от checkpoint.
 * Лента показывает активную ветку; переключатель меняет конфиг агента
 * на живом диалоге; после сценария прогон ложится строкой в таблицу.
 */
(() => {
  const form = document.querySelector("#agent-form");
  if (!form) return;

  const messageField = document.querySelector("#agent-message");
  const submitButton = document.querySelector("#agent-submit");
  const resetButton = document.querySelector("#agent-reset");
  const checkpointButton = document.querySelector("#day10-checkpoint");
  const recordButton = document.querySelector("#day10-record");
  const clearRunsButton = document.querySelector("#day10-clear-runs");
  const branchScenarioButton = document.querySelector("#day10-branch-scenario");
  const scenarioButtons = [...document.querySelectorAll("[data-scenario-url]")];
  const strategyForm = document.querySelector("#day10-strategy");
  const nField = document.querySelector("#day10-n");
  const status = document.querySelector("#agent-status");
  const log = document.querySelector("#agent-log");
  const emptyNote = log.querySelector("[data-log-empty]");
  const turnTemplate = document.querySelector("#agent-turn-template");
  const markerTemplate = document.querySelector("#day10-marker-template");
  const branchesBox = document.querySelector("[data-branches]");
  const factsBox = document.querySelector("[data-facts]");
  const factsEmpty = document.querySelector("[data-facts-empty]");
  const treeBox = document.querySelector("[data-tree]");
  const runsBody = document.querySelector("[data-runs]");
  const runsEmpty = document.querySelector("[data-runs-empty]");
  const initialScript = document.querySelector("#agent-initial");
  const urls = {
    chat: form.dataset.chatUrl,
    state: form.dataset.stateUrl,
    reset: form.dataset.resetUrl,
    strategy: form.dataset.strategyUrl,
    checkpoint: form.dataset.checkpointUrl,
    fork: form.dataset.forkUrl,
    switch: form.dataset.switchUrl,
    runs: form.dataset.runsUrl,
    clearRuns: clearRunsButton ? clearRunsButton.dataset.clearUrl : null,
  };

  const STRATEGY_TITLES = { full: "Полная история", window: "Sliding Window", facts: "Sticky Facts", summary: "Summary" };

  let current = null; // последнее состояние: дерево, слепок активной ветки, стратегия

  /* ── Потоковая отрисовка и ведение экрана — как в дне 9 ─────────────── */

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

  const appendBody = (output, chunk) => {
    setBody(output, (rawBodies.get(output) || "") + chunk);
  };

  const revealBlock = (element) => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    element.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest" });
  };

  /* ── Лента активной ветки ───────────────────────────────────────────── */

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
    marker.classList.toggle("is-branch", kind === "branch");
    marker.querySelector("[data-marker-text]").textContent = text;
  };

  const describeFacts = (update) => {
    const parts = [];
    if (update.added.length) parts.push(`+ ${update.added.join(", ")}`);
    if (update.changed.length) parts.push(`~ ${update.changed.join(", ")}`);
    if (update.removed.length) parts.push(`− ${update.removed.join(", ")}`);
    const diff = parts.length ? parts.join(" · ") : "без изменений";
    const keys = Object.keys(update.facts || {}).length;
    return `факты обновлены: ${diff} · ${keys} ключей ≈${update.facts_tokens} токенов · ${update.input_tokens ?? "—"} → ${update.output_tokens ?? "—"} токенов`;
  };

  const describeCompression = (record) =>
    `история сжата: ${record.compressed} сообщ. → конспект ≈${record.summary_tokens} токенов · ${record.input_tokens ?? "—"} → ${record.output_tokens ?? "—"}`;

  const formatTurnMeta = (turn, finishReason) =>
    `≈${turn.question_tokens} вопрос · ${turn.context_tokens ?? "—"} стек · ${turn.answer_tokens ?? "—"} ответ${finishReason ? ` · ${finishReason}` : ""}`;

  const clearLog = () => {
    log.querySelectorAll("[data-turn], [data-marker]").forEach((node) => node.remove());
    emptyNote.hidden = false;
  };

  // Лента строится из стека агента: отметки фактов и конспекта встают после
  // своего хода, точка ветвления — там, где ветка отделилась от родителя.
  const renderLog = (snapshot, branch, { restored = false } = {}) => {
    clearLog();
    const messages = snapshot.messages || [];
    const turns = snapshot.turns || [];
    const byTurn = new Map();
    const put = (turnIndex, text, kind) => {
      const list = byTurn.get(turnIndex) || [];
      list.push({ text, kind });
      byTurn.set(turnIndex, list);
    };
    (snapshot.fact_updates || []).forEach((u) => put(u.after_turn, describeFacts(u), "done"));
    (snapshot.compressions || []).forEach((r) => put(r.after_turn, describeCompression(r), "done"));
    if (branch && branch.parent) {
      put(branch.forked_at / 2, `ветка «${branch.title}» отделилась здесь от «${parentTitle(branch)}»: ${branch.forked_at} общих сообщений`, "branch");
    }
    (byTurn.get(0) || []).forEach((m) => addMarker(m.text, m.kind));
    let turnIndex = 0;
    messages.forEach((message) => {
      const turn = addTurn(message.role, message.content, { reveal: false, restored });
      if (message.role === "assistant") {
        turnIndex += 1;
        const stats = turns[turnIndex - 1];
        if (stats) turn.querySelector("[data-turn-meta]").textContent = formatTurnMeta(stats);
        (byTurn.get(turnIndex) || []).forEach((m) => addMarker(m.text, m.kind));
      }
    });
    markDropped(snapshot);
  };

  const parentTitle = (branch) => {
    const parent = current?.tree?.branches?.find((b) => b.id === branch.parent);
    return parent ? parent.title : branch.parent;
  };

  // Гасим то, что модель больше не увидит: окно отбросило, факты впитали.
  const markDropped = (snapshot) => {
    const strategy = snapshot.strategy || "full";
    log.dataset.strategy = strategy;
    const dropped = strategy === "window" || strategy === "facts" ? snapshot.context?.dropped || 0 : 0;
    log.querySelectorAll("[data-turn]").forEach((turn, index) => {
      turn.classList.toggle("is-dropped", index < dropped);
    });
  };

  /* ── Панели ─────────────────────────────────────────────────────────── */

  const setText = (selector, value) => {
    document.querySelectorAll(selector).forEach((target) => {
      target.textContent = value;
    });
  };

  const renderDebug = (snapshot, strategy) => {
    const set = (key, value) => setText(`[data-debug="${key}"]`, value);
    const sideRequests = (snapshot.compressions || []).length + (snapshot.fact_updates || []).length;
    set("strategy_title", strategy ? strategy.title : STRATEGY_TITLES[snapshot.strategy] || snapshot.strategy);
    set("strategy", snapshot.strategy);
    set("n", strategy && strategy.kind !== "full" ? strategy.n : "—");
    set("stack", snapshot.messages.length);
    set("context_messages", snapshot.context?.messages ?? snapshot.messages.length);
    set("context_estimated", snapshot.context?.estimated ?? 0);
    set("requests", snapshot.usage.requests);
    set("side_requests", sideRequests);
    set("total_tokens", snapshot.usage.total_tokens);
  };

  const renderFacts = (snapshot) => {
    if (!factsBox) return;
    factsBox.querySelectorAll("div").forEach((node) => node.remove());
    const facts = snapshot.facts || {};
    const keys = Object.keys(facts);
    const last = (snapshot.fact_updates || []).at(-1);
    const touched = new Set([...(last?.added || []), ...(last?.changed || [])]);
    if (factsEmpty) {
      factsEmpty.hidden = keys.length > 0;
      factsEmpty.textContent = snapshot.strategy === "facts"
        ? "Блок пуст — заполнится после первой реплики."
        : "Блок пуст. Включите Sticky Facts — он заполнится после первой реплики.";
    }
    keys.forEach((key) => {
      const row = document.createElement("div");
      if (touched.has(key)) row.classList.add("is-changed");
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = facts[key];
      row.append(dt, dd);
      factsBox.append(row);
    });
  };

  const renderBranches = (tree) => {
    if (!branchesBox) return;
    branchesBox.querySelectorAll("[data-branch]").forEach((node) => node.remove());
    (tree.branches || []).forEach((branch) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "day10-branch";
      button.dataset.branch = branch.id;
      button.classList.toggle("is-active", branch.id === tree.active);
      button.title = branch.parent ? `от «${branch.parent}», ${branch.forked_at} общих сообщений` : "основная ветка";
      button.innerHTML = `${escapeHtml(branch.title)}<small>${branch.messages ?? 0}</small>`;
      button.addEventListener("click", () => switchBranch(branch.id));
      branchesBox.append(button);
    });
  };

  const renderTree = (tree) => {
    if (!treeBox) return;
    treeBox.textContent = "";
    const branches = tree.branches || [];
    const checkpoints = tree.checkpoints || [];
    if (branches.length <= 1 && !checkpoints.length) {
      const note = document.createElement("p");
      note.className = "day10-tree__empty";
      note.textContent = "Одна ветка, checkpoint'ов нет. Поставьте checkpoint — от него можно вырастить ветку.";
      treeBox.append(note);
      return;
    }
    // Строка ветки в панели переключает её так же, как вкладка над лентой.
    branches.forEach((branch) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "day10-tree__branch";
      row.classList.toggle("is-active", branch.id === tree.active);
      row.title = branch.id === tree.active ? "Активная ветка" : "Переключиться на эту ветку";
      const parent = branch.parent ? branches.find((b) => b.id === branch.parent) : null;
      row.innerHTML =
        `<span>${escapeHtml(branch.title)} <small>· ${branch.messages ?? 0} сообщ.${parent ? ` · от «${escapeHtml(parent.title)}» после ${branch.forked_at}` : ""}</small></span>` +
        `<small>${escapeHtml(STRATEGY_TITLES[branch.strategy] || branch.strategy || "")}</small>`;
      row.addEventListener("click", () => switchBranch(branch.id));
      treeBox.append(row);
    });
    checkpoints.forEach((point) => {
      const branch = branches.find((b) => b.id === point.branch);
      const row = document.createElement("div");
      row.className = "day10-tree__point";
      row.innerHTML = `<span>${escapeHtml(point.title)} <small>· «${escapeHtml(branch ? branch.title : point.branch)}»</small></span>`;
      const fork = document.createElement("button");
      fork.type = "button";
      fork.className = "day10-tree__fork";
      fork.textContent = "[⑂] ветка";
      fork.title = "Новая ветка от этого checkpoint";
      fork.addEventListener("click", () => forkBranch(point.id));
      row.append(fork);
      treeBox.append(row);
    });
  };

  const renderRuns = (runs) => {
    if (!runsBody) return;
    runsBody.textContent = "";
    const list = runs || [];
    if (runsEmpty) runsEmpty.hidden = list.length > 0;
    const best = Math.max(0, ...list.map((r) => r.facts_found || 0));
    list.forEach((run) => {
      const row = document.createElement("tr");
      const factsClass = run.facts_found === run.facts_total ? "is-good" : run.facts_found < best ? "is-bad" : "";
      row.innerHTML =
        `<td>${run.id}</td>` +
        `<td class="day10-table__strategy">${escapeHtml(run.title || STRATEGY_TITLES[run.strategy] || run.strategy)}</td>` +
        `<td>${run.n ?? "—"}</td>` +
        `<td>${escapeHtml(run.branch || "")}</td>` +
        `<td>${run.turns}</td>` +
        `<td>${run.requests}</td>` +
        `<td>${run.side_requests}</td>` +
        `<td>${run.total_tokens}</td>` +
        `<td>${run.side_tokens}</td>` +
        `<td class="${factsClass}">${run.facts_found} / ${run.facts_total}</td>` +
        `<td class="day10-table__missing">${run.missing?.length ? escapeHtml(run.missing.join(", ")) : "—"}</td>`;
      runsBody.append(row);
    });
  };

  const renderStrategyForm = (strategy) => {
    if (!strategyForm || !strategy) return;
    const radio = strategyForm.querySelector(`input[name="kind"][value="${strategy.kind}"]`);
    if (radio && !strategyForm.contains(document.activeElement)) radio.checked = true;
    if (nField && document.activeElement !== nField) nField.value = strategy.n ?? "";
  };

  const renderState = (state, { rebuildLog = false, restored = false } = {}) => {
    if (!state) return;
    current = state;
    const branch = state.tree?.branches?.find((b) => b.id === state.tree.active);
    if (rebuildLog) renderLog(state.branch, branch, { restored });
    else markDropped(state.branch);
    renderDebug(state.branch, state.strategy);
    renderFacts(state.branch);
    renderBranches(state.tree);
    renderTree(state.tree);
    renderRuns(state.tree?.runs);
    renderStrategyForm(state.strategy);
  };

  const escapeHtml = (text) =>
    String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);

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

  const refreshState = async ({ rebuildLog = false } = {}) => {
    try {
      const response = await fetch(urls.state);
      const state = await readJson(response);
      if (response.ok) renderState(state, { rebuildLog });
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
    if (checkpointButton) checkpointButton.disabled = isLoading;
    if (recordButton) recordButton.disabled = isLoading;
    if (clearRunsButton) clearRunsButton.disabled = isLoading;
    scenarioButtons.forEach((button) => {
      button.disabled = isLoading || scenarioRunning;
    });
    strategyForm.querySelectorAll("button, input").forEach((el) => {
      el.disabled = isLoading;
    });
    document.querySelectorAll(".day10-branch, .day10-tree__branch, .day10-tree__fork").forEach((el) => {
      el.disabled = isLoading;
    });
  };

  // Один ход активной ветки. Возвращает true, если ответ дошёл.
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
        } else if (event.type === "facts_updating") {
          pendingMarker = addMarker(`обновляю факты (сейчас ${event.keys} ключей)…`, "pending");
          revealBlock(pendingMarker);
        } else if (event.type === "facts_updated") {
          setMarker(pendingMarker || addMarker("", "pending"), describeFacts(event.update));
          pendingMarker = null;
        } else if (event.type === "facts_failed") {
          setMarker(pendingMarker || addMarker("", "pending"), `факты не обновились: ${event.error} — блок прежний`, "failed");
          pendingMarker = null;
        } else if (event.type === "compressing") {
          pendingMarker = addMarker(`сжимаю историю: ${event.messages} сообщ.…`, "pending");
        } else if (event.type === "compressed") {
          setMarker(pendingMarker || addMarker("", "pending"), describeCompression(event.compression));
          pendingMarker = null;
        } else if (event.type === "compression_failed") {
          setMarker(pendingMarker || addMarker("", "pending"), `конспект не получился: ${event.error}`, "failed");
          pendingMarker = null;
        } else if (event.type === "done") {
          finished = true;
          setBody(body, event.reply.content);
          meta.textContent = event.turn ? formatTurnMeta(event.turn, event.reply.finish_reason) : "";
          if (current) current.branch = event.snapshot;
          renderDebug(event.snapshot, current?.strategy);
          renderFacts(event.snapshot);
          markDropped(event.snapshot);
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

  /* ── Сценарии ───────────────────────────────────────────────────────── */

  const loadScenario = async (url) => {
    const response = await fetch(url);
    const scenario = await readJson(response);
    if (!response.ok) throw new Error(scenario?.error || "Сценарий не загрузился");
    return scenario;
  };

  // Реплики по одной в активную ветку; стоп на первой ошибке.
  const runMessages = async (messages, label) => {
    for (let i = 0; i < messages.length; i += 1) {
      status.textContent = `${label}: реплика ${i + 1} из ${messages.length}…`;
      const ok = await ask(messages[i]);
      if (!ok) return false;
    }
    return true;
  };

  const recordRun = async (scenarioKey) => {
    const data = await postJson(urls.runs, { scenario: scenarioKey });
    renderState(data.state);
    return data.run;
  };

  const withScenarioLock = async (work) => {
    if (busy || scenarioRunning) return;
    scenarioRunning = true;
    scenarioButtons.forEach((button) => {
      button.disabled = true;
    });
    try {
      await work();
    } catch (error) {
      status.textContent = error.message || "Сценарий не выполнен";
    } finally {
      scenarioRunning = false;
      scenarioButtons.forEach((button) => {
        button.disabled = false;
      });
    }
  };

  scenarioButtons.forEach((button) => {
    if (button === branchScenarioButton) return;
    button.addEventListener("click", () =>
      withScenarioLock(async () => {
        const scenario = await loadScenario(button.dataset.scenarioUrl);
        const ok = await runMessages(scenario.messages, "Сценарий");
        if (!ok) {
          status.textContent = "Сценарий остановлен: ход не состоялся";
          return;
        }
        const run = await recordRun(scenario.key);
        status.textContent = `Сценарий пройден и записан: ${run.facts_found} из ${run.facts_total} фактов, ${run.total_tokens} токенов`;
      }),
    );
  });

  // Ветки: общий ствол, checkpoint, две ветки — каждая со своим продолжением.
  if (branchScenarioButton) {
    branchScenarioButton.addEventListener("click", () =>
      withScenarioLock(async () => {
        const scenario = await loadScenario(branchScenarioButton.dataset.scenarioUrl);
        status.textContent = "Новый диалог для веток…";
        renderState(await postJson(urls.reset, {}), { rebuildLog: true });
        if (!(await runMessages(scenario.trunk, "Общий ствол"))) {
          status.textContent = "Ствол не пройден: ход не состоялся";
          return;
        }
        status.textContent = "Ставлю checkpoint…";
        const point = await postJson(urls.checkpoint, { title: `после ${scenario.trunk.length} реплик` });
        renderState(point.state);
        for (const tail of scenario.branches) {
          status.textContent = `Ветка «${tail.title}»…`;
          const forked = await postJson(urls.fork, { checkpoint: point.checkpoint.id, title: tail.title });
          renderState(forked.state, { rebuildLog: true });
          if (!(await runMessages(tail.messages, `Ветка «${tail.title}»`))) {
            status.textContent = `Ветка «${tail.title}» остановлена: ход не состоялся`;
            return;
          }
        }
        status.textContent = "Две ветки от одного checkpoint готовы — переключайтесь вкладками над лентой";
      }),
    );
  }

  /* ── Стратегия, checkpoint, ветки, прогоны, сброс ───────────────────── */

  strategyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const kind = strategyForm.querySelector('input[name="kind"]:checked')?.value || "full";
    const n = Number(nField.value);
    setLoading(true);
    status.textContent = "Меняю стратегию…";
    try {
      const state = await postJson(urls.strategy, { kind, n });
      renderState(state);
      status.textContent = `Стратегия: ${state.strategy.title}${kind === "full" ? "" : `, N = ${state.strategy.n}`} — диалог прежний`;
    } catch (error) {
      status.textContent = error.message || "Не удалось сменить стратегию";
    } finally {
      setLoading(false);
    }
  });

  if (checkpointButton) {
    checkpointButton.addEventListener("click", async () => {
      if (busy) return;
      setLoading(true);
      status.textContent = "Ставлю checkpoint…";
      try {
        const data = await postJson(urls.checkpoint, {});
        renderState(data.state);
        status.textContent = `Checkpoint «${data.checkpoint.title}» — в панели справа кнопка «ветка»`;
      } catch (error) {
        status.textContent = error.message || "Не удалось поставить checkpoint";
      } finally {
        setLoading(false);
      }
    });
  }

  const forkBranch = async (checkpointId) => {
    if (busy) return;
    setLoading(true);
    status.textContent = "Создаю ветку…";
    try {
      const data = await postJson(urls.fork, { checkpoint: checkpointId });
      renderState(data.state, { rebuildLog: true });
      status.textContent = `Ветка «${data.branch.title}» создана и активна: ${data.branch.forked_at} общих сообщений`;
    } catch (error) {
      status.textContent = error.message || "Не удалось создать ветку";
    } finally {
      setLoading(false);
    }
  };

  const switchBranch = async (branchId) => {
    if (busy || current?.tree?.active === branchId) return;
    setLoading(true);
    status.textContent = "Переключаю ветку…";
    try {
      const state = await postJson(urls.switch, { branch: branchId });
      renderState(state, { rebuildLog: true });
      const branch = state.tree.branches.find((b) => b.id === branchId);
      status.textContent = `Активна ветка «${branch ? branch.title : branchId}»`;
    } catch (error) {
      status.textContent = error.message || "Не удалось переключить ветку";
    } finally {
      setLoading(false);
    }
  };

  if (recordButton) {
    recordButton.addEventListener("click", async () => {
      if (busy) return;
      setLoading(true);
      try {
        const run = await recordRun("spec");
        status.textContent = `Прогон записан: ${run.facts_found} из ${run.facts_total} фактов, ${run.total_tokens} токенов`;
      } catch (error) {
        status.textContent = error.message || "Не удалось записать прогон";
      } finally {
        setLoading(false);
      }
    });
  }

  if (clearRunsButton && urls.clearRuns) {
    clearRunsButton.addEventListener("click", async () => {
      if (busy) return;
      setLoading(true);
      try {
        renderState(await postJson(urls.clearRuns, undefined, "DELETE"));
        status.textContent = "Таблица прогонов очищена";
      } catch (error) {
        status.textContent = error.message || "Не удалось очистить таблицу";
      } finally {
        setLoading(false);
      }
    });
  }

  resetButton.addEventListener("click", async () => {
    if (busy) return;
    setLoading(true);
    status.textContent = "Создаю нового агента…";
    try {
      renderState(await postJson(urls.reset, {}), { rebuildLog: true });
      status.textContent = "Новый диалог: одна ветка, стратегия прежняя";
    } catch (error) {
      status.textContent = error.message || "Не удалось сбросить диалог";
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

  // Страница открылась — показать то, что у активной ветки уже есть.
  if (initialScript) {
    try {
      const initial = JSON.parse(initialScript.textContent);
      renderState(initial, { rebuildLog: true, restored: Boolean(initial.branch?.storage) });
    } catch {
      // Слепок не разобрался — лента останется пустой.
    }
  }
})();
