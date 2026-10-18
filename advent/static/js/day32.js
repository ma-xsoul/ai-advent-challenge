/* День 32: пайплайн AI-ревью — запуски в GitHub и тот же ревьюер на паре веток. */
(() => {
  const lab = document.querySelector(".day32-lab");
  if (!lab) return;

  const $ = (selector) => lab.querySelector(selector);
  const form = $("[data-review-form]");
  const submit = form.querySelector('[type="submit"]');
  const error = $("[data-error]");
  const result = $("[data-result]");
  const diffBox = $("[data-diff]");
  const ragBox = $("[data-rag]");
  const reviewBox = $("[data-review]");
  const markdownWrap = $("[data-markdown-wrap]");
  const markdownBox = $("[data-markdown]");
  const runsList = $("[data-runs]");

  const escape = (text) => String(text ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const number = (value, digits = 1) => Number(value).toLocaleString("ru-RU", { maximumFractionDigits: digits });
  const readJson = async (response) => { try { return await response.json(); } catch { return {}; } };
  const SECTIONS = [["bugs", "Потенциальные баги"], ["architecture", "Архитектурные проблемы"], ["recommendations", "Рекомендации"]];
  const SEVERITY = { high: "высокая", medium: "средняя", low: "низкая" };
  const STATUS = { A: "новый", M: "изменён", D: "удалён" };

  // ── запуски в GitHub ──────────────────────────────────────────────────

  const loadRuns = async () => {
    runsList.innerHTML = '<li class="day32-muted">загружаем…</li>';
    const response = await fetch("api/runs").catch(() => null);
    const data = response ? await readJson(response) : {};
    if (!response?.ok) {
      runsList.innerHTML = `<li class="day32-muted">${escape(data.error || "GitHub недоступен")}</li>`;
      return;
    }
    runsList.innerHTML = data.runs.length
      ? data.runs.map((run) => {
        const state = run.status === "completed" ? (run.conclusion === "success" ? "ok" : "bad") : "wait";
        const label = run.status === "completed" ? (run.conclusion === "success" ? "готово" : run.conclusion) : "идёт";
        return `<li class="is-${state}"><a href="${escape(run.url)}" target="_blank" rel="noopener">#${run.number}</a> `
          + `${run.pr ? `PR #${run.pr} · ` : ""}${escape(run.title)} · ${escape(run.branch)} · <b>${escape(label)}</b> · ${escape(String(run.created_at).slice(0, 16).replace("T", " "))}</li>`;
      }).join("")
      : `<li class="day32-muted">Запусков пока нет — откройте PR: <a href="${escape(data.pulls)}" target="_blank" rel="noopener">pull requests ↗</a></li>`;
  };

  $("[data-runs-refresh]").addEventListener("click", loadRuns);

  // ── ревью ветки ───────────────────────────────────────────────────────

  const renderDiff = (event) => {
    const included = new Set(event.included);
    const rows = event.files.map((file) => `<li class="${included.has(file.path) ? "" : "is-skipped"}"><span class="day32-state">${escape(STATUS[file.status] || file.status)}</span> ${escape(file.path)} `
      + `<span class="day32-add">+${file.added ?? "?"}</span> <span class="day32-del">−${file.removed ?? "?"}</span>${included.has(file.path) ? "" : " <em>не вошёл</em>"}</li>`).join("");
    diffBox.innerHTML = `<p class="day32-meta">${escape(event.head)} → ${escape(event.base)} · коммитов ${event.commits.length} · файлов ${event.files.length}, в ревью ${event.included.length} · diff ${number(event.diff_chars / 1000)} тыс. символов</p>`
      + `<ul class="day32-files">${rows}</ul>`;
    ragBox.innerHTML = '<p class="day32-muted">ищем по документации и коду…</p>';
  };

  const renderRag = (event) => {
    const queries = event.queries.map((query) => `<li><code>${escape(query.length > 140 ? `${query.slice(0, 139)}…` : query)}</code></li>`).join("");
    const sources = event.sources.map((card) => `<li data-source="${card.n}" class="is-${escape(card.kind)}"><b>[${card.n}]</b> ${escape(card.source)} · ${escape(card.section)} · строки ${escape(card.lines)}</li>`).join("");
    ragBox.innerHTML = `<p class="day32-meta">поиск: ${escape(event.retriever)} · запросов из diff ${event.queries.length} · кусков ${event.sources.length} (изменённый код не ищем — нужны правила и соседи)</p>`
      + `<details class="day32-fold"><summary>запросы</summary><ul class="day32-queries">${queries}</ul></details>`
      + `<ol class="day32-sources">${sources}</ol>`;
    reviewBox.innerHTML = '<p class="day32-muted">модель читает diff, файлы и куски…</p>';
  };

  const renderReview = (event) => {
    const sections = SECTIONS.map(([key, title]) => {
      const items = event[key];
      const cards = items.length
        ? items.map((item) => `<li class="day32-item is-${escape(item.severity)}"><p class="day32-item__title"><span>${escape(SEVERITY[item.severity])}</span> ${escape(item.title)}</p>`
          + `<p class="day32-item__where"><code>${escape(item.file)}${item.line ? `:${item.line}` : ""}</code>${item.sources.length ? ` · по ${item.sources.map((n) => `[${n}]`).join(" ")}` : ""}</p>`
          + (item.why ? `<p><b>почему:</b> ${escape(item.why)}</p>` : "")
          + (item.fix ? `<p><b>как исправить:</b> ${escape(item.fix)}</p>` : "") + "</li>").join("")
        : '<li class="day32-muted">Не нашёл.</li>';
      return `<section class="day32-section"><h4>${escape(title)} · ${items.length}</h4><ul>${cards}</ul></section>`;
    }).join("");
    const stats = event.stats;
    reviewBox.innerHTML = `<p class="day32-summary">${escape(event.summary)}</p>${sections}`
      + `<p class="day32-meta">${escape(stats.model)} · токенов ${number(stats.prompt_tokens + stats.completion_tokens, 0)} · ${number(stats.ms / 1000)} с</p>`;
    const used = new Set(SECTIONS.flatMap(([key]) => event[key].flatMap((item) => item.sources)));
    used.forEach((n) => ragBox.querySelector(`[data-source="${n}"]`)?.classList.add("is-used"));
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hidden = true;
    submit.disabled = true;
    result.hidden = false;
    markdownWrap.hidden = true;
    diffBox.innerHTML = '<p class="day32-muted">собираем diff…</p>';
    ragBox.innerHTML = '<p class="day32-muted">ждём diff…</p>';
    reviewBox.innerHTML = '<p class="day32-muted">ждём…</p>';
    const body = {
      head: form.elements.head.value,
      base: form.elements.base.value,
      retriever: form.querySelector('[name="retriever"]:checked').value,
    };
    try {
      const response = await fetch("api/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!response.ok) throw new Error((await readJson(response)).error || `HTTP ${response.status}`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let newline;
        while ((newline = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, newline).trim();
          buffer = buffer.slice(newline + 1);
          if (!line) continue;
          const item = JSON.parse(line);
          if (item.type === "diff") renderDiff(item);
          else if (item.type === "retrieved") renderRag(item);
          else if (item.type === "review") renderReview(item);
          else if (item.type === "markdown") {
            markdownBox.textContent = item.text;
            markdownWrap.hidden = false;
          } else if (item.type === "error") throw new Error(item.error);
        }
      }
    } catch (caught) {
      error.textContent = caught.message || "Ревью не получилось";
      error.hidden = false;
    }
    submit.disabled = false;
  });

  $("[data-copy]").addEventListener("click", async (event) => {
    event.preventDefault();
    const button = event.currentTarget;
    try {
      await navigator.clipboard.writeText(markdownBox.textContent);
      button.textContent = "скопирован";
    } catch {
      button.textContent = "не вышло";
    }
    window.setTimeout(() => { button.textContent = "копировать"; }, 1500);
  });

  loadRuns();
})();
