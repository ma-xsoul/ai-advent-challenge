/*
 * День 23: реранкинг и фильтрация.
 * Вопрос: probe? → funnel × 2 (порог + реранк, + rewrite) → delta трёх
 * режимов вперемешку → answer × 3. Воронка — таблица кандидатов: похожесть
 * с отметкой порога, оценка реранкера и где кандидат отсеян.
 * Прогон 13 вопросов идёт по три сразу, поэтому строки таблицы
 * заполняются в порядке готовности, а не по номерам.
 * Тексты кусков и запросов — только через textContent; ответы модели —
 * через общий renderMarkdown, он экранирует HTML.
 */
(() => {
  const form = document.querySelector("#day23-ask");
  if (!form) return;

  const input = document.querySelector("#day23-question");
  const askButton = form.querySelector("button[type=submit]");
  const settingsForm = document.querySelector("[data-settings]");
  const errorBox = document.querySelector("#day23-error");
  const run = document.querySelector("[data-run]");
  const picks = document.querySelector("[data-picks]");
  const compareButton = document.querySelector("#day23-compare");
  const compareStatus = document.querySelector("#day23-compare-status");
  const table = document.querySelector("[data-compare-table]");
  const MODES = ["base", "filter", "rewrite"];
  const STAGES = {
    context: "в контексте",
    top_k: "за top-K",
    rerank: "реранк ниже порога",
    threshold: "ниже порога",
  };
  const VIA = { question: "вопрос", rewrite: "rewrite", both: "оба" };

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
  const pieces = (count) => {
    const mod10 = count % 10;
    const mod100 = count % 100;
    if (mod10 === 1 && mod100 !== 11) return `${count} кусок`;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${count} куска`;
    return `${count} кусков`;
  };

  const showError = (text) => {
    errorBox.textContent = text;
    errorBox.hidden = !text;
  };

  const settings = () => {
    const data = Object.fromEntries(new FormData(settingsForm).entries());
    return {
      threshold: Number(data.threshold),
      candidates: Number(data.candidates),
      top_k: Number(data.top_k),
      rerank_min: Number(data.rerank_min),
    };
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
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return response;
  }

  const verdict = (grade) => `${grade.passed ? "✓" : "✗"} ${grade.found}/${grade.total} фактов`;

  /* ── Один вопрос ───────────────────────────────────────────────────── */

  const answerBox = (mode) => run.querySelector(`[data-answer="${mode}"]`);
  const funnelBox = (mode) => run.querySelector(`[data-funnel="${mode}"]`);

  function resetRun() {
    run.hidden = false;
    run.querySelector("[data-expect]").hidden = true;
    for (const mode of ["filter", "rewrite"]) {
      const box = funnelBox(mode);
      box.querySelector("[data-funnel-meta]").textContent = "ищем…";
      box.querySelector("[data-funnel-rows]").replaceChildren();
      box.querySelector("[data-funnel-query]").hidden = true;
    }
    for (const mode of MODES) {
      const box = answerBox(mode);
      box.classList.remove("is-passed", "is-failed", "is-error");
      box.querySelector("[data-answer-meta]").textContent = "ждёт";
      box.querySelector("[data-answer-body]").replaceChildren(el("p", "day23-muted", "…"));
      box.querySelector("[data-answer-cited]").hidden = true;
      box.querySelector("[data-answer-checks]").hidden = true;
    }
  }

  function showProbe(event) {
    const box = run.querySelector("[data-expect]");
    box.hidden = false;
    box.querySelector("[data-expect-text]").textContent = event.expected;
    box.querySelector("[data-expect-sources]").textContent = event.sources.length
      ? `источники: ${event.sources.join(", ")}`
      : "вопрос вне базы — нужного файла нет";
  }

  function candidateRow(row, number, threshold) {
    const tr = el("tr", `is-${row.stage}`);
    tr.append(el("td", "day23-num", String(number)));

    const piece = el("td", "day23-piece");
    piece.title = `${row.source} · ${row.section} · строки ${row.start_line}–${row.end_line}`;
    piece.append(el("code", "", row.source), el("small", "", row.section));
    if (row.via !== "question") piece.append(el("small", "day23-via", VIA[row.via]));
    tr.append(piece);

    const score = el("td", "day23-score");
    const bar = el("span", "day23-bar");
    const fill = el("span", "day23-bar__fill");
    // Шкала 0.3–0.8: у bge-m3 всё интересное лежит здесь.
    const scale = (value) => Math.max(0, Math.min(100, ((value - 0.3) / 0.5) * 100));
    fill.style.width = `${scale(row.score)}%`;
    const mark = el("span", "day23-bar__mark");
    mark.style.left = `${scale(threshold)}%`;
    bar.append(fill, mark);
    score.append(bar, el("small", "", row.score.toFixed(3)));
    tr.append(score);

    tr.append(el("td", "day23-rerank", row.rerank === null ? "—" : `${row.rerank}/10`));
    tr.append(el("td", "day23-stage", STAGES[row.stage]));
    return tr;
  }

  function showFunnel(event, threshold) {
    const box = funnelBox(event.mode);
    if (!box) return;
    const rows = event.candidates;
    const passed = rows.filter((row) => row.stage !== "threshold").length;
    let meta = `${rows.length} кандидатов → ${passed} прошли порог → ${pieces(event.context)} в контексте`;
    if (event.found_expected === true) meta += " · нужный файл дошёл";
    if (event.found_expected === false) meta += " · нужный файл не дошёл";
    if (event.cost.rerank?.error) meta += ` · реранкер: ${event.cost.rerank.error}`;
    box.querySelector("[data-funnel-meta]").textContent = meta;
    if (event.query) {
      const query = box.querySelector("[data-funnel-query]");
      query.replaceChildren(el("span", "day23-label", "запрос для поиска"), document.createTextNode(event.query));
      query.hidden = false;
    }
    // Ниже порога — свёрнуто в одну строку: до реранкера они не дошли.
    const body = box.querySelector("[data-funnel-rows]");
    body.replaceChildren(...rows.map((row, i) => candidateRow(row, i + 1, threshold)));
    const cut = rows.filter((row) => row.stage === "threshold");
    if (cut.length) {
      const hidden = [...body.querySelectorAll(".is-threshold")];
      hidden.forEach((tr) => (tr.hidden = true));
      const toggle = el("tr", "day23-cut");
      const cell = el("td", "", `[+] ещё ${cut.length} ниже порога ${String(threshold).replace(".", ",")} · ${cut[0].score.toFixed(3)}…${cut[cut.length - 1].score.toFixed(3)}`);
      cell.colSpan = 5;
      toggle.append(cell);
      toggle.addEventListener("click", () => {
        const show = hidden[0].hidden;
        hidden.forEach((tr) => (tr.hidden = !show));
        cell.textContent = cell.textContent.replace(show ? "[+]" : "[−]", show ? "[−]" : "[+]");
      });
      body.append(toggle);
    }
  }

  function fillCited(box, cited) {
    box.replaceChildren(el("span", "day23-label", cited.length ? "сослался на" : "ссылок на куски нет"));
    for (const item of cited) {
      const tag = el("span", "day23-cite");
      tag.append(el("b", "", `[${item.n}]`), document.createTextNode(` ${item.source}`));
      tag.title = item.section;
      box.append(tag);
    }
    box.hidden = false;
  }

  function fillChecks(list, grade) {
    list.replaceChildren();
    for (const fact of grade.facts) list.append(el("li", fact.found ? "is-found" : "is-missing", `${fact.found ? "✓" : "✗"} ${fact.label}`));
    const total = el("li", "day23-checks__total", `${verdict(grade)} · нужно ${grade.need}`);
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
      body.replaceChildren(el("p", "day23-error", event.error));
      return;
    }
    drafts[event.mode] = event.answer;
    markdown(body, event.answer);
    const meta = [pieces(event.context)];
    meta.push(event.skipped ? "модель не вызывалась" : seconds(event.stage_ms + event.ms));
    meta.push(`${number(event.tokens)} ток.`);
    box.querySelector("[data-answer-meta]").textContent = meta.join(" · ");
    if (!event.skipped) fillCited(box.querySelector("[data-answer-cited]"), event.cited);
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
    settingsForm.querySelectorAll("input").forEach((field) => (field.disabled = value));
  };

  async function ask(question) {
    if (busy) return;
    if (!settingsForm.reportValidity()) return;
    showError("");
    const current = settings();
    setBusy(true);
    resetRun();
    const drafts = { base: "", filter: "", rewrite: "" };
    let pending = null;
    const flush = () => {
      pending = null;
      for (const mode of MODES) if (drafts[mode]) markdown(answerBox(mode).querySelector("[data-answer-body]"), drafts[mode]);
    };
    try {
      const response = await post(form.dataset.askUrl, { question, settings: current });
      for await (const event of streamEvents(response.body)) {
        if (event.type === "probe") showProbe(event);
        else if (event.type === "funnel") {
          showFunnel(event, current.threshold);
          answerBox(event.mode).querySelector("[data-answer-meta]").textContent = "пишет…";
        } else if (event.type === "delta") {
          drafts[event.mode] += event.text;
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

  /* ── Тринадцать контрольных вопросов ───────────────────────────────── */

  const row = (index) => table.querySelector(`[data-probe-row="${index}"]`);
  const detail = (index) => table.querySelector(`[data-probe-detail="${index}"]`);

  function resetTable() {
    table.querySelectorAll("[data-probe-row]").forEach((tr) => {
      tr.classList.remove("is-done");
      tr.classList.add("is-waiting");
      tr.querySelectorAll("[data-verdict]").forEach((td) => {
        td.className = "day23-verdict";
        td.textContent = "…";
      });
    });
    table.querySelectorAll("[data-probe-detail]").forEach((tr) => (tr.hidden = true));
    table.querySelector("[data-compare-summary]").hidden = true;
  }

  function fillRow(event) {
    const tr = row(event.index);
    tr.classList.remove("is-waiting");
    tr.classList.add("is-done", "is-flash");
    setTimeout(() => tr.classList.remove("is-flash"), 900);
    for (const mode of MODES) {
      const result = event.modes[mode];
      const cell = tr.querySelector(`[data-verdict="${mode}"]`);
      const answer = detail(event.index).querySelector(`[data-detail-answer="${mode}"]`);
      if (result.error) {
        cell.className = "day23-verdict is-error";
        cell.textContent = "ошибка";
        answer.textContent = result.error;
        continue;
      }
      cell.className = `day23-verdict ${result.passed ? "is-passed" : "is-failed"}`;
      cell.replaceChildren(el("strong", "", event.negative ? (result.passed ? "✓ «в базе нет»" : "✗ ответил") : `${result.passed ? "✓" : "✗"} ${result.found}/${result.total} фактов`));
      const facts = [result.relevant === null ? pieces(result.context) : `${result.relevant} из ${pieces(result.context)} по делу`];
      if (result.found_expected !== null) facts.push(`файл ${result.found_expected ? "✓" : "✗"}`);
      cell.append(el("small", "", facts.join(" · ")));
      cell.append(el("small", "", `${number(result.tokens)} ток. · ${seconds(result.ms)}`));
      markdown(answer, result.answer);
      if (mode === "rewrite" && result.query) {
        const query = detail(event.index).querySelector("[data-detail-query]");
        query.replaceChildren(el("span", "day23-label", "запрос для поиска"), document.createTextNode(result.query));
        query.hidden = false;
      }
    }
  }

  function fillSummary(event) {
    const foot = table.querySelector("[data-compare-summary]");
    const values = {
      passed: (s) => `${s.passed} из ${event.on_topic}`,
      refused: (s) => `${s.refused} из ${event.off_topic}`,
      found_expected: (s) => `${s.found_expected} из ${event.on_topic}`,
      avg_context: (s) => String(s.avg_context).replace(".", ","),
      precision: (s) => `${Math.round(s.precision * 100)}%`,
      tokens: (s) => number(s.tokens),
      avg_ms: (s) => seconds(s.avg_ms),
    };
    for (const [key, format] of Object.entries(values)) {
      for (const mode of MODES) foot.querySelector(`[data-summary-row="${key}"] [data-summary-cell="${mode}"]`).textContent = format(event.summary[mode]);
    }
    foot.hidden = false;
  }

  compareButton.addEventListener("click", async () => {
    if (busy) return;
    if (!settingsForm.reportValidity()) return;
    const current = settings();
    setBusy(true);
    resetTable();
    compareStatus.textContent = "";
    let done = 0;
    try {
      const response = await post(compareButton.dataset.compareUrl, { settings: current });
      for await (const event of streamEvents(response.body)) {
        if (event.type === "compare_start") compareStatus.textContent = `Идут по три вопроса сразу… 0 из ${event.total}`;
        else if (event.type === "compare_row") {
          fillRow(event);
          done += 1;
          compareStatus.textContent = `Идут по три вопроса сразу… ${done} из 13`;
        } else if (event.type === "compare_summary") {
          fillSummary(event);
          const s = event.summary;
          compareStatus.textContent =
            `Готово: по ожиданию ${s.base.passed} → ${s.filter.passed} → ${s.rewrite.passed} из ${event.on_topic}, ` +
            `«в базе нет» ${s.base.refused} → ${s.filter.refused} → ${s.rewrite.refused} из ${event.off_topic}. Нажмите на строку — откроются ответы.`;
        } else if (event.type === "error") compareStatus.textContent = event.error;
      }
    } catch (error) {
      compareStatus.textContent = error.message;
    } finally {
      table.querySelectorAll(".is-waiting").forEach((tr) => tr.classList.remove("is-waiting"));
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
