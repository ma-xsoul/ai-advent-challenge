/* День 34: цель → ассистент сам работает с файлами → черновик и diff → применить; прогоны. */
(() => {
  const lab = document.querySelector(".day34-lab");
  if (!lab) return;

  const initial = JSON.parse(document.querySelector("#day34-initial")?.textContent || "{}");
  const $ = (selector) => lab.querySelector(selector);
  const form = $("[data-goal-form]");
  const field = form.querySelector("textarea");
  const submit = form.querySelector('[type="submit"]');
  const error = $("[data-error]");
  const work = $("[data-work]");
  const calls = $("[data-calls]");
  const traceMeta = $("[data-trace-meta]");
  const answer = $("[data-answer]");
  const changesBox = $("[data-changes]");
  const applyButton = $("[data-apply]");
  const discardButton = $("[data-discard]");
  const runsBox = $("[data-runs]");

  let busy = false;
  let runs = initial.runs || [];

  const escape = (text) => String(text ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const markdown = (text) => (typeof window.renderMarkdown === "function" ? window.renderMarkdown(text) : escape(text));
  const number = (value, digits = 1) => Number(value).toLocaleString("ru-RU", { maximumFractionDigits: digits });
  const readJson = async (response) => { try { return await response.json(); } catch { return {}; } };
  const ICON = { list_files: "список", search: "поиск", read_file: "чтение", write_file: "запись", edit_file: "правка", draft_diff: "черновик" };

  const showError = (text) => {
    error.textContent = text || "";
    error.hidden = !text;
  };

  const argsText = (name, args) => {
    if (!args || typeof args !== "object") return escape(String(args ?? ""));
    if (name === "read_file") return `${escape(args.path)}${args.start ? ` · строки ${args.start}–${args.end ?? "…"}` : ""}`;
    if (name === "search") return `«${escape(args.query)}»${args.path ? ` в ${escape(args.path)}` : ""}${args.glob ? ` · ${escape(args.glob)}` : ""}`;
    if (name === "write_file") return `${escape(args.path)} · ${number(String(args.content || "").split("\n").length, 0)} строк`;
    if (name === "edit_file") return `${escape(args.path)} · замена фрагмента`;
    return Object.entries(args).filter(([, v]) => v !== "" && v !== undefined).map(([k, v]) => `${escape(k)}=${escape(JSON.stringify(v))}`).join(", ");
  };

  // ── diff ──────────────────────────────────────────────────────────────

  const diffHtml = (text) => text.split("\n").map((row) => {
    const kind = row.startsWith("+") && !row.startsWith("+++") ? "add" : row.startsWith("-") && !row.startsWith("---") ? "del" : row.startsWith("@@") ? "hunk" : "";
    return `<span class="day34-diff${kind ? ` day34-diff--${kind}` : ""}">${escape(row)}</span>`;
  }).join("\n");

  const renderChanges = (files) => {
    applyButton.disabled = !files.length;
    discardButton.disabled = !files.length;
    if (!files.length) {
      changesBox.innerHTML = '<p class="day34-muted">Черновик пуст. Изменения появятся, если цель просит создать или поправить файл.</p>';
      return;
    }
    changesBox.innerHTML = files.map((file) => `<details class="day34-file" open><summary><b>${escape(file.path)}</b> ${file.new ? '<em class="day34-new">новый файл</em>' : ""}`
      + ` <span class="day34-add">+${file.added}</span> <span class="day34-del">−${file.removed}</span></summary><pre class="day34-pre">${diffHtml(file.diff)}</pre></details>`).join("");
  };

  // ── прогоны ───────────────────────────────────────────────────────────

  const renderRuns = () => {
    runsBox.innerHTML = runs.length ? runs.map((run) => {
      const changed = (run.changes || []).map((item) => `${escape(item.path)} +${item.added} −${item.removed}`).join(", ") || "без изменений";
      const stats = run.stats || {};
      return `<li><p class="day34-run__goal">${escape(run.goal)}</p>`
        + `<p class="day34-muted">${escape(String(run.started_at).slice(0, 16).replace("T", " "))} · инструментов ${stats.tool_calls ?? "?"} · прочитано файлов ${(run.files_read || []).length} · ${changed} · ${number((stats.ms || 0) / 1000)} с`
        + ` <button class="day34-link" type="button" data-repeat="${escape(run.goal)}">повторить</button> <button class="day34-link" type="button" data-show="${escape(run.id)}">открыть</button></p></li>`;
    }).join("") : '<li class="day34-muted">Прогонов пока нет.</li>';
  };

  runsBox.addEventListener("click", async (event) => {
    const repeat = event.target.closest("[data-repeat]");
    if (repeat && !busy) {
      field.value = repeat.dataset.repeat;
      run(field.value);
      return;
    }
    const show = event.target.closest("[data-show]");
    if (!show) return;
    const response = await fetch(`api/runs/${encodeURIComponent(show.dataset.show)}`).catch(() => null);
    const data = response?.ok ? await readJson(response) : null;
    if (!data) return;
    work.hidden = false;
    field.value = data.goal;
    calls.innerHTML = data.calls.map((call) => `<li class="${call.ok ? "" : "is-bad"}"><span>${escape(ICON[call.name] || call.name)}</span> ${argsText(call.name, call.arguments)}</li>`).join("");
    traceMeta.textContent = `· сохранённый прогон ${String(data.started_at).slice(0, 16).replace("T", " ")} · вызовов ${data.calls.length}`;
    answer.innerHTML = markdown(data.answer || "—");
    work.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  // ── выполнение цели ───────────────────────────────────────────────────

  const run = async (goal) => {
    busy = true;
    submit.disabled = true;
    showError("");
    work.hidden = false;
    calls.innerHTML = "";
    traceMeta.textContent = "";
    answer.innerHTML = '<p class="day34-muted">ассистент работает…</p>';
    const started = performance.now();
    let count = 0;
    try {
      const response = await fetch("api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ goal }) });
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
          const event = JSON.parse(line);
          if (event.type === "start") {
            if (event.discarded) traceMeta.textContent = `· черновик очищен (${event.discarded})`;
            renderChanges([]);
          } else if (event.type === "tool_call") {
            count += 1;
            calls.insertAdjacentHTML("beforeend", `<li data-call="${escape(event.id)}"><span>${escape(ICON[event.name] || event.name)}</span> ${argsText(event.name, event.arguments)} <em>…</em></li>`);
            traceMeta.textContent = `· вызовов ${count} · ${number((performance.now() - started) / 1000)} с`;
          } else if (event.type === "tool_result") {
            const item = calls.querySelector(`[data-call="${CSS.escape(event.id)}"]`);
            if (item) {
              item.classList.toggle("is-bad", !event.ok);
              item.querySelector("em").textContent = event.ok ? `${number(event.chars, 0)} символов` : "ошибка";
            }
          } else if (event.type === "answer") {
            answer.innerHTML = markdown(event.content);
          } else if (event.type === "changes") {
            renderChanges(event.files);
          } else if (event.type === "run") {
            runs = [event.run, ...runs.filter((item) => item.id !== event.run.id)].slice(0, 8);
            renderRuns();
            const stats = event.run.stats;
            traceMeta.textContent = `· вызовов ${stats.tool_calls} · файлов прочитано ${event.run.files_read.length} · запросов к модели ${stats.llm_calls} · ${number(stats.ms / 1000)} с`;
          } else if (event.type === "error") {
            throw new Error(event.error);
          }
        }
      }
    } catch (caught) {
      showError(caught.message || "Не получилось");
    }
    busy = false;
    submit.disabled = false;
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const goal = field.value.trim();
    if (goal && !busy) run(goal);
  });

  lab.querySelectorAll("[data-goal]").forEach((button) => button.addEventListener("click", () => {
    field.value = button.dataset.goal;
    field.focus();
  }));

  applyButton.addEventListener("click", async () => {
    applyButton.disabled = true;
    const response = await fetch("api/changes/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => null);
    const data = response ? await readJson(response) : {};
    if (!response?.ok) {
      showError(data.error || "Не применилось");
      applyButton.disabled = false;
      return;
    }
    renderChanges(data.files);
    changesBox.insertAdjacentHTML("afterbegin", `<p class="day34-applied">Применено в проект: ${data.applied.map((path) => `<code>${escape(path)}</code>`).join(", ")}</p>`);
  });

  discardButton.addEventListener("click", async () => {
    await fetch("api/changes/discard", { method: "POST" }).catch(() => null);
    renderChanges([]);
  });

  renderChanges(initial.changes || []);
  renderRuns();
})();
