/*
 * День 26: локальная LLM.
 * Запрос: start → thinking/delta (кусочки) → done (замеры Ollama и что в
 * памяти). Каждый ответ добавляет строку в «Замеры» — так видно, как
 * отличаются модели и как первый запрос платит за прогрев.
 * «Прогнать все три» задаёт вопросы по очереди: модель одна, памяти мало.
 * Тексты запросов и размышлений — через textContent; ответ модели —
 * через общий renderMarkdown, он экранирует HTML.
 */
(() => {
  const controls = document.querySelector("[data-controls]");
  if (!controls) return;

  const modelSelect = controls.querySelector("[data-model]");
  const modelInput = modelSelect.querySelector('input[type="hidden"]');
  window.CustomSelect.init(modelSelect);
  const thinkBox = controls.querySelector("[data-think]");
  const runAll = controls.querySelector("[data-run-all]");
  const errorBox = document.querySelector("#day26-error");
  const runs = document.querySelector("[data-runs] tbody");
  const loadedLine = document.querySelector("[data-loaded]");
  const GB = 1000 ** 3;

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

  const sec = (value) => (value === null || value === undefined ? "—" : `${String(value).replace(".", ",")} с`);
  const gb = (bytes) => `${(bytes / GB).toFixed(1).replace(".", ",")} ГБ`;

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

  function renderLoaded(loaded) {
    if (!loadedLine) return;
    loadedLine.replaceChildren();
    if (!loaded.length) {
      loadedLine.textContent = "В памяти сейчас ничего: первая модель прогреется при первом запросе.";
      return;
    }
    loadedLine.append("В памяти сейчас: ");
    loaded.forEach((item, index) => {
      loadedLine.append(el("code", "", item.name));
      const where = item.vram < item.size ? `на видеочипе ${gb(item.vram)} — остальное на процессоре` : "всё на видеочипе";
      loadedLine.append(` — ${gb(item.size)}, ${where}, контекст ${item.context}${index < loaded.length - 1 ? "; " : "."}`);
    });
  }

  function addRun(model, title, metrics) {
    runs.querySelector(".day26-runs__empty")?.remove();
    const tr = el("tr", "is-new");
    for (const text of [model, title, sec(metrics.load_s), sec(metrics.first_token_s), metrics.output_tps ? `${String(metrics.output_tps).replace(".", ",")} ток/с` : "—", `${metrics.prompt_tokens} → ${metrics.output_tokens}`, sec(metrics.total_s)]) {
      tr.append(el("td", "", text));
    }
    tr.firstChild.replaceChildren(el("code", "", model));
    runs.append(tr);
  }

  let busy = false;
  const setBusy = (value) => {
    busy = value;
    runAll.disabled = value;
    window.CustomSelect.setDisabled(modelSelect, value);
    document.querySelectorAll("[data-ask]").forEach((button) => (button.disabled = value));
  };

  async function ask(card) {
    const model = modelInput.value;
    const prompt = card.querySelector("[data-prompt]").textContent.trim();
    const title = card.querySelector("h4").textContent;
    const answer = card.querySelector("[data-answer]");
    const thinking = card.querySelector("[data-thinking]");
    const thinkingText = card.querySelector("[data-thinking-text]");
    const metricsLine = card.querySelector("[data-metrics]");
    card.classList.add("is-running");
    answer.replaceChildren(el("p", "day26-muted", "модель загружается в память и думает…"));
    thinking.hidden = true;
    thinkingText.textContent = "";
    metricsLine.textContent = "";
    let text = "";
    let pending = null;
    const flush = () => {
      pending = null;
      markdown(answer, text);
    };
    try {
      const response = await fetch(controls.dataset.askUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model, prompt, think: thinkBox.checked }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      for await (const event of streamEvents(response.body)) {
        if (event.type === "thinking") {
          thinking.hidden = false;
          thinkingText.textContent += event.text;
        } else if (event.type === "delta") {
          text += event.text;
          pending ??= requestAnimationFrame(flush);
        } else if (event.type === "done") {
          if (pending) {
            cancelAnimationFrame(pending);
            flush();
          }
          metricsLine.textContent =
            `${model} · прогрев ${sec(event.load_s)} · первый токен ${sec(event.first_token_s)} · ` +
            `${event.output_tps ? String(event.output_tps).replace(".", ",") + " ток/с" : "—"} · ` +
            `${event.prompt_tokens} → ${event.output_tokens} токенов · всего ${sec(event.total_s)}`;
          addRun(model, title, event);
          renderLoaded(event.loaded || []);
        } else if (event.type === "error") throw new Error(event.error);
      }
    } catch (error) {
      answer.replaceChildren(el("p", "day26-error", error.message));
    } finally {
      card.classList.remove("is-running");
    }
  }

  document.querySelectorAll("[data-ask]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (busy) return;
      showError("");
      setBusy(true);
      await ask(button.closest("[data-preset]"));
      setBusy(false);
    });
  });

  runAll.addEventListener("click", async () => {
    if (busy) return;
    showError("");
    setBusy(true);
    for (const card of document.querySelectorAll("[data-preset]")) await ask(card);
    setBusy(false);
  });

  modelInput.addEventListener("change", () => {
    document.querySelectorAll("[data-cli-model]").forEach((node) => (node.textContent = modelInput.value));
  });
})();
