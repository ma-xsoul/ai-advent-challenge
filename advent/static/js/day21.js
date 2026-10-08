/*
 * День 21: индексация документов.
 * Сборка идёт потоком событий: corpus → chunked → embedding… → built →
 * done → контрольные вопросы по одному. Полоски показывают, сколько
 * векторов посчитано, таблица сравнения заполняется по мере готовности.
 * Вопросы проявляются по шагам: строка с вопросом → место нужного файла в
 * fixed → в structure → следующий вопрос. Поиск занимает сотые доли
 * секунды, поэтому шаги идут с паузой — иначе глаз ничего не успеет.
 * Тексты кусков — наш же код и страницы, но выводятся только через textContent.
 */
(() => {
  const buildButton = document.querySelector("#day21-build");
  if (!buildButton) return;

  const status = document.querySelector("#day21-status");
  const errorBox = document.querySelector("#day21-error");
  const progress = document.querySelector("[data-progress]");
  const indexMeta = document.querySelector("[data-index-meta]");
  const compare = document.querySelector("[data-compare]");
  const searchForm = document.querySelector("#day21-search");
  const probesButton = document.querySelector("#day21-probes");
  const probesTable = document.querySelector("[data-probes] tbody");
  const viewerPick = document.querySelector("[data-viewer-pick]");
  const initial = JSON.parse(document.querySelector("#day21-initial").textContent);

  const STRATEGIES = ["fixed", "structure"];
  const STEP_MS = 320;
  let info = initial;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const number = (value) => Number(value).toLocaleString("ru-RU");

  const showError = (text) => {
    errorBox.textContent = text;
    errorBox.hidden = !text;
  };

  // События NDJSON по одному — чтобы обработчик мог подождать между шагами.
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

  /* ── Таблица сравнения ─────────────────────────────────────────────── */

  const setCell = (row, strategy, text, best = false) => {
    const cell = compare.querySelector(`[data-row="${row}"] [data-cell="${strategy}"]`);
    if (!cell) return;
    cell.textContent = text;
    cell.classList.toggle("is-best", best);
  };

  const renderInfo = (data) => {
    info = data;
    if (!data.built) {
      indexMeta.textContent = "ещё не собран";
      return;
    }
    const built = new Date(data.built_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    indexMeta.textContent = `${data.model} · ${data.dims} измерений · ${built} · ${(data.bytes / 1048576).toFixed(1)} МБ · data/day21/index.sqlite3`;
    const corpusChars = data.corpus?.chars || 1;
    for (const strategy of STRATEGIES) {
      const stats = data.strategies?.[strategy];
      if (!stats) continue;
      setCell("chunks", strategy, number(stats.chunks));
      setCell("avg_chars", strategy, number(stats.avg_chars));
      setCell("range", strategy, `${number(stats.min_chars)} – ${number(stats.max_chars)}`);
      setCell("overlap", strategy, `${Math.round((stats.total_chars / corpusChars) * 100)}%`);
      setCell("ms", strategy, `${(stats.ms / 1000).toFixed(1)} с · новых векторов ${number(stats.embedded)}`);
    }
  };

  const renderSummary = (summary) => {
    for (const [row, key, format] of [
      ["hit1", "hit1", (v) => `${Math.round(v * 100)}%`],
      ["hit5", "hit5", (v) => `${Math.round(v * 100)}%`],
      ["mrr", "mrr", (v) => v.toFixed(2)],
    ]) {
      const values = STRATEGIES.map((strategy) => summary[strategy][key]);
      const best = Math.max(...values);
      STRATEGIES.forEach((strategy, index) => setCell(row, strategy, format(values[index]), values[index] === best && values.some((v) => v !== best)));
    }
  };

  /* ── Сборка ────────────────────────────────────────────────────────── */

  const setProgress = (strategy, done, total, text) => {
    const row = progress.querySelector(`[data-progress-strategy="${strategy}"]`);
    if (!row) return;
    row.querySelector("[data-progress-fill]").style.width = `${total ? Math.round((done / total) * 100) : 100}%`;
    row.querySelector("[data-progress-text]").textContent = text;
  };

  buildButton.addEventListener("click", async () => {
    buildButton.disabled = true;
    showError("");
    progress.hidden = false;
    STRATEGIES.forEach((strategy) => setProgress(strategy, 0, 1, "ждёт"));
    status.textContent = "читаю корпус…";
    try {
      const response = await fetch(buildButton.dataset.buildUrl, { method: "POST" });
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
      for await (const event of streamEvents(response.body)) {
        if (event.type.startsWith("probe_")) {
          await showProbe(event);
        } else if (event.type === "corpus") {
          status.textContent = `${event.documents} файлов · ≈ ${event.pages} стр. — режу на куски…`;
        } else if (event.type === "chunked") {
          setProgress(event.strategy, 0, 1, `${number(event.chunks)} кусков — считаю векторы…`);
        } else if (event.type === "embedding") {
          const cached = event.cached ? ` · из кэша ${number(event.cached)}` : "";
          setProgress(event.strategy, event.done, event.total, `векторов ${number(event.done)} из ${number(event.total)}${cached}`);
          status.textContent = `${event.strategy}: эмбеддинги bge-m3…`;
        } else if (event.type === "built") {
          setProgress(event.strategy, 1, 1, `готово · ${number(event.chunks)} кусков · ${(event.ms / 1000).toFixed(1)} с`);
        } else if (event.type === "done") {
          renderInfo(event.info);
          status.textContent = "индекс собран — прогоняю контрольные вопросы…";
        } else if (event.type === "error") {
          throw new Error(event.error);
        }
      }
    } catch (error) {
      showError(error.message);
      status.textContent = "";
    } finally {
      buildButton.disabled = false;
    }
  });

  /* ── Поиск ─────────────────────────────────────────────────────────── */

  const hitItem = (hit) => {
    const item = el("li", "day21-hit");
    const head = el("div", "day21-hit__head");
    head.append(el("span", "day21-hit__rank", `#${hit.rank}`));
    head.append(el("span", "day21-hit__score", hit.score.toFixed(3)));
    head.append(el("span", `day21-kind day21-kind--${hit.kind}`, hit.kind));
    item.append(head);
    item.append(el("code", "day21-hit__source", `${hit.source}:${hit.start_line}–${hit.end_line}`));
    item.append(el("p", "day21-hit__section", hit.section));
    const details = el("details", "day21-fold");
    details.append(el("summary", "", `${hit.chunk_id} · ${hit.text.length} симв.`));
    details.append(el("pre", "", hit.text));
    item.append(details);
    return item;
  };

  searchForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = searchForm.elements.q.value.trim();
    if (!query) return;
    showError("");
    STRATEGIES.forEach((strategy) => {
      const list = document.querySelector(`[data-hits="${strategy}"]`);
      list.replaceChildren(el("li", "day21-hits__empty", "ищу…"));
    });
    try {
      const response = await fetch(`${searchForm.dataset.searchUrl}?q=${encodeURIComponent(query)}&k=5`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      for (const strategy of STRATEGIES) {
        const list = document.querySelector(`[data-hits="${strategy}"]`);
        list.replaceChildren(...data.results[strategy].map(hitItem));
      }
    } catch (error) {
      showError(error.message);
      STRATEGIES.forEach((strategy) => document.querySelector(`[data-hits="${strategy}"]`).replaceChildren());
    }
  });

  /* ── Контрольные вопросы ───────────────────────────────────────────── */

  const rankCell = (result) => {
    const cell = el("td", "day21-rank");
    if (result.rank === 1) {
      cell.classList.add("is-first");
      cell.textContent = "✓ 1";
    } else if (result.rank) {
      cell.classList.add("is-near");
      cell.textContent = `${result.rank}`;
    } else {
      cell.classList.add("is-miss");
      cell.textContent = "—";
    }
    if (result.top) cell.title = `первым: ${result.top.source} › ${result.top.section} (${result.top.score})`;
    return cell;
  };

  // Пошаговый прогон: строка появляется с вопросом, клетки стратегий — по мере ответа.
  const probeRows = new Map();
  let probeTotal = 0;
  // Номер прогона: ответ, пришедший для старого прогона, таблицу не трогает.
  let probeRun = 0;

  const showProbe = async (event) => {
    if (event.type === "probe_start") {
      probeRun += 1;
      probesTable.replaceChildren();
      probeRows.clear();
      probeTotal = event.total;
      ["hit1", "hit5", "mrr"].forEach((row) => STRATEGIES.forEach((strategy) => setCell(row, strategy, "…")));
      status.textContent = `контрольные вопросы: 0 из ${event.total}`;
    } else if (event.type === "probe_question") {
      const tr = el("tr", "day21-probes__row is-entering");
      tr.append(el("td", "day21-probes__question", event.question));
      tr.append(el("td", "day21-probes__sources", event.sources.map((source) => source.replace("advent/", "")).join(", ")));
      const cells = {};
      STRATEGIES.forEach((strategy) => {
        cells[strategy] = el("td", "day21-rank is-pending", "ищу…");
        tr.append(cells[strategy]);
      });
      probeRows.set(event.index, cells);
      probesTable.append(tr);
      status.textContent = `контрольные вопросы: ${event.index} из ${probeTotal}`;
      requestAnimationFrame(() => tr.classList.remove("is-entering"));
      await sleep(STEP_MS);
    } else if (event.type === "probe_result") {
      const cell = probeRows.get(event.index)?.[event.strategy];
      if (cell) {
        const filled = rankCell(event);
        cell.className = `${filled.className} is-flash`;
        cell.textContent = filled.textContent;
        cell.title = filled.title;
      }
      await sleep(STEP_MS * 0.7);
    } else if (event.type === "probe_summary") {
      renderSummary(event.summary);
      status.textContent = "готово";
    }
  };

  const renderProbes = (data) => {
    probesTable.replaceChildren();
    for (const row of data.questions) {
      const tr = el("tr");
      tr.append(el("td", "day21-probes__question", row.question));
      tr.append(el("td", "day21-probes__sources", row.sources.map((source) => source.replace("advent/", "")).join(", ")));
      STRATEGIES.forEach((strategy) => tr.append(rankCell(row[strategy])));
      probesTable.append(tr);
    }
    renderSummary(data.summary);
  };

  // При открытии страницы — сразу всей таблицей, без пауз.
  const loadProbes = async () => {
    const run = ++probeRun;
    try {
      const response = await fetch(probesButton.dataset.probesUrl);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      if (run === probeRun) renderProbes(data);
    } catch (error) {
      showError(error.message);
    }
  };

  // По кнопке — по одному вопросу.
  probesButton.addEventListener("click", async () => {
    probesButton.disabled = true;
    buildButton.disabled = true;
    showError("");
    try {
      const response = await fetch(probesButton.dataset.probesStreamUrl);
      if (!response.ok || !response.body) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      for await (const event of streamEvents(response.body)) {
        if (event.type === "error") throw new Error(event.error);
        await showProbe(event);
      }
    } catch (error) {
      showError(error.message);
      status.textContent = "";
    } finally {
      probesButton.disabled = false;
      buildButton.disabled = false;
    }
  });

  /* ── Как нарезан файл ──────────────────────────────────────────────── */

  viewerPick.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-source]");
    if (!button) return;
    viewerPick.querySelectorAll("[data-source]").forEach((item) => item.classList.toggle("is-active", item === button));
    try {
      const response = await fetch(`${viewerPick.dataset.chunksUrl}?source=${encodeURIComponent(button.dataset.source)}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      for (const strategy of STRATEGIES) {
        const list = document.querySelector(`[data-pieces="${strategy}"]`);
        list.replaceChildren(
          ...data.strategies[strategy].map((piece) => {
            const item = el("li", "day21-piece");
            const head = el("div", "day21-hit__head");
            head.append(el("span", "day21-hit__rank", piece.chunk_id.split("#")[1]));
            head.append(el("code", "", `строки ${piece.start_line}–${piece.end_line}`));
            head.append(el("small", "", `${piece.chars} симв.`));
            item.append(head);
            item.append(el("p", "day21-hit__section", piece.section));
            const details = el("details", "day21-fold");
            details.append(el("summary", "", "текст куска"));
            details.append(el("pre", "", piece.text));
            item.append(details);
            return item;
          }),
        );
      }
    } catch (error) {
      showError(error.message);
    }
  });

  renderInfo(initial);
  if (initial.built) loadProbes();
})();
