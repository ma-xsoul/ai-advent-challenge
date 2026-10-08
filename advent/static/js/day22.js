/*
 * День 22: первый RAG-запрос.
 * Вопрос: retrieved (куски + что ушло модели) → delta обоих режимов
 * вперемешку → answer × 2. Если вопрос контрольный — сначала приходит
 * probe с ожиданием, а под каждым ответом появляется сверка фактов.
 * Прогон 10 вопросов: compare_question → retrieved → answer × 2 по
 * каждому вопросу, строка таблицы заполняется по мере готовности.
 * Тексты кусков и промптов выводятся только через textContent; ответы
 * модели — через общий renderMarkdown, он экранирует HTML.
 */
(() => {
  const form = document.querySelector("#day22-ask");
  if (!form) return;

  const input = document.querySelector("#day22-question");
  const askButton = form.querySelector("button[type=submit]");
  const errorBox = document.querySelector("#day22-error");
  const run = document.querySelector("[data-run]");
  const picks = document.querySelector("[data-picks]");
  const compareButton = document.querySelector("#day22-compare");
  const compareStatus = document.querySelector("#day22-compare-status");
  const table = document.querySelector("[data-compare-table]");
  const MODES = ["plain", "rag"];

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

  const usage = (event) => {
    const parts = [seconds(event.ms)];
    if (event.prompt_tokens != null) parts.push(`${number(event.prompt_tokens)} → ${number(event.completion_tokens || 0)} ток.`);
    return parts.join(" · ");
  };

  const verdict = (grade) => `${grade.passed ? "✓" : "✗"} ${grade.found}/${grade.total} фактов`;

  /* ── Один вопрос ───────────────────────────────────────────────────── */

  const answerBox = (mode) => run.querySelector(`[data-answer="${mode}"]`);

  function resetRun() {
    run.hidden = false;
    run.querySelector("[data-expect]").hidden = true;
    run.querySelector("[data-hits]").replaceChildren();
    run.querySelector("[data-retrieved-meta]").textContent = "ищем…";
    run.querySelector("[data-prompt-meta]").textContent = "—";
    for (const mode of MODES) {
      const box = answerBox(mode);
      box.classList.remove("is-passed", "is-failed", "is-error");
      box.querySelector("[data-answer-meta]").textContent = "ждёт";
      box.querySelector("[data-answer-body]").replaceChildren(el("p", "day22-muted", "…"));
      box.querySelector("[data-answer-cited]").hidden = true;
      box.querySelector("[data-answer-checks]").hidden = true;
      run.querySelector(`[data-prompt="${mode}"]`).textContent = "";
    }
  }

  function showProbe(event) {
    const box = run.querySelector("[data-expect]");
    box.hidden = false;
    box.querySelector("[data-expect-text]").textContent = event.expected;
    box.querySelector("[data-expect-sources]").textContent = event.sources.join(", ");
  }

  function hitItem(hit) {
    const item = el("li", "day22-hit");
    const head = el("p", "day22-hit__head");
    head.append(el("span", "day22-num", `[${hit.rank}]`), el("code", "", hit.source), el("small", "", `${hit.score.toFixed(3)}`));
    const section = el("p", "day22-hit__section", `${hit.section} · строки ${hit.start_line}–${hit.end_line}`);
    const fold = el("details", "day22-fold");
    fold.append(el("summary", "", "текст куска"), el("pre", "day22-prompt", hit.text));
    item.append(head, section, fold);
    return item;
  }

  function showRetrieved(event) {
    const list = run.querySelector("[data-hits]");
    list.replaceChildren(...event.hits.map(hitItem));
    const files = new Set(event.hits.map((hit) => hit.source)).size;
    let meta = `top-${event.k} · ${files} ${files === 1 ? "файл" : files < 5 ? "файла" : "файлов"}`;
    if (event.found_expected === true) meta += " · нужный файл есть";
    if (event.found_expected === false) meta += " · нужного файла нет";
    run.querySelector("[data-retrieved-meta]").textContent = meta;
    for (const mode of MODES) run.querySelector(`[data-prompt="${mode}"]`).textContent = event.prompt[mode];
    run.querySelector("[data-prompt-meta]").textContent =
      `без RAG ${number(event.prompt.plain.length)} симв. · с RAG ${number(event.prompt.rag.length)} симв.`;
    for (const mode of MODES) answerBox(mode).querySelector("[data-answer-meta]").textContent = "пишет…";
  }

  function fillCited(box, cited) {
    box.replaceChildren(el("span", "day22-label", cited.length ? "сослался на" : "ссылок на куски нет"));
    for (const item of cited) {
      const tag = el("span", "day22-cite");
      tag.append(el("b", "", `[${item.n}]`), document.createTextNode(` ${item.source}`));
      tag.title = item.section;
      box.append(tag);
    }
    box.hidden = false;
  }

  function fillChecks(list, grade) {
    list.replaceChildren();
    for (const fact of grade.facts) list.append(el("li", fact.found ? "is-found" : "is-missing", `${fact.found ? "✓" : "✗"} ${fact.label}`));
    const total = el("li", "day22-checks__total", `${verdict(grade)} · нужно ${grade.need}`);
    if (grade.sources_ok !== null) total.textContent += ` · источник ${grade.sources_ok ? "✓" : "✗"}`;
    list.append(total);
    list.hidden = false;
  }

  function showAnswer(event, drafts) {
    const box = answerBox(event.mode);
    const body = box.querySelector("[data-answer-body]");
    if (event.error) {
      box.classList.add("is-error");
      box.querySelector("[data-answer-meta]").textContent = "ошибка";
      body.replaceChildren(el("p", "day22-error", event.error));
      return;
    }
    drafts[event.mode] = event.answer;
    markdown(body, event.answer);
    box.querySelector("[data-answer-meta]").textContent = usage(event);
    if (event.cited) fillCited(box.querySelector("[data-answer-cited]"), event.cited);
    if (event.grade) {
      fillChecks(box.querySelector("[data-answer-checks]"), event.grade);
      box.classList.add(event.grade.passed ? "is-passed" : "is-failed");
    }
  }

  let busy = false;
  const setBusy = (value) => {
    busy = value;
    askButton.disabled = value;
    compareButton.disabled = value;
    picks.querySelectorAll("button").forEach((button) => (button.disabled = value));
  };

  async function ask(question) {
    if (busy) return;
    showError("");
    setBusy(true);
    resetRun();
    const drafts = { plain: "", rag: "" };
    let pending = null;
    const flush = () => {
      pending = null;
      for (const mode of MODES) if (drafts[mode]) markdown(answerBox(mode).querySelector("[data-answer-body]"), drafts[mode]);
    };
    try {
      const response = await post(form.dataset.askUrl, { question });
      for await (const event of streamEvents(response.body)) {
        if (event.type === "probe") showProbe(event);
        else if (event.type === "retrieved") showRetrieved(event);
        else if (event.type === "delta") {
          drafts[event.mode] += event.text;
          // Перерисовка markdown на каждый кусочек дорогая — не чаще кадра.
          pending ??= requestAnimationFrame(flush);
        } else if (event.type === "answer") {
          if (pending) {
            cancelAnimationFrame(pending);
            flush();
          }
          showAnswer(event, drafts);
        } else if (event.type === "error") showError(event.error);
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

  /* ── Десять контрольных вопросов ───────────────────────────────────── */

  const row = (index) => table.querySelector(`[data-probe-row="${index}"]`);
  const detail = (index) => table.querySelector(`[data-probe-detail="${index}"]`);

  function resetTable() {
    table.querySelectorAll("[data-probe-row]").forEach((tr) => {
      tr.classList.remove("is-active", "is-done");
      tr.querySelectorAll("[data-verdict]").forEach((td) => {
        td.className = "day22-verdict";
        td.textContent = "—";
      });
    });
    table.querySelectorAll("[data-probe-detail]").forEach((tr) => (tr.hidden = true));
    table.querySelector("[data-compare-summary]").hidden = true;
  }

  function fillVerdict(event) {
    const cell = row(event.index).querySelector(`[data-verdict="${event.mode}"]`);
    const answer = detail(event.index).querySelector(`[data-detail-answer="${event.mode}"]`);
    if (event.error) {
      cell.className = "day22-verdict is-error";
      cell.textContent = "ошибка";
      answer.textContent = event.error;
      return;
    }
    cell.className = `day22-verdict ${event.grade.passed ? "is-passed" : "is-failed"}`;
    cell.replaceChildren(el("strong", "", verdict(event.grade)));
    if (event.grade.sources_ok !== null) cell.append(el("small", "", `источник ${event.grade.sources_ok ? "✓" : "✗"}`));
    cell.append(el("small", "", seconds(event.ms)));
    markdown(answer, event.answer);
  }

  function fillSummary(event) {
    const foot = table.querySelector("[data-compare-summary]");
    const values = {
      passed: (s) => `${s.passed} из ${event.total}`,
      facts: (s) => `${Math.round(s.facts * 100)}%`,
      sources: (s) => (s.sources === null ? "—" : `${s.sources} из ${event.total}`),
      avg_ms: (s) => seconds(s.avg_ms),
      tokens: (s) => number(s.tokens),
    };
    for (const [key, format] of Object.entries(values)) {
      for (const mode of MODES) foot.querySelector(`[data-summary-row="${key}"] [data-summary-cell="${mode}"]`).textContent = format(event.summary[mode]);
    }
    foot.hidden = false;
  }

  compareButton.addEventListener("click", async () => {
    if (busy) return;
    setBusy(true);
    resetTable();
    compareStatus.textContent = "";
    try {
      const response = await post(compareButton.dataset.compareUrl);
      for await (const event of streamEvents(response.body)) {
        if (event.type === "compare_question") {
          table.querySelectorAll(".is-active").forEach((tr) => tr.classList.remove("is-active"));
          row(event.index).classList.add("is-active");
          row(event.index).scrollIntoView({ block: "nearest", behavior: "smooth" });
          compareStatus.textContent = `Вопрос ${event.index} из 10…`;
        } else if (event.type === "answer") {
          fillVerdict(event);
          const done = MODES.every((mode) => row(event.index).querySelector(`[data-verdict="${mode}"]`).textContent !== "—");
          if (done) row(event.index).classList.add("is-done");
        } else if (event.type === "compare_summary") {
          table.querySelectorAll(".is-active").forEach((tr) => tr.classList.remove("is-active"));
          fillSummary(event);
          compareStatus.textContent = `Готово: без RAG ${event.summary.plain.passed}/10, с RAG ${event.summary.rag.passed}/10. Нажмите на строку — откроются оба ответа.`;
        } else if (event.type === "error") {
          compareStatus.textContent = event.error;
        }
      }
    } catch (error) {
      compareStatus.textContent = error.message;
    } finally {
      setBusy(false);
    }
  });

  table.addEventListener("click", (event) => {
    const tr = event.target.closest("[data-probe-row]");
    if (!tr || !tr.classList.contains("is-done")) return;
    const target = detail(tr.dataset.probeRow);
    target.hidden = !target.hidden;
  });
})();
