/* День 35: конспект курса по дням, вопрос по источникам, тренажёр с разбором ответа. */
(() => {
  const lab = document.querySelector(".day35-lab");
  if (!lab) return;

  const initial = JSON.parse(document.querySelector("#day35-initial")?.textContent || "{}");
  const $ = (selector) => lab.querySelector(selector);
  const daysBox = $("[data-days]");
  const chapterBox = $("[data-chapter]");
  const buildButton = $("[data-build]");
  const buildStatus = $("[data-build-status]");
  const scope = $("[data-scope]");
  const askForm = $("[data-ask-form]");
  const askField = askForm.querySelector("textarea");
  const answerBox = $("[data-answer]");
  const quizQuestion = $("[data-quiz-question]");
  const quizForm = $("[data-quiz-form]");
  const quizField = quizForm.querySelector("textarea");
  const verdictBox = $("[data-verdict]");
  const error = $("[data-error]");
  const kinds = initial.kinds || {};

  let selected = initial.days?.[0]?.day || null;
  let current = null;

  const escape = (text) => String(text ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const markdown = (text) => (typeof window.renderMarkdown === "function" ? window.renderMarkdown(text) : escape(text));
  const readJson = async (response) => { try { return await response.json(); } catch { return {}; } };
  const VERDICT = { correct: "верно", partial: "частично", wrong: "мимо" };
  const list = (items) => (items?.length ? `<ul>${items.map((item) => `<li>${escape(item)}</li>`).join("")}</ul>` : '<p class="day35-muted">—</p>');

  const showError = (text) => {
    error.textContent = text || "";
    error.hidden = !text;
  };

  const post = async (url, body) => {
    const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await readJson(response);
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  };

  // ── глава ─────────────────────────────────────────────────────────────

  const openDay = async (day) => {
    selected = day;
    daysBox.querySelectorAll("[data-day]").forEach((button) => button.classList.toggle("is-active", Number(button.dataset.day) === day));
    const response = await fetch(`api/days/${day}`).catch(() => null);
    const data = response ? await readJson(response) : {};
    if (!response?.ok) {
      chapterBox.innerHTML = `<p class="day35-muted">${escape(data.error || "Глава не загрузилась")}</p>`;
      return;
    }
    const sources = Object.entries(data.sources || {}).filter(([, count]) => count).map(([kind, count]) => `${escape(kinds[kind] || kind)} · ${count}`).join(" · ");
    chapterBox.innerHTML = `<p class="day35-kicker">День ${day}</p><h4>${escape(data.title)}</h4>`
      + `<p class="day35-summary">${escape(data.summary)}</p>`
      + `<div class="day35-cols"><section><p class="day35-label">Главные идеи</p>${list(data.ideas)}</section>`
      + `<section><p class="day35-label">Что построено</p>${list(data.built)}</section></div>`
      + (data.curator?.length ? `<section><p class="day35-label">Что уточнял куратор</p>${list(data.curator)}</section>` : "")
      + `<p class="day35-muted">Источники главы: ${sources}</p>`
      + `<p class="day35-chapter__actions"><button class="day35-btn" type="button" data-ask-day>Спросить про этот день</button> <button class="day35-btn" type="button" data-train-day>Тренироваться по дню</button></p>`;
    chapterBox.querySelector("[data-ask-day]").addEventListener("click", () => { scope.value = String(day); askField.focus(); });
    chapterBox.querySelector("[data-train-day]").addEventListener("click", () => nextQuestion(day));
  };

  daysBox.addEventListener("click", (event) => {
    const button = event.target.closest("[data-day]");
    if (button) openDay(Number(button.dataset.day));
  });

  // ── сборка ────────────────────────────────────────────────────────────

  buildButton.addEventListener("click", async () => {
    buildButton.disabled = true;
    buildStatus.hidden = false;
    buildStatus.textContent = "собираем источники…";
    let written = 0;
    try {
      const response = await fetch("api/build", { method: "POST" });
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
          if (event.type === "sources") buildStatus.textContent = `источников ${event.total}: ${Object.entries(event.kinds).map(([kind, count]) => `${kinds[kind] || kind} ${count}`).join(", ")} · индекс…`;
          else if (event.type === "index") buildStatus.textContent += ` ${event.chunks} кусков · главы…`;
          else if (event.type === "chapter") { written += 1; buildStatus.textContent = buildStatus.textContent.replace(/ · глав \d+.*$/, "") + ` · глав ${written}${event.error ? " (есть ошибки)" : ""}`; }
          else if (event.type === "built") buildStatus.textContent = `готово: ${event.days} глав, новых ${event.fresh}, из кэша ${event.days - event.fresh} · ${(event.ms / 1000).toFixed(1)} с — обновите страницу`;
          else if (event.type === "error") throw new Error(event.error);
        }
      }
      window.setTimeout(() => window.location.reload(), 1200);
    } catch (caught) {
      buildStatus.textContent = caught.message || "Не собралось";
    }
    buildButton.disabled = false;
  });

  // ── вопрос ────────────────────────────────────────────────────────────

  askForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = askField.value.trim();
    if (!question) return;
    showError("");
    answerBox.hidden = false;
    answerBox.innerHTML = '<p class="day35-muted">ищем в источниках…</p>';
    try {
      const data = await post("api/ask", { question, day: scope.value || null });
      const cited = new Set(data.cited || []);
      answerBox.innerHTML = `<div class="markdown-body">${markdown(data.answer)}</div>`
        + `<ol class="day35-sources">${data.sources.map((source) => `<li class="${cited.has(source.n) ? "is-cited" : ""}"><b>[${source.n}]</b> день ${source.day} · ${escape(kinds[source.kind] || source.kind)} · ${escape(source.ref)}</li>`).join("")}</ol>`;
    } catch (caught) {
      answerBox.innerHTML = "";
      showError(caught.message);
    }
  });

  // ── тренажёр ──────────────────────────────────────────────────────────

  const nextQuestion = async (day) => {
    showError("");
    verdictBox.hidden = true;
    const response = await fetch(`api/quiz${day ? `?day=${day}` : ""}`).catch(() => null);
    const data = response ? await readJson(response) : {};
    if (!response?.ok) {
      showError(data.error || "Вопрос не загрузился");
      return;
    }
    current = data;
    quizQuestion.innerHTML = `<span class="day35-kicker">День ${data.day} · ${escape(data.title)}</span>${escape(data.q)}`;
    quizQuestion.classList.remove("day35-muted");
    quizForm.hidden = false;
    quizField.value = "";
    quizField.focus();
  };

  $("[data-quiz-any]").addEventListener("click", () => nextQuestion(null));
  $("[data-quiz-day]").addEventListener("click", () => nextQuestion(selected));

  quizForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const answer = quizField.value.trim();
    if (!answer || !current) return;
    const button = quizForm.querySelector("button");
    button.disabled = true;
    verdictBox.hidden = false;
    verdictBox.innerHTML = '<p class="day35-muted">проверяем…</p>';
    try {
      const data = await post("api/quiz", { day: current.day, index: current.index, answer });
      verdictBox.innerHTML = `<p class="day35-badge day35-badge--${escape(data.verdict)}">${escape(VERDICT[data.verdict])}</p>`
        + `<p>${escape(data.feedback)}</p>${data.hint ? `<p class="day35-muted">${escape(data.hint)}</p>` : ""}`
        + `<details><summary>эталон</summary><p>${escape(data.reference)}</p></details>`
        + `<p class="day35-chapter__actions"><button class="day35-btn" type="button" data-next>Следующий вопрос</button></p>`;
      verdictBox.querySelector("[data-next]").addEventListener("click", () => nextQuestion(null));
      const score = lab.querySelector(`[data-score="${current.day}"]`);
      if (score && data.progress) score.textContent = `${data.progress.correct}/${data.progress.answers}`;
    } catch (caught) {
      verdictBox.innerHTML = "";
      showError(caught.message);
    }
    button.disabled = false;
  });

  if (selected) openDay(selected);
})();
