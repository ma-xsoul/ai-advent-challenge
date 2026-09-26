/*
 * День 16: подключение MCP.
 * Одна кнопка — один проход: соединение, рукопожатие, tools/list. Сервер
 * отвечает сводкой, списком инструментов и журналом провода. Всё, что
 * пришло от MCP-сервера, — чужой текст, поэтому только textContent.
 */
(() => {
  const connectButton = document.querySelector("#day16-connect");
  if (!connectButton) return;

  const status = document.querySelector("#day16-status");
  const errorBox = document.querySelector("#day16-error");
  const resultBox = document.querySelector("#day16-result");
  const toolsBox = resultBox.querySelector("[data-tools]");
  const toolsMeta = resultBox.querySelector("[data-tools-meta]");
  const wireBox = resultBox.querySelector("[data-wire]");
  const wireMeta = resultBox.querySelector("[data-wire-meta]");
  const fallbackNote = resultBox.querySelector("[data-fallback]");
  const stderrBox = resultBox.querySelector("[data-stderr]");
  const stderrText = resultBox.querySelector("[data-stderr-text]");
  const instructionsBox = resultBox.querySelector("[data-instructions]");
  const instructionsText = resultBox.querySelector("[data-instructions-text]");
  const inspectUrl = connectButton.dataset.inspectUrl;
  const buttonLabel = connectButton.textContent;

  const KIND_TITLES = {
    request: "запрос",
    response: "ответ",
    error: "отказ",
    notification: "уведомление",
    transport: "транспорт",
  };

  let dotsTimer = null;

  connectButton.addEventListener("click", async () => {
    const choice = document.querySelector('input[name="day16-server"]:checked');
    if (!choice) return;

    setBusy(true);
    errorBox.hidden = true;

    try {
      const response = await fetch(inspectUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ server: choice.value }),
      });
      const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));

      if (!data.server) {
        showError(data.error || "Не удалось выполнить запрос");
        resultBox.hidden = true;
        return;
      }

      render(data);
      if (!data.ok) showError(`Соединение не установлено: ${data.error}`);
    } catch (error) {
      showError(`Сервер лаборатории недоступен: ${error.message}`);
    } finally {
      setBusy(false);
    }
  });

  function setBusy(busy) {
    connectButton.disabled = busy;
    connectButton.textContent = busy ? "[…] Подключаюсь" : buttonLabel;
    clearInterval(dotsTimer);
    if (!busy) {
      status.textContent = "";
      return;
    }
    let dots = 0;
    status.textContent = "рукопожатие";
    dotsTimer = setInterval(() => {
      dots = (dots + 1) % 4;
      status.textContent = `рукопожатие${".".repeat(dots)}`;
    }, 350);
  }

  function showError(text) {
    errorBox.textContent = text;
    errorBox.hidden = false;
  }

  /* ── Сводка ────────────────────────────────────────────────────────── */

  function render(data) {
    resultBox.hidden = false;

    const serverLine = [data.server_title || data.server_name, data.server_version].filter(Boolean).join(" ");
    setSummary("connection", data.ok ? "установлено" : "не установлено", data.ok ? "ok" : "error");
    setSummary("transport", `${data.server.transport} · ${data.server.target}`);
    setSummary("protocol", data.protocol_version || "—");
    setSummary("server", serverLine || "—");
    setSummary("capabilities", data.capabilities.length ? data.capabilities.join(" · ") : "—");
    setSummary("tools", data.ok ? `${data.tools.length}${data.pages > 1 ? ` (страниц: ${data.pages})` : ""}` : "—");
    setSummary("required", data.ok ? String(data.required_params) : "—");
    setSummary("tokens", data.ok ? `≈ ${data.schema_tokens} — в каждом запросе к модели` : "—", data.ok ? "accent" : "");
    setSummary("elapsed", `${(data.elapsed_ms / 1000).toFixed(2)} с`);

    instructionsBox.hidden = !data.instructions;
    instructionsText.textContent = data.instructions || "";

    renderTools(data.tools, data.schema_tokens);
    renderWire(data.wire);

    stderrBox.hidden = !data.stderr;
    stderrText.textContent = data.stderr || "";
  }

  function setSummary(key, text, tone = "") {
    const cell = resultBox.querySelector(`[data-summary="${key}"]`);
    cell.textContent = text;
    cell.dataset.tone = tone;
  }

  /* ── Инструменты ───────────────────────────────────────────────────── */

  function renderTools(tools, total) {
    toolsBox.replaceChildren();
    toolsMeta.textContent = tools.length ? `${tools.length} · ≈ ${total} ток.` : "";

    if (!tools.length) {
      toolsBox.append(el("li", "day16-tools__empty", "Список не получен."));
      return;
    }

    for (const tool of tools) {
      const item = el("li", "day16-tool");

      const head = el("div", "day16-tool__head");
      head.append(el("strong", "day16-tool__name", tool.name));
      if (tool.title && tool.title !== tool.name) head.append(el("span", "day16-tool__title", tool.title));
      head.append(el("span", "day16-tool__tokens", `≈ ${tool.tokens} ток.`));
      item.append(head);

      if (tool.description) item.append(el("p", "day16-tool__description", tool.description));

      const params = el("ul", "day16-params");
      if (!tool.params.length) params.append(el("li", "day16-param day16-param--none", "без параметров"));
      for (const param of tool.params) {
        const chip = el("li", `day16-param${param.required ? " day16-param--required" : ""}`);
        chip.append(el("span", "day16-param__name", param.name + (param.required ? "*" : "")));
        chip.append(el("span", "day16-param__type", param.type));
        if (param.description) chip.title = param.description;
        params.append(chip);
      }
      item.append(params);

      item.append(fold("схема входа (inputSchema)", tool.schema));
      toolsBox.append(item);
    }
  }

  /* ── Провод ────────────────────────────────────────────────────────── */

  function renderWire(wire) {
    wireBox.replaceChildren();
    const sent = wire.filter((message) => message.direction === "out").length;
    wireMeta.textContent = wire.length ? `→ ${sent} · ← ${wire.length - sent}` : "";
    fallbackNote.hidden = !wire.some((message) => message.kind === "error" && message.method === "server/discover");

    if (!wire.length) {
      wireBox.append(el("li", "day16-wire__empty", "По проводу ничего не ушло."));
      return;
    }

    for (const message of wire) {
      const item = el("li", `day16-message day16-message--${message.direction} day16-message--${message.kind}`);

      const head = el("div", "day16-message__head");
      head.append(el("span", "day16-message__arrow", message.direction === "out" ? "→" : "←"));
      head.append(el("span", "day16-message__kind", KIND_TITLES[message.kind] || message.kind));
      if (message.id !== null && message.id !== undefined) head.append(el("span", "day16-message__id", `#${message.id}`));
      head.append(el("span", "day16-message__at", `+${message.at_ms} мс`));
      item.append(head);

      item.append(el("p", "day16-message__summary", message.summary));
      item.append(fold("JSON", message.body));
      wireBox.append(item);
    }
  }

  /* ── Мелочи ────────────────────────────────────────────────────────── */

  function fold(title, value) {
    const details = el("details", "day16-fold");
    details.append(el("summary", "", title));
    details.append(el("pre", "", JSON.stringify(value, null, 2)));
    return details;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
})();
