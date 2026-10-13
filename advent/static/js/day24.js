/*
 * День 24: цитаты, источники и анти-галлюцинации.
 * Вопрос: probe? → retrieved (что нашёл поиск, релевантность) → answer
 * (статус, текст, источники, цитаты с проверкой) → judge → checks.
 * У каждой цитаты можно раскрыть её кусок: цитата в нём подсвечена,
 * если нашлась символ в символ.
 * Прогон 13 вопросов идёт по три сразу — строки заполняются в порядке
 * готовности. Тексты кусков и цитат — только через textContent; ответ
 * модели — через общий renderMarkdown, он экранирует HTML.
 */
(() => {
  const form = document.querySelector("#day24-ask");
  if (!form) return;

  const input = document.querySelector("#day24-question");
  const askButton = form.querySelector("button[type=submit]");
  const settingsForm = document.querySelector("[data-settings]");
  const errorBox = document.querySelector("#day24-error");
  const run = document.querySelector("[data-run]");
  const picks = document.querySelector("[data-picks]");
  const compareButton = document.querySelector("#day24-compare");
  const compareStatus = document.querySelector("#day24-compare-status");
  const table = document.querySelector("[data-compare-table]");
  const QUOTE_TITLES = JSON.parse(document.querySelector("#day24-quote-titles").textContent);
  const QUOTE_MARKS = { exact: "✓", near: "✓", elsewhere: "≈", partial: "≈", stitched: "≈", missing: "✗" };
  const VERDICTS = { yes: "✓ да", partial: "≈ частично", no: "✗ нет" };

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

  const settings = () => {
    const data = Object.fromEntries(new FormData(settingsForm).entries());
    return { threshold: Number(data.threshold), rerank_min: Number(data.rerank_min) };
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

  /* ── Один вопрос ───────────────────────────────────────────────────── */

  const part = (selector) => run.querySelector(selector);

  function resetRun() {
    run.hidden = false;
    part("[data-retrieved]").textContent = "Ищем в базе…";
    part("[data-status]").className = "day24-badge";
    part("[data-status]").textContent = "ждёт";
    part("[data-answer-body]").replaceChildren(el("p", "day24-muted", "…"));
    part("[data-clarify]").hidden = true;
    part("[data-sources]").replaceChildren(el("li", "day24-muted", "—"));
    part("[data-quotes]").replaceChildren(el("li", "day24-muted", "—"));
    part("[data-judge]").replaceChildren(el("p", "day24-muted", "—"));
    part("[data-checks]").hidden = true;
  }

  function showRetrieved(event) {
    const relevance = event.relevance === null ? "реранкер не оценивал" : `лучший кусок ${event.relevance}/10`;
    const threshold = event.settings.rerank_min;
    let text = `Поиск: ${event.candidates} кандидатов → ${event.passed} прошли порог похожести → ${event.context.length} в контексте · ${relevance}, порог ${threshold}`;
    if (!event.context.length) text += " — ниже порога, отвечать нечем";
    part("[data-retrieved]").textContent = text;
    part("[data-status]").textContent = event.context.length ? "пишет…" : "уточняет…";
  }

  // Кусок с подсвеченной цитатой. Пробелы цитаты совпадают с любыми
  // пробелами куска: модель часто склеивает переносы строк и отступы.
  function chunkWithMark(chunk, quote) {
    const pre = el("pre", "day24-chunk");
    const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const words = quote.trim().split(/\s+/).filter(Boolean);
    const match = words.length ? new RegExp(words.map(escape).join("\\s+")).exec(chunk) : null;
    if (match) pre.append(chunk.slice(0, match.index), el("mark", "", match[0]), chunk.slice(match.index + match[0].length));
    else pre.textContent = chunk;
    return pre;
  }

  function sourceItem(source) {
    const item = el("li", "day24-source");
    item.append(el("b", "", `[${source.n}]`), el("code", "", source.source), el("small", "", source.section));
    const meta = [`chunk_id ${source.chunk_id}`];
    if (source.lines) meta.push(`строки ${source.lines[0]}–${source.lines[1]}`);
    item.append(el("small", "day24-source__id", meta.join(" · ")));
    return item;
  }

  function quoteItem(quote) {
    const item = el("li", `day24-quote is-${quote.status}`);
    const head = el("p", "day24-quote__head");
    head.append(
      el("span", "day24-quote__mark", QUOTE_MARKS[quote.status]),
      el("span", "", QUOTE_TITLES[quote.status] + (quote.status === "elsewhere" ? ` [${quote.n}], а не [${quote.claimed}]` : "")),
      el("small", "", quote.source ? `[${quote.n}] ${quote.source}` : `[${quote.n}]`),
    );
    item.append(head, el("blockquote", "", quote.text));
    if (quote.chunk) {
      const fold = el("details", "day24-fold");
      fold.append(el("summary", "", "кусок целиком"), chunkWithMark(quote.chunk, quote.text));
      item.append(fold);
    }
    return item;
  }

  function showAnswer(event) {
    const badge = part("[data-status]");
    badge.className = `day24-badge is-${event.status}`;
    badge.textContent = event.status === "unknown" ? (event.reason === "relevance" ? "не знаю · ниже порога" : "не знаю · решила модель") : "ответ";
    markdown(part("[data-answer-body]"), event.answer);
    const clarifyBox = part("[data-clarify]");
    clarifyBox.hidden = !event.clarify;
    clarifyBox.replaceChildren(el("span", "day24-label", "уточните"), document.createTextNode(event.clarify || ""));

    const sources = part("[data-sources]");
    sources.replaceChildren(...(event.sources.length ? event.sources.map(sourceItem) : [el("li", "day24-muted", "нет")]));
    const quotes = part("[data-quotes]");
    quotes.replaceChildren(...(event.quotes.length ? event.quotes.map(quoteItem) : [el("li", "day24-muted", "нет")]));
    if (!event.parsed) quotes.prepend(el("li", "day24-error", "Модель ответила не JSON — цитат нет."));
    if (event.status === "unknown") part("[data-judge]").replaceChildren(el("p", "day24-muted", "не нужен: ответа нет"));
    else if (!event.quotes.length) part("[data-judge]").replaceChildren(el("p", "day24-muted", "нечего проверять: цитат нет"));
    else part("[data-judge]").replaceChildren(el("p", "day24-muted", "проверяет…"));
  }

  function showJudge(event) {
    const box = part("[data-judge]");
    box.replaceChildren(el("p", `day24-verdict is-${event.verdict || "none"}`, VERDICTS[event.verdict] || "судья не ответил"));
    if (event.comment) box.append(el("p", "day24-judge__comment", event.comment));
    if (event.unsupported.length) {
      const list = el("ul", "day24-unsupported");
      for (const claim of event.unsupported) list.append(el("li", "", claim));
      box.append(el("p", "day24-label", "без опоры в цитатах"), list);
    }
  }

  function showChecks(event) {
    const list = part("[data-checks]");
    const mark = (ok) => (ok ? "✓" : "✗");
    const items = event.unknown
      ? [`${mark(true)} «не знаю» вместо выдумки`]
      : [
          `${mark(event.has_sources)} источники`,
          `${mark(event.has_quotes)} цитаты`,
          `${mark(event.quotes_total && event.quotes_verified === event.quotes_total)} цитат нашлось ${event.quotes_verified}/${event.quotes_total}`,
          `${event.meaning === "yes" ? "✓" : event.meaning === "partial" ? "≈" : "✗"} смысл совпадает`,
        ];
    if (event.negative && !event.unknown) items.push("✗ вопрос вне базы, а ассистент ответил");
    items.push(`${seconds(event.ms)} · ${number(event.tokens)} ток.`);
    list.replaceChildren(...items.map((text) => el("li", text.startsWith("✗") ? "is-bad" : text.startsWith("≈") ? "is-half" : "", text)));
    list.hidden = false;
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
    if (busy || !settingsForm.reportValidity()) return;
    showError("");
    const current = settings();
    setBusy(true);
    resetRun();
    try {
      const response = await post(form.dataset.askUrl, { question, settings: current });
      for await (const event of streamEvents(response.body)) {
        if (event.type === "retrieved") showRetrieved(event);
        else if (event.type === "answer") showAnswer(event);
        else if (event.type === "judge") showJudge(event);
        else if (event.type === "checks") showChecks(event);
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

  /* ── Тринадцать контрольных вопросов ───────────────────────────────── */

  const row = (index) => table.querySelector(`[data-probe-row="${index}"]`);
  const detail = (index) => table.querySelector(`[data-probe-detail="${index}"]`);

  function resetTable() {
    table.querySelectorAll("[data-probe-row]").forEach((tr) => {
      tr.classList.remove("is-done");
      tr.querySelectorAll("[data-cell]").forEach((td) => {
        td.className = "";
        td.textContent = "…";
      });
    });
    table.querySelectorAll("[data-probe-detail]").forEach((tr) => (tr.hidden = true));
    table.querySelector("[data-compare-summary]").hidden = true;
  }

  const cell = (tr, name, text, state) => {
    const td = tr.querySelector(`[data-cell="${name}"]`);
    td.className = state ? `is-${state}` : "";
    td.textContent = text;
  };

  function fillRow(event) {
    const tr = row(event.index);
    tr.classList.add("is-done", "is-flash");
    setTimeout(() => tr.classList.remove("is-flash"), 900);
    const box = detail(event.index).querySelector("[data-detail]");
    if (event.error) {
      for (const name of ["sources", "quotes", "meaning"]) cell(tr, name, "—");
      cell(tr, "passed", "ошибка", "bad");
      box.textContent = event.error;
      return;
    }
    const checks = event.checks;
    if (checks.unknown) {
      cell(tr, "sources", "—");
      cell(tr, "quotes", "—");
      const why = event.relevance === null ? "всё ниже порога похожести" : `лучший кусок ${event.relevance}/10`;
      cell(tr, "meaning", `«не знаю» · ${why}`, checks.negative ? "good" : "bad");
    } else {
      cell(tr, "sources", checks.has_sources ? `✓ ${event.sources.length}` : "✗ нет", checks.has_sources ? "good" : "bad");
      const allFound = checks.quotes_total && checks.quotes_verified === checks.quotes_total;
      cell(tr, "quotes", checks.has_quotes ? `${allFound ? "✓" : "✗"} ${checks.quotes_verified}/${checks.quotes_total} нашлись` : "✗ нет", allFound ? "good" : "bad");
      cell(tr, "meaning", VERDICTS[checks.meaning] || "—", checks.meaning === "yes" ? "good" : checks.meaning === "partial" ? "half" : "bad");
    }
    cell(tr, "passed", checks.passed ? "✓" : "✗", checks.passed ? "good" : "bad");

    box.replaceChildren();
    const answer = el("div", "markdown-body");
    markdown(answer, event.answer);
    box.append(answer);
    if (event.clarify) box.append(el("p", "day24-clarify", `Уточните: ${event.clarify}`));
    if (event.quotes.length) {
      const list = el("ul", "day24-detail__quotes");
      for (const quote of event.quotes) list.append(el("li", `is-${quote.status}`, `${QUOTE_MARKS[quote.status]} [${quote.n}] «${quote.text}»`));
      box.append(list);
    }
    if (event.judge?.comment) box.append(el("p", "day24-judge__comment", `Судья: ${event.judge.comment}`));
  }

  function fillSummary(event) {
    const foot = table.querySelector("[data-compare-summary]");
    const values = {
      with_sources: `${event.with_sources} из ${event.answered}`,
      with_quotes: `${event.with_quotes} из ${event.answered}`,
      quotes: `${event.quotes_verified} из ${event.quotes_total}`,
      meaning: `${event.meaning.yes} / ${event.meaning.partial} / ${event.meaning.no}`,
      passed: `${event.passed} из ${event.on_topic}`,
      false_unknown: String(event.false_unknown),
      unknown_off_topic: `${event.unknown_off_topic} из ${event.off_topic}`,
      tokens: number(event.tokens),
    };
    for (const [key, text] of Object.entries(values)) foot.querySelector(`[data-summary-row="${key}"] [data-summary-cell]`).textContent = text;
    foot.hidden = false;
  }

  compareButton.addEventListener("click", async () => {
    if (busy || !settingsForm.reportValidity()) return;
    const current = settings();
    setBusy(true);
    resetTable();
    let done = 0;
    compareStatus.textContent = "Идут по три вопроса сразу… 0 из 13";
    try {
      const response = await post(compareButton.dataset.compareUrl, { settings: current });
      for await (const event of streamEvents(response.body)) {
        if (event.type === "compare_row") {
          fillRow(event);
          done += 1;
          compareStatus.textContent = `Идут по три вопроса сразу… ${done} из 13`;
        } else if (event.type === "compare_summary") {
          fillSummary(event);
          compareStatus.textContent = `Готово: засчитано ${event.passed} из ${event.on_topic}, «не знаю» вне базы ${event.unknown_off_topic} из ${event.off_topic}. Нажмите на строку — откроется ответ с цитатами.`;
        } else if (event.type === "error") compareStatus.textContent = event.error;
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
