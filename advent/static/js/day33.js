/* День 33: мини-сервис поддержки — тикеты, карточка клиента, чат с ассистентом, ответ в тикет. */
(() => {
  const lab = document.querySelector(".day33-lab");
  if (!lab) return;

  const initial = JSON.parse(document.querySelector("#day33-initial")?.textContent || "{}");
  const $ = (selector) => lab.querySelector(selector);
  const ticketsBox = $("[data-tickets]");
  const subject = $("[data-subject]");
  const meta = $("[data-ticket-meta]");
  const clientBox = $("[data-client]");
  const eventsBox = $("[data-events]");
  const eventsSummary = $("[data-events-summary]");
  const thread = $("[data-thread]");
  const chat = $("[data-chat]");
  const form = $("[data-chat-form]");
  const field = form.querySelector("textarea");
  const submit = form.querySelector("button");
  const error = $("[data-error]");

  let current = initial.first;
  let busy = false;

  const escape = (text) => String(text ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const markdown = (text) => (typeof window.renderMarkdown === "function" ? window.renderMarkdown(text) : escape(text));
  const number = (value, digits = 1) => Number(value).toLocaleString("ru-RU", { maximumFractionDigits: digits });
  const readJson = async (response) => { try { return await response.json(); } catch { return {}; } };
  const KEY_STATUS = { active: "активен", revoked: "отозван", expired: "истёк" };
  const FROM = { client: "клиент", support: "поддержка" };

  const showError = (text) => {
    error.textContent = text;
    error.hidden = !text;
  };

  // ── карточка тикета ───────────────────────────────────────────────────

  const toolLine = (name, args, ok = true) => {
    const shown = args && typeof args === "object" ? Object.entries(args).map(([k, v]) => `${escape(k)}=${escape(JSON.stringify(v))}`).join(", ") : "";
    return `<div class="day33-tool${ok ? "" : " is-bad"}"><span>MCP · CRM</span> ${escape(name)}(${shown})</div>`;
  };

  const renderChat = (items) => {
    chat.innerHTML = items.length ? "" : '<p class="day33-muted">Переписки с ассистентом по этому тикету ещё нет — отправьте сообщение клиента.</p>';
    items.forEach((item) => {
      if (item.role === "client") chat.insertAdjacentHTML("beforeend", `<div class="day33-msg day33-msg--client">${escape(item.text)}</div>`);
      else if (item.role === "tool") chat.insertAdjacentHTML("beforeend", toolLine(item.name, item.arguments, item.ok));
      else addAnswer(item.text);
    });
  };

  const renderCase = (data, { withChat = true } = {}) => {
    const { ticket, client, events } = data;
    subject.textContent = `${ticket.id} · ${ticket.subject}`;
    meta.innerHTML = `<em class="day33-status day33-status--${escape(ticket.status)}">${escape(ticket.status)}</em> · создан ${escape(ticket.created_at)}`;
    const key = client.key;
    clientBox.innerHTML = `<dl class="day33-kv">`
      + `<dt>клиент</dt><dd><b>${escape(client.name)}</b> · ${escape(client.email)} · с ${escape(client.since)}</dd>`
      + `<dt>тариф</dt><dd>${escape(client.plan)} · ${client.rate_per_minute} запросов в минуту</dd>`
      + `<dt>ключ</dt><dd class="day33-key day33-key--${escape(key.status)}">${escape(key.name)} · ${escape(KEY_STATUS[key.status] || key.status)} · ${escape(key.changed_at)}${key.reason ? ` · ${escape(key.reason)}` : ""}</dd></dl>`;
    const counts = {};
    events.forEach((event) => { counts[event.status] = (counts[event.status] || 0) + 1; });
    eventsSummary.textContent = `· ${Object.entries(counts).map(([code, count]) => `${code} × ${count}`).join(", ")}`;
    eventsBox.innerHTML = events.map((event) => `<li class="${event.status === 200 ? "" : "is-bad"}"><span>${escape(event.at)}</span> <b>${event.status}</b> ${escape(event.detail)}</li>`).join("");
    thread.innerHTML = ticket.messages.map((item) => `<li class="day33-thread__${escape(item.from)}"><span>${escape(FROM[item.from] || item.from)} · ${escape(item.at)}</span>${escape(item.text)}</li>`).join("");
    if (!withChat) return;
    renderChat(data.chat);
    const firstClient = ticket.messages.find((item) => item.from === "client");
    field.value = data.chat.length ? "" : (firstClient?.text || "");
  };

  const open = async (id) => {
    current = id;
    showError("");
    ticketsBox.querySelectorAll("[data-ticket]").forEach((button) => button.classList.toggle("is-active", button.dataset.ticket === id));
    const response = await fetch(`api/tickets/${encodeURIComponent(id)}`).catch(() => null);
    const data = response ? await readJson(response) : {};
    if (!response?.ok) {
      showError(data.error || "Тикет не загрузился");
      return;
    }
    renderCase(data);
  };

  const refreshTickets = async () => {
    const response = await fetch("api/tickets").catch(() => null);
    if (!response?.ok) return;
    const list = await readJson(response);
    list.forEach((item) => {
      const badge = ticketsBox.querySelector(`[data-ticket="${item.id}"] .day33-status`);
      if (badge) {
        badge.className = `day33-status day33-status--${item.status}`;
        badge.textContent = item.status;
      }
    });
  };

  // ── чат ───────────────────────────────────────────────────────────────

  function addAnswer(text, extra = "") {
    const block = document.createElement("div");
    block.className = "day33-msg day33-msg--assistant";
    block.innerHTML = `<div class="markdown-body">${markdown(text)}</div>${extra}`
      + `<div class="day33-msg__actions"><button class="day33-btn" type="button" data-send>Отправить клиенту</button></div>`;
    block.querySelector("[data-send]").addEventListener("click", (event) => sendToClient(text, event.currentTarget));
    chat.append(block);
    return block;
  }

  const sendToClient = async (text, button) => {
    button.disabled = true;
    const response = await fetch(`api/tickets/${encodeURIComponent(current)}/reply`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, status: "answered" }),
    }).catch(() => null);
    const data = response ? await readJson(response) : {};
    if (!response?.ok) {
      button.disabled = false;
      showError(data.error || "CRM не ответила");
      return;
    }
    button.textContent = `отправлено через MCP · reply_to_ticket · ${data.ms} мс`;
    renderCase(data, { withChat: false });
    refreshTickets();
  };

  const ask = async (text) => {
    busy = true;
    submit.disabled = true;
    showError("");
    chat.querySelector(".day33-muted")?.remove();
    chat.insertAdjacentHTML("beforeend", `<div class="day33-msg day33-msg--client">${escape(text)}</div>`);
    const status = document.createElement("p");
    status.className = "day33-muted";
    status.textContent = "ищем в FAQ…";
    chat.append(status);
    let hits = [];
    const started = performance.now();
    try {
      const response = await fetch(`api/tickets/${encodeURIComponent(current)}/chat`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text }),
      });
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
          if (event.type === "retrieved") {
            hits = event.hits;
            status.textContent = "ассистент смотрит в CRM…";
          } else if (event.type === "tool_call") {
            status.insertAdjacentHTML("beforebegin", toolLine(event.name, event.arguments));
          } else if (event.type === "answer") {
            const sources = hits.map((hit) => `<li class="${(event.cited || []).includes(hit.n) ? "is-cited" : ""}"><b>[${hit.n}]</b> ${escape(hit.section)}</li>`).join("");
            status.remove();
            addAnswer(event.content, `<details class="day33-fold day33-sources"><summary>FAQ · найдено ${hits.length}${event.cited?.length ? ` · в ответе ${event.cited.map((n) => `[${n}]`).join(" ")}` : ""}</summary><ol>${sources}</ol></details>`);
          } else if (event.type === "done") {
            const stats = event.stats || {};
            chat.insertAdjacentHTML("beforeend", `<p class="day33-muted">CRM: ${stats.tool_calls} ${stats.tool_calls === 1 ? "вызов" : "вызова"} · запросов к модели ${stats.llm_calls} · ${number((performance.now() - started) / 1000)} с</p>`);
          } else if (event.type === "error") {
            throw new Error(event.error);
          }
        }
      }
    } catch (caught) {
      status.remove();
      showError(caught.message || "Ассистент не ответил");
    }
    busy = false;
    submit.disabled = false;
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = field.value.trim();
    if (!text || busy || !current) return;
    field.value = "";
    ask(text);
  });

  ticketsBox.addEventListener("click", (event) => {
    const button = event.target.closest("[data-ticket]");
    if (button && !busy) open(button.dataset.ticket);
  });

  $("[data-chat-reset]").addEventListener("click", async () => {
    if (busy || !current) return;
    await fetch(`api/tickets/${encodeURIComponent(current)}/reset`, { method: "POST" }).catch(() => null);
    open(current);
  });

  $("[data-crm-reset]").addEventListener("click", async () => {
    if (busy) return;
    await fetch("api/crm/reset", { method: "POST" }).catch(() => null);
    await refreshTickets();
    if (current) open(current);
  });

  if (current) open(current);
})();
