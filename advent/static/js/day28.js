/*
 * День 28: локальная LLM + RAG.
 * Вопрос: probe? → retrieved → answer × модели (по мере готовности).
 * Прогон: compare_question → compare_row × 13 → compare_summary (+ список
 * прогонов). Сохранённый прогон открывается в той же таблице — последний
 * показывается сразу при загрузке страницы.
 * Тексты кусков и цитат — через textContent; ответ модели — через общий
 * renderMarkdown, он экранирует HTML.
 */
(() => {
  const form = document.querySelector("#day28-ask");
  if (!form) return;

  const input = document.querySelector("#day28-question");
  const askButton = form.querySelector("button[type=submit]");
  const modesBox = document.querySelector("[data-modes]");
  const errorBox = document.querySelector("#day28-error");
  const run = document.querySelector("[data-run]");
  const picks = document.querySelector("[data-picks]");
  const compareButton = document.querySelector("#day28-compare");
  const compareStatus = document.querySelector("#day28-compare-status");
  const table = document.querySelector("[data-compare-table]");
  const runsList = document.querySelector("[data-runs]");
  const initial = JSON.parse(document.querySelector("#day28-initial").textContent);
  const MODES = ["local", "cloud"];
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
  const sec = (ms) => `${(ms / 1000).toFixed(1).replace(".", ",")} с`;
  const num = (value) => String(value).replace(".", ",");

  const showError = (text) => {
    errorBox.textContent = text;
    errorBox.hidden = !text;
  };
  const modes = () => [...modesBox.querySelectorAll("input:checked")].map((box) => box.value);

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
    const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return response;
  }

  // Короткая сводка проверки: засчитано, факты, цитаты, формат.
  function verdictParts(result) {
    if (result.error) return { ok: false, title: "ошибка", lines: [result.error] };
    const check = result.check;
    const lines = [];
    if (!check) return { ok: true, title: result.status === "unknown" ? "«не знаю»" : "ответ", lines: [`${sec(result.ms)}`] };
    let title;
    if (check.negative) title = check.passed ? "✓ «не знаю»" : "✗ ответил";
    else title = `${check.passed ? "✓" : "✗"} ${check.facts_found}/${check.facts_total} фактов`;
    if (!check.negative) lines.push(`цитаты ${check.quotes_found}/${check.quotes_total}`);
    if (!check.parsed) lines.push("сломан JSON");
    lines.push(`${sec(result.ms)}${result.tps ? ` · ${num(result.tps)} ток/с` : ""}`);
    return { ok: check.passed, title, lines };
  }

  /* ── Один вопрос ───────────────────────────────────────────────────── */

  const answerBox = (mode) => run.querySelector(`[data-answer="${mode}"]`);

  function resetRun(selected) {
    run.hidden = false;
    run.querySelector("[data-expect]").hidden = true;
    run.querySelector("[data-hits]").replaceChildren();
    run.querySelector("[data-retrieved-meta]").textContent = "ищем…";
    for (const mode of MODES) {
      const box = answerBox(mode);
      box.hidden = !selected.includes(mode);
      box.classList.remove("is-passed", "is-failed");
      box.querySelector("[data-answer-meta]").textContent = "думает…";
      box.querySelector("[data-answer-body]").replaceChildren(el("p", "day28-muted", "…"));
      box.querySelector("[data-answer-quotes]").hidden = true;
      box.querySelector("[data-answer-checks]").hidden = true;
    }
  }

  function showAnswer(result) {
    const box = answerBox(result.mode);
    const body = box.querySelector("[data-answer-body]");
    if (result.error) {
      box.classList.add("is-failed");
      box.querySelector("[data-answer-meta]").textContent = `ошибка · ${sec(result.ms)}`;
      body.replaceChildren(el("p", "day28-error", result.error));
      return;
    }
    markdown(body, result.answer);
    const meta = [sec(result.ms)];
    if (result.tps) meta.push(`${num(result.tps)} ток/с`);
    meta.push(`${result.prompt_tokens} → ${result.completion_tokens} ток.`);
    box.querySelector("[data-answer-meta]").textContent = meta.join(" · ");
    const list = box.querySelector("[data-answer-quotes]");
    list.replaceChildren(...result.quotes.map((quote) => {
      const item = el("li", `is-${quote.status}`);
      item.append(el("span", "day28-quote__mark", `${QUOTE_MARKS[quote.status]} [${quote.n}]`), el("blockquote", "", quote.text));
      return item;
    }));
    list.hidden = !result.quotes.length;
    const checks = box.querySelector("[data-answer-checks]");
    const verdict = verdictParts(result);
    checks.textContent = [verdict.title, ...verdict.lines.slice(0, -1)].join(" · ");
    checks.hidden = !result.check;
    if (result.check) box.classList.add(result.check.passed ? "is-passed" : "is-failed");
  }

  let busy = false;
  const setBusy = (value) => {
    busy = value;
    askButton.disabled = value;
    compareButton.disabled = value;
    picks.querySelectorAll("button").forEach((button) => (button.disabled = value));
    modesBox.querySelectorAll("input").forEach((box) => (box.disabled = value || box.dataset.locked === "1"));
  };
  modesBox.querySelectorAll("input:disabled").forEach((box) => (box.dataset.locked = "1"));

  async function ask(question) {
    if (busy) return;
    const selected = modes();
    if (!selected.length) return showError("Выберите хотя бы одну модель");
    showError("");
    setBusy(true);
    resetRun(selected);
    try {
      const response = await post(form.dataset.askUrl, { question, modes: selected });
      for await (const event of streamEvents(response.body)) {
        if (event.type === "probe") {
          run.querySelector("[data-expect]").hidden = false;
          run.querySelector("[data-expect-text]").textContent = event.expected;
        } else if (event.type === "retrieved") {
          run.querySelector("[data-retrieved-meta]").textContent = `top-${event.hits.length} · ${event.ms} мс на маке`;
          run.querySelector("[data-hits]").replaceChildren(...event.hits.map((hit) => {
            const item = el("li");
            item.append(el("code", "", `[${hit.rank}] ${hit.source}`), el("small", "", ` ${hit.section} · ${hit.score.toFixed(3)}`));
            return item;
          }));
        } else if (event.type === "answer") showAnswer(event);
        else if (event.type === "error") showError(event.error);
      }
    } catch (error) {
      showError(error.message);
    } finally {
      setBusy(false);
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (question) ask(question);
  });
  picks.addEventListener("click", (event) => {
    const button = event.target.closest("[data-question]");
    if (!button) return;
    input.value = button.dataset.question;
    ask(input.value);
  });

  /* ── Прогон ────────────────────────────────────────────────────────── */

  const row = (index) => table.querySelector(`[data-probe-row="${index}"]`);
  const detail = (index) => table.querySelector(`[data-probe-detail="${index}"]`);

  function resetTable() {
    table.querySelectorAll("[data-probe-row]").forEach((tr) => {
      tr.classList.remove("is-done", "is-active");
      tr.querySelectorAll("[data-verdict]").forEach((td) => {
        td.className = "day28-verdict";
        td.textContent = "—";
      });
    });
    table.querySelectorAll("[data-probe-detail]").forEach((tr) => (tr.hidden = true));
    table.querySelector("[data-summary]").hidden = true;
  }

  function fillRow(item) {
    const tr = row(item.index);
    tr.classList.remove("is-active");
    tr.classList.add("is-done");
    for (const mode of MODES) {
      const cell = tr.querySelector(`[data-verdict="${mode}"]`);
      const box = detail(item.index).querySelector(`[data-detail="${mode}"]`);
      const result = item.modes[mode];
      if (!result) {
        cell.textContent = "—";
        box.replaceChildren();
        continue;
      }
      const verdict = verdictParts(result);
      cell.className = `day28-verdict ${verdict.ok ? "is-passed" : "is-failed"}`;
      cell.replaceChildren(el("strong", "", verdict.title), ...verdict.lines.map((line) => el("small", "", line)));
      box.replaceChildren(el("p", "day28-label", mode === "local" ? "на маке" : "в облаке"));
      const answer = el("div", "markdown-body");
      markdown(answer, result.error || result.answer || "");
      box.append(answer);
    }
  }

  function fillSummary(summary) {
    const foot = table.querySelector("[data-summary]");
    const values = {
      passed: (s) => `${s.passed} из ${s.on_topic}`,
      facts: (s) => `${Math.round(s.facts * 100)}%`,
      quotes: (s) => `${Math.round(s.quotes * 100)}%`,
      refused: (s) => `${s.refused} из ${s.off_topic}`,
      avg_s: (s) => (s.avg_s === null ? "—" : `${num(s.avg_s)} с`),
      range_s: (s) => (s.min_s === null ? "—" : `${num(s.min_s)} · ${num(s.max_s)} с`),
      tps: (s) => (s.tps ? `${num(s.tps)} ток/с` : "—"),
      broken_json: (s) => String(s.broken_json),
      false_unknown: (s) => String(s.false_unknown),
      errors: (s) => String(s.errors),
    };
    for (const [key, format] of Object.entries(values)) {
      for (const mode of MODES) {
        const cell = foot.querySelector(`[data-summary-row="${key}"] [data-summary-cell="${mode}"]`);
        cell.textContent = summary[mode] ? format(summary[mode]) : "—";
      }
    }
    foot.hidden = false;
  }

  function renderRuns(runs) {
    runsList.replaceChildren();
    if (!runs.length) {
      runsList.append(el("li", "day28-muted", "Прогонов пока нет — нажмите «[▶] Прогнать»."));
      return;
    }
    for (const item of runs) {
      const li = el("li");
      const button = el("button", "day28-run-item");
      button.type = "button";
      button.dataset.run = item.id;
      const when = new Date(item.started_at);
      button.append(el("b", "", Number.isNaN(when.getTime()) ? item.started_at : when.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })));
      for (const backend of item.backends) {
        const s = item.summary[backend.key];
        button.append(el("span", "", `${backend.model}: ${s.passed}/${s.on_topic} · «не знаю» ${s.refused}/${s.off_topic} · ${s.avg_s === null ? "—" : num(s.avg_s) + " с"} · JSON ✗ ${s.broken_json}`));
      }
      li.append(button);
      runsList.append(li);
    }
  }

  async function openRun(id) {
    const response = await fetch(`${runsList.dataset.runUrl}/${encodeURIComponent(id)}`);
    if (!response.ok) return;
    const saved = await response.json();
    resetTable();
    saved.rows.forEach(fillRow);
    fillSummary(saved.summary);
    runsList.querySelectorAll(".is-current").forEach((node) => node.classList.remove("is-current"));
    runsList.querySelector(`[data-run="${id}"]`)?.classList.add("is-current");
    compareStatus.textContent = `Показан прогон от ${new Date(saved.started_at).toLocaleString("ru-RU")}.`;
  }

  compareButton.addEventListener("click", async () => {
    if (busy) return;
    const selected = modes();
    if (!selected.length) return showError("Выберите хотя бы одну модель");
    setBusy(true);
    resetTable();
    const started = Date.now();
    let done = 0;
    compareStatus.textContent = "Вопрос 1 из 13…";
    try {
      const response = await post(compareButton.dataset.compareUrl, { modes: selected });
      for await (const event of streamEvents(response.body)) {
        if (event.type === "compare_question") {
          row(event.index).classList.add("is-active");
          compareStatus.textContent = `Вопрос ${event.index} из 13… прошло ${sec(Date.now() - started)}`;
        } else if (event.type === "compare_row") {
          fillRow(event);
          done += 1;
        } else if (event.type === "compare_summary") {
          fillSummary(event.summary);
          renderRuns(event.runs || []);
          runsList.firstElementChild?.querySelector("button")?.classList.add("is-current");
          compareStatus.textContent = `Готово за ${sec(Date.now() - started)}. Нажмите на строку — откроются ответы.`;
        } else if (event.type === "error") compareStatus.textContent = event.error;
      }
    } catch (error) {
      compareStatus.textContent = error.message;
    } finally {
      table.querySelectorAll(".is-active").forEach((tr) => tr.classList.remove("is-active"));
      setBusy(false);
    }
  });

  table.addEventListener("click", (event) => {
    const tr = event.target.closest("[data-probe-row]");
    if (!tr || !tr.classList.contains("is-done")) return;
    const target = detail(tr.dataset.probeRow);
    target.hidden = !target.hidden;
  });

  runsList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-run]");
    if (button && !busy) openRun(button.dataset.run);
  });

  renderRuns(initial.runs);
  if (initial.runs.length) openRun(initial.runs[0].id);
})();
