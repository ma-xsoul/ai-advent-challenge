/* День 31: терминал ассистента разработчика — /help через агента, команды git без модели. */
(() => {
  const lab = document.querySelector(".day31-lab");
  if (!lab) return;

  const initial = JSON.parse(document.querySelector("#day31-initial")?.textContent || "{}");
  const log = lab.querySelector("[data-log]");
  const empty = lab.querySelector("[data-log-empty]");
  const form = lab.querySelector("[data-prompt]");
  const input = form.querySelector("input");
  const submit = form.querySelector("button");
  const rebuild = lab.querySelector("[data-rebuild]");
  const indexCard = lab.querySelector("[data-index-card]");
  const indexState = lab.querySelector("[data-index-state]");

  let busy = false;
  let ready = Boolean(initial.ready);

  const number = (value, digits = 1) => Number(value).toLocaleString("ru-RU", { maximumFractionDigits: digits });
  const escape = (text) => String(text ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const markdown = (text) => (typeof window.renderMarkdown === "function" ? window.renderMarkdown(text) : escape(text));
  const readJson = async (response) => { try { return await response.json(); } catch { return {}; } };

  const setBusy = (value) => {
    busy = value;
    submit.disabled = value;
    rebuild.disabled = value;
  };

  const entry = (commandText) => {
    empty.hidden = true;
    const block = document.createElement("article");
    block.className = "day31-entry";
    block.innerHTML = `<p class="day31-entry__cmd"><span aria-hidden="true">&gt;</span> ${escape(commandText)}</p><div class="day31-entry__body"></div>`;
    log.append(block);
    block.scrollIntoView({ block: "nearest", behavior: "smooth" });
    return block.querySelector(".day31-entry__body");
  };

  const line = (body, html, kind = "") => {
    const node = document.createElement("div");
    node.className = `day31-line${kind ? ` day31-line--${kind}` : ""}`;
    node.innerHTML = html;
    body.append(node);
    return node;
  };

  async function* ndjson(response) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const text = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (text) yield JSON.parse(text);
      }
    }
  }

  // ── /help без вопроса — справка ───────────────────────────────────────

  const showHelp = (body) => {
    const commands = initial.commands.map((item) => `<li><code>${escape(item.name)}</code> — ${escape(item.title)}, прямо из git</li>`).join("");
    line(body, `<ul class="day31-list"><li><code>/help &lt;вопрос&gt;</code> — вопрос о проекте: поиск по README, docs и коду + git через MCP</li>${commands}</ul>`
      + `<p class="day31-muted">Например: ${initial.examples.slice(0, 3).map((q) => `<code>/help ${escape(q)}</code>`).join(" · ")}</p>`);
  };

  // ── /help <вопрос> — агент ────────────────────────────────────────────

  const toolTitle = (event) => {
    const args = event.arguments && typeof event.arguments === "object" ? Object.entries(event.arguments).filter(([, v]) => v !== "" && v !== undefined) : [];
    return `${escape(event.name)}(${args.map(([k, v]) => `${escape(k)}=${escape(JSON.stringify(v))}`).join(", ")})`;
  };

  const askHelp = async (question, body) => {
    const started = performance.now();
    const status = line(body, "ищем в документации…", "muted");
    let hitsNode = null;
    let hits = [];
    const calls = [];
    const response = await fetch("api/help", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) });
    if (!response.ok) throw new Error((await readJson(response)).error || `HTTP ${response.status}`);
    for await (const event of ndjson(response)) {
      if (event.type === "retrieved") {
        hits = event.hits;
        const items = hits.map((hit) => `<li data-hit="${hit.n}"><b>[${hit.n}]</b> ${escape(hit.source)} · ${escape(hit.section)} · строки ${escape(hit.lines)} <span>${number(hit.score, 3)}</span></li>`).join("");
        hitsNode = line(body, `<details class="day31-fold"><summary>RAG · найдено ${hits.length} кусков: ${escape([...new Set(hits.map((h) => h.source))].join(", "))}</summary><ol class="day31-hits">${items}</ol></details>`, "rag");
        status.textContent = "агент думает…";
        body.append(status);
      } else if (event.type === "connected") {
        status.textContent = `MCP: подключён «${event.server}» · ${event.tools.length} инструментов · агент думает…`;
      } else if (event.type === "tool_call") {
        calls.push(line(body, `<span class="day31-tag">MCP</span> ${toolTitle(event)} <span class="day31-muted">…</span>`, "tool"));
        body.append(status);
      } else if (event.type === "tool_result") {
        const node = calls.find((item) => item.dataset.done !== "1") || line(body, "", "tool");
        node.dataset.done = "1";
        node.innerHTML = `<span class="day31-tag${event.ok ? "" : " is-bad"}">MCP</span> ${toolTitle(event)} → <span class="day31-muted">${number(event.chars, 0)} символов · ${event.ms} мс</span>`
          + `<details class="day31-fold"><summary>результат</summary><pre>${escape(event.preview)}</pre></details>`;
      } else if (event.type === "answer") {
        status.remove();
        line(body, markdown(event.content), "answer").classList.add("markdown-body");
        (event.cited || []).forEach((n) => hitsNode?.querySelector(`[data-hit="${n}"]`)?.classList.add("is-cited"));
        if (hitsNode && event.cited?.length) {
          hitsNode.querySelector("summary").insertAdjacentHTML("beforeend", ` · <em>в ответе ${event.cited.map((n) => `[${n}]`).join(" ")}</em>`);
        }
      } else if (event.type === "done") {
        const stats = event.stats || {};
        line(body, `${calls.length ? `git: ${calls.length} ${calls.length === 1 ? "вызов" : "вызова"}` : "git не понадобился"} · запросов к модели ${stats.llm_calls ?? "?"} · токенов ${number((stats.prompt_tokens || 0) + (stats.completion_tokens || 0), 0)} · ${number((performance.now() - started) / 1000)} с`, "meta");
      } else if (event.type === "error") {
        throw new Error(event.error);
      }
    }
  };

  // ── команды git ───────────────────────────────────────────────────────

  const renderCommand = (body, result) => {
    const data = result.data || {};
    if (!result.ok) {
      line(body, escape(result.error || "git не ответил"), "error");
      return;
    }
    if (result.tool === "git_branch") {
      const head = data.head ? `${escape(data.head.hash)} · ${escape(data.head.date)} · ${escape(data.head.subject)}` : "—";
      line(body, `<dl class="day31-kv"><dt>ветка</dt><dd><b>${escape(data.branch)}</b></dd><dt>последний коммит</dt><dd>${head}</dd>`
        + `<dt>не закоммичено</dt><dd>${data.uncommitted_files} файлов</dd>`
        + (data.base ? `<dt>от ${escape(data.base)}</dt><dd>впереди на ${data.ahead_of_base} коммитов, позади на ${data.behind_base}</dd>` : "") + "</dl>");
    } else if (result.tool === "git_status") {
      line(body, data.files.length
        ? `<ul class="day31-list">${data.files.map((f) => `<li><span class="day31-state">${escape(f.state)}</span> ${escape(f.path)}</li>`).join("")}</ul>`
        : "Рабочая копия чистая — всё закоммичено");
    } else if (result.tool === "git_diff") {
      const files = data.files.map((f) => `<li>${escape(f.path)} <span class="day31-add">+${f.added ?? "?"}</span> <span class="day31-del">−${f.removed ?? "?"}</span></li>`).join("");
      const untracked = data.untracked.length ? `<p class="day31-muted">ещё не в git: ${data.untracked.map(escape).join(", ")}</p>` : "";
      const diff = data.diff.split("\n").map((row) => {
        const kind = row.startsWith("+") && !row.startsWith("+++") ? "add" : row.startsWith("-") && !row.startsWith("---") ? "del" : row.startsWith("@@") ? "hunk" : "";
        return `<span class="day31-diff${kind ? ` day31-diff--${kind}` : ""}">${escape(row)}</span>`;
      }).join("\n");
      line(body, `${files ? `<ul class="day31-list">${files}</ul>` : "Изменений в отслеживаемых файлах нет"}${untracked}`
        + (data.diff ? `<details class="day31-fold"><summary>diff${data.truncated ? " · обрезан" : ""}</summary><pre class="day31-pre">${diff}</pre></details>` : ""));
    } else if (result.tool === "git_log") {
      line(body, `<ul class="day31-list">${data.commits.map((c) => `<li><code>${escape(c.hash)}</code> ${escape(c.date)} · ${escape(c.subject)}</li>`).join("")}</ul>`);
    }
    line(body, `MCP · ${escape(result.tool)} · ${result.ms} мс · без модели`, "meta");
  };

  const runCommand = async (text, body) => {
    const response = await fetch("api/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ command: text }) });
    const data = await readJson(response);
    if (response.status === 400) throw new Error(data.error);
    renderCommand(body, data);
  };

  // ── ввод ──────────────────────────────────────────────────────────────

  const run = async (raw) => {
    const text = raw.trim();
    if (!text || busy) return;
    setBusy(true);
    const body = entry(text);
    try {
      const [head] = text.split(/\s+/);
      if (head.toLowerCase() === "/help" || !text.startsWith("/")) {
        const question = head.toLowerCase() === "/help" ? text.slice(head.length).trim() : text;
        if (!question) showHelp(body);
        else if (!ready) throw new Error("Индекс ещё не собран — нажмите «Собрать индекс»");
        else await askHelp(question, body);
      } else {
        await runCommand(text, body);
      }
    } catch (error) {
      line(body, escape(error.message || "Не получилось"), "error");
    }
    setBusy(false);
    input.focus();
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value;
    input.value = "";
    run(text);
  });

  lab.querySelectorAll("[data-run]").forEach((button) => button.addEventListener("click", () => run(button.dataset.run)));
  lab.querySelectorAll("[data-fill]").forEach((button) => button.addEventListener("click", () => {
    input.value = button.dataset.fill;
    input.focus();
  }));
  lab.querySelector("[data-clear]").addEventListener("click", () => {
    log.querySelectorAll(".day31-entry").forEach((node) => node.remove());
    empty.hidden = false;
  });

  // ── сборка индекса ────────────────────────────────────────────────────

  rebuild.addEventListener("click", async () => {
    if (busy) return;
    setBusy(true);
    indexCard.classList.remove("is-ok", "is-bad");
    indexState.textContent = "собираем…";
    try {
      const response = await fetch("api/index", { method: "POST" });
      if (!response.ok) throw new Error((await readJson(response)).error || `HTTP ${response.status}`);
      for await (const event of ndjson(response)) {
        if (event.type === "corpus") indexState.textContent = `${event.documents} файлов…`;
        else if (event.type === "chunked") indexState.textContent = `${event.chunks} кусков, считаем векторы…`;
        else if (event.type === "embedding") indexState.textContent = `новых векторов: ${event.done} из ${event.total}`;
        else if (event.type === "built") indexState.textContent = `${event.chunks} кусков · новых векторов ${event.embedded}, из кэша ${event.cached} · ${number(event.ms / 1000)} с`;
        else if (event.type === "state") {
          ready = event.ready;
          indexCard.classList.add(event.ready ? "is-ok" : "is-bad");
          if (!event.ready) indexState.textContent = event.reason;
          else {
            indexState.textContent += ` · ${event.documents} файлов, ${event.code} модулей кода, ${event.pages} страниц`;
            const docs = lab.querySelector("[data-index-docs]");
            if (docs) docs.textContent = event.docs.join(" · ");
            rebuild.textContent = "Пересобрать индекс";
          }
        } else if (event.type === "error") throw new Error(event.error);
      }
    } catch (error) {
      indexCard.classList.add("is-bad");
      indexState.textContent = error.message || "Не собрался";
    }
    setBusy(false);
  });
})();
