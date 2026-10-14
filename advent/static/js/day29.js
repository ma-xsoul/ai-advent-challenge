/* День 29: одна локальная модель, две настройки, одинаковые вопросы и источники. */
(() => {
  const form = document.querySelector("#day29-ask");
  if (!form) return;
  const settingsForm = document.querySelector("#day29-settings");
  const controls = [...settingsForm.querySelectorAll("[data-custom-select]")];
  controls.forEach((control) => window.CustomSelect.init(control));
  const question = document.querySelector("#day29-question");
  const askButton = form.querySelector("[type=submit]");
  const compareButton = document.querySelector("#day29-compare");
  const resetSettings = document.querySelector("[data-reset-settings]");
  const errorBox = document.querySelector("#day29-error");
  const askStatus = document.querySelector("#day29-ask-status");
  const compareStatus = document.querySelector("#day29-compare-status");
  const progress = document.querySelector("#day29-progress");
  const runBox = document.querySelector("[data-question-run]");
  const table = document.querySelector("[data-compare-table]");
  const summaryBox = document.querySelector("[data-summary]");
  const runsList = document.querySelector("[data-runs]");
  const initial = JSON.parse(document.querySelector("#day29-initial").textContent);
  const MODES = ["before", "after"];
  const TITLE = { before: "До", after: "После" };
  const CURRENT_FORMAT = "source_lines_schema-v1";
  const QUOTE_MARK = { exact: "✓", near: "✓", elsewhere: "≈", partial: "≈", stitched: "≈", missing: "✗" };
  let busy = false;
  let currentRows = [];
  let currentRunId = null;

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const number = (value, digits = 1) => typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("ru-RU", { maximumFractionDigits: digits }) : "—";
  const seconds = (ms) => typeof ms === "number" ? `${number(ms / 1000)} с` : "—";
  const percent = (value) => typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
  const coverage = (summary, key) => {
    const found = summary[`${key}_found`];
    const total = summary[`${key}_total`];
    return typeof found === "number" && typeof total === "number"
      ? `${found}/${total} · ${percent(summary[key])}` : percent(summary[key]);
  };
  const gigabytes = (bytes) => typeof bytes === "number" ? `${number(bytes / 1e9, 2)} ГБ` : "—";
  const markdown = (node, text) => {
    if (typeof window.renderMarkdown === "function") node.innerHTML = window.renderMarkdown(text || "");
    else node.textContent = text || "";
  };
  const showError = (message) => {
    errorBox.textContent = message;
    errorBox.hidden = !message;
  };
  const getSettings = () => ({
    temperature: Number(settingsForm.elements.temperature.value),
    max_tokens: Number(settingsForm.elements.max_tokens.value),
    num_ctx: Number(settingsForm.elements.num_ctx.value),
    prompt: settingsForm.elements.prompt.value,
  });
  const formatLabel = (version) => version === CURRENT_FORMAT
    ? "Цитаты по схеме · v1" : version ? `Формат: ${version}` : "Формат не записан";
  const runFormat = (saved) => {
    const version = typeof saved.format_version === "string" ? saved.format_version
      : saved.format_version?.after || saved.format_versions?.after;
    return version || saved.rows?.map((item) => item.modes?.after?.format_version)
      .find((item) => typeof item === "string") || null;
  };
  const settingsCaption = (settings, version = CURRENT_FORMAT) => {
    if (!settings) return "";
    const prompt = settings.prompt !== "precise" ? "Исходный JSON"
      : version === CURRENT_FORMAT ? "Точный + цитаты по схеме"
      : `Точный · ${formatLabel(version)}`;
    return `Температура ${number(settings.temperature)} · ответ ${settings.max_tokens} · контекст ${settings.num_ctx} · ${prompt}`;
  };
  const updateSettingsCaption = () => {
    settingsForm.elements.max_tokens.max = String(Math.min(8192, Number(settingsForm.elements.num_ctx.value) / 2));
    document.querySelector("[data-settings-caption]").textContent = settingsCaption(getSettings());
  };
  const setBusy = (value) => {
    busy = value;
    askButton.disabled = value || !initial.ready;
    compareButton.disabled = value || !initial.ready;
    question.disabled = value || !initial.ready;
    resetSettings.disabled = value;
    settingsForm.querySelectorAll('input:not([type="hidden"])').forEach((input) => { input.disabled = value; });
    controls.forEach((control) => window.CustomSelect.setDisabled(control, value));
    document.querySelectorAll("[data-question], [data-run-id]").forEach((button) => {
      button.disabled = value || (button.hasAttribute("data-question") && !initial.ready);
    });
  };

  async function post(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      headers: { Accept: "application/x-ndjson", "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || "Не удалось запустить сравнение.");
    }
    if (!response.body) throw new Error("Браузер не поддерживает потоковые результаты.");
    return response;
  }

  async function* streamEvents(body) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) if (line.trim()) yield JSON.parse(line);
        if (done) break;
      }
      if (buffer.trim()) yield JSON.parse(buffer);
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }

  function verdict(result) {
    if (result.error) return { passed: false, title: "Ошибка", lines: [result.error] };
    const check = result.check;
    const lines = [];
    const title = !check ? (result.status === "unknown" ? "«Не знаю»" : "Ответ получен")
      : check.negative ? (check.passed ? "✓ «Не знаю»" : "✗ Ответ вне базы")
      : `${check.passed ? "✓" : "✗"} Факты ${check.facts_found}/${check.facts_total}`;
    if (check && !check.negative) lines.push(`Цитаты ${check.quotes_found}/${check.quotes_total}`);
    if (check?.parsed === false || result.parsed === false) lines.push("Ошибка формата JSON");
    return { passed: check?.passed ?? true, title, lines };
  }

  function answerContent(container, result) {
    container.replaceChildren();
    if (result.format_version || result.settings?.prompt === "precise") {
      container.append(el("p", "day29-format", formatLabel(result.format_version)));
    }
    if (result.error) {
      container.append(el("p", "day29-error-text", result.error));
      return;
    }
    const text = el("div", "md-body");
    markdown(text, result.answer);
    container.append(text);
    if (result.quotes?.length) {
      const list = el("ol", "day29-quotes");
      result.quotes.forEach((quote) => {
        const item = el("li", `is-${quote.status}`);
        item.append(el("span", "day29-quote-mark", `${QUOTE_MARK[quote.status] || "?"} [${quote.n}]`));
        item.append(el("blockquote", "", quote.text));
        list.append(item);
      });
      container.append(list);
    }
    const sources = result.sources || [];
    const details = el("details", "day29-fold day29-sources");
    details.append(el("summary", "", `Источники · ${sources.length}`));
    const list = el("ul", "");
    sources.forEach((source) => list.append(el("li", "",
      typeof source === "string" ? source : `[${source.n}] ${source.source}${source.section ? ` · ${source.section}` : ""}`)));
    if (!sources.length) list.append(el("li", "day29-muted", "Нет источников в ответе"));
    details.append(list);
    container.append(details);
  }

  function resultMeta(result) {
    const values = [seconds(result.ms)];
    if (typeof result.generation_tps === "number") values.push(`${number(result.generation_tps)} ток/с`);
    if (typeof result.memory?.bytes === "number") values.push(`${gigabytes(result.memory.bytes)} модели`);
    if (typeof result.actual_ctx === "number") values.push(`контекст ${result.actual_ctx}${result.context_verified === false ? ` (запрошено ${result.settings?.num_ctx})` : ""}`);
    if (typeof result.load_s === "number") values.push(`загрузка ${number(result.load_s, 2)} с`);
    return values.join(" · ");
  }

  function showAnswer(result) {
    const box = runBox.querySelector(`[data-answer="${result.mode}"]`);
    if (!box) return;
    const check = verdict(result);
    box.classList.toggle("is-passed", check.passed);
    box.classList.toggle("is-failed", !check.passed);
    answerContent(box.querySelector("[data-answer-body]"), result);
    box.querySelector("[data-answer-meta]").textContent = resultMeta(result);
    box.querySelector("[data-answer-check]").textContent = [check.title, ...check.lines].join(" · ");
  }

  async function ask(text) {
    if (busy || !settingsForm.reportValidity()) return;
    showError("");
    const settings = getSettings();
    setBusy(true);
    runBox.hidden = false;
    runBox.querySelector("[data-asked-question]").textContent = text;
    runBox.querySelector("[data-expect]").hidden = true;
    runBox.querySelector("[data-hits]").replaceChildren();
    runBox.querySelector("[data-retrieved-meta]").textContent = "Поиск…";
    document.querySelector("[data-ask-settings]").textContent = settingsCaption(settings);
    MODES.forEach((mode) => {
      const box = runBox.querySelector(`[data-answer="${mode}"]`);
      box.classList.remove("is-passed", "is-failed");
      box.querySelector("[data-answer-meta]").textContent = "Ожидание ответа…";
      box.querySelector("[data-answer-body]").replaceChildren();
      box.querySelector("[data-answer-check]").textContent = "";
    });
    const received = new Set();
    askStatus.textContent = "Сравниваю два профиля…";
    try {
      const response = await post(form.dataset.askUrl, { question: text, settings });
      for await (const event of streamEvents(response.body)) {
        if (event.type === "probe") {
          runBox.querySelector("[data-expect]").hidden = false;
          runBox.querySelector("[data-expect-text]").textContent = event.expected || "";
        } else if (event.type === "retrieved") {
          const hits = event.hits || [];
          runBox.querySelector("[data-retrieved-meta]").textContent = `${hits.length} кусков · ${number(event.ms, 0)} мс`;
          runBox.querySelector("[data-hits]").replaceChildren(...hits.map((hit) =>
            el("li", "", `[${hit.rank}] ${hit.source}${hit.section ? ` · ${hit.section}` : ""}`)));
        } else if (event.type === "answer") {
          showAnswer(event);
          received.add(event.mode);
          askStatus.textContent = `Готово ${received.size} из 2 ответов`;
        } else if (event.type === "error") throw new Error(event.error);
        else if (event.type === "done") break;
      }
      if (received.size !== 2) throw new Error("Сравнение прервалось. Повторите вопрос.");
      askStatus.textContent = "Два профиля сравнены";
    } catch (error) {
      showError(error.message || "Не удалось сравнить ответы.");
      askStatus.textContent = "Сравнение прервалось";
      MODES.filter((mode) => !received.has(mode)).forEach((mode) => {
        runBox.querySelector(`[data-answer="${mode}"] [data-answer-meta]`).textContent = "Ответ не получен";
      });
    } finally { setBusy(false); }
  }

  const row = (index) => table.querySelector(`[data-probe-row="${index}"]`);
  const detail = (index) => table.querySelector(`[data-probe-detail="${index}"]`);
  function resetTable() {
    currentRows = [];
    currentRunId = null;
    summaryBox.hidden = true;
    table.querySelectorAll("[data-probe-row]").forEach((tr) => {
      tr.classList.remove("is-active", "is-done");
      tr.querySelector("[data-expand]").setAttribute("aria-expanded", "false");
      tr.querySelectorAll("[data-verdict]").forEach((cell) => {
        cell.classList.remove("is-passed", "is-failed");
        cell.textContent = "—";
      });
    });
    table.querySelectorAll("[data-probe-detail]").forEach((tr) => { tr.hidden = true; });
  }

  function fillRow(item) {
    const tr = row(item.index);
    if (!tr) return;
    currentRows = [...currentRows.filter((existing) => existing.index !== item.index), item];
    tr.classList.remove("is-active");
    tr.classList.add("is-done");
    MODES.forEach((mode) => {
      const result = item.modes?.[mode];
      if (!result) return;
      const cell = tr.querySelector(`[data-verdict="${mode}"]`);
      const check = verdict(result);
      cell.classList.toggle("is-passed", check.passed);
      cell.classList.toggle("is-failed", !check.passed);
      cell.replaceChildren(el("strong", "", check.title),
        ...check.lines.map((text) => el("small", "", text)), el("small", "", seconds(result.ms)));
      answerContent(detail(item.index).querySelector(`[data-detail="${mode}"]`), result);
    });
  }

  function fillSummary(summary) {
    summaryBox.hidden = false;
    const format = {
      passed: (s) => `${s.passed ?? 0} / ${s.on_topic ?? 10}`,
      facts: (s) => coverage(s, "facts"),
      quotes: (s) => coverage(s, "quotes"),
      refused: (s) => `${s.refused ?? 0} / ${s.off_topic ?? 3}`,
      broken_json: (s) => s.broken_json ?? "—",
      errors: (s) => s.errors ?? "—",
      avg_s: (s) => typeof s.avg_s === "number" ? `${number(s.avg_s)} с` : "—",
      generation_tps: (s) => typeof s.generation_tps === "number" ? `${number(s.generation_tps)} ток/с` : "—",
      memory_bytes: (s) => gigabytes(s.memory_bytes),
      load_s: (s) => typeof s.load_s === "number" ? `${number(s.load_s, 2)} с` : "—",
      warm_s: (s) => typeof s.warm_s === "number" ? `${number(s.warm_s)} с` : "—",
      actual_ctx: (_s, mode) => [...new Set(currentRows.map((item) => item.modes?.[mode]?.actual_ctx)
        .filter((value) => typeof value === "number"))].join(" · ") || "—",
    };
    Object.entries(format).forEach(([key, formatter]) => {
      MODES.forEach((mode) => {
        const cell = summaryBox.querySelector(`[data-summary-row="${key}"] [data-summary-cell="${mode}"]`);
        if (cell) cell.textContent = summary[mode] ? formatter(summary[mode], mode) : "—";
      });
    });
  }

  function renderRuns(runs) {
    runsList.replaceChildren();
    if (!runs?.length) {
      runsList.append(el("li", "day29-muted", "Прогонов пока нет"));
      return;
    }
    runs.forEach((saved) => {
      const item = el("li");
      const button = el("button", "day29-run-button");
      button.type = "button";
      button.dataset.runId = saved.id;
      button.disabled = busy;
      button.classList.toggle("is-current", saved.id === currentRunId);
      button.append(el("strong", "", new Date(saved.started_at).toLocaleString("ru-RU")));
      const before = saved.summary?.before;
      const after = saved.summary?.after;
      if (before && after) button.append(el("span", "", `Ответы ${before.passed}/10 → ${after.passed}/10 · цитаты ${coverage(before, "quotes")} → ${coverage(after, "quotes")}`));
      if (saved.settings) button.append(el("small", "", settingsCaption(saved.settings, runFormat(saved))));
      item.append(button);
      runsList.append(item);
    });
  }

  function displayRun(saved) {
    resetTable();
    (saved.rows || []).forEach(fillRow);
    if (saved.summary) fillSummary(saved.summary);
    currentRunId = saved.id;
    progress.value = saved.rows?.length || 0;
    compareStatus.textContent = `Прогон от ${new Date(saved.started_at).toLocaleString("ru-RU")}`;
    document.querySelector("[data-compare-caption]").textContent = settingsCaption(saved.settings, runFormat(saved));
    runsList.querySelectorAll("[data-run-id]").forEach((button) => {
      button.classList.toggle("is-current", button.dataset.runId === saved.id);
    });
  }

  async function openRun(id) {
    if (busy) return;
    setBusy(true);
    showError("");
    try {
      const response = await fetch(`${runsList.dataset.runsUrl}/${encodeURIComponent(id)}`);
      const saved = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(saved.error || "Не удалось открыть прогон.");
      displayRun(saved);
    } catch (error) { showError(error.message); }
    finally { setBusy(false); }
  }

  compareButton.addEventListener("click", async () => {
    if (busy || !settingsForm.reportValidity()) return;
    const settings = getSettings();
    showError("");
    resetTable();
    setBusy(true);
    progress.value = 0;
    const started = Date.now();
    let completed = false;
    let count = 0;
    compareStatus.textContent = "Начинаю сравнение…";
    document.querySelector("[data-compare-caption]").textContent = settingsCaption(settings);
    try {
      const response = await post(compareButton.dataset.compareUrl, { settings });
      for await (const event of streamEvents(response.body)) {
        if (event.type === "compare_start") progress.max = event.total || 13;
        else if (event.type === "compare_question") {
          table.querySelectorAll(".is-active").forEach((tr) => tr.classList.remove("is-active"));
          row(event.index)?.classList.add("is-active");
          compareStatus.textContent = `Вопрос ${event.index} из ${progress.max} · ${seconds(Date.now() - started)}`;
        } else if (event.type === "compare_row") {
          fillRow(event.row || event);
          count += 1;
          progress.value = count;
        } else if (event.type === "compare_summary") {
          fillSummary(event.summary);
          currentRunId = event.save_error ? null : event.id || event.runs?.[0]?.id || null;
          if (event.runs) renderRuns(event.runs);
          if (event.save_error) showError(event.save_error);
          compareStatus.textContent = `Сравнено ${count} вопросов · ${seconds(Date.now() - started)} · ${event.save_error ? "результат не сохранён" : "прогон сохранён"}`;
          completed = true;
          break;
        } else if (event.type === "error") throw new Error(event.error);
      }
      if (!completed) throw new Error("Прогон прервался до итогов. Полученные ответы остались в таблице.");
    } catch (error) {
      showError(error.message || "Не удалось выполнить прогон.");
      compareStatus.textContent = `Прогон прервался · готово ${count} из ${progress.max}`;
    } finally {
      table.querySelectorAll(".is-active").forEach((tr) => tr.classList.remove("is-active"));
      setBusy(false);
    }
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (question.value.trim()) ask(question.value.trim());
  });
  document.querySelector("[data-picks]").addEventListener("click", (event) => {
    const button = event.target.closest("[data-question]");
    if (!button || busy) return;
    question.value = button.dataset.question;
    ask(question.value);
  });
  table.addEventListener("click", (event) => {
    const button = event.target.closest("[data-expand]");
    const tr = button?.closest("[data-probe-row]");
    if (!tr?.classList.contains("is-done")) return;
    const target = detail(tr.dataset.probeRow);
    target.hidden = !target.hidden;
    button.setAttribute("aria-expanded", String(!target.hidden));
  });
  runsList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-run-id]");
    if (button && !busy) openRun(button.dataset.runId);
  });
  settingsForm.addEventListener("input", updateSettingsCaption);
  settingsForm.addEventListener("change", updateSettingsCaption);
  settingsForm.addEventListener("submit", (event) => event.preventDefault());
  resetSettings.addEventListener("click", () => {
    settingsForm.reset();
    controls.forEach((control) => window.CustomSelect.reset(control));
    updateSettingsCaption();
  });

  updateSettingsCaption();
  renderRuns(initial.runs || []);
  setBusy(false);
  if (initial.latest) displayRun(initial.latest);
  else if (initial.runs?.length) openRun(initial.runs[0].id);
})();
