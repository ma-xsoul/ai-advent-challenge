/* День 30: пульт приватного сервиса — ключи, очередь и журнал, проверка нагрузки. */
(() => {
  const lab = document.querySelector(".day30-lab");
  if (!lab) return;

  const initial = JSON.parse(document.querySelector("#day30-initial")?.textContent || "{}");
  const $ = (selector) => lab.querySelector(selector);
  const serviceItem = $('[data-chain="service"]');
  const serviceState = $("[data-service-state]");
  const publicItem = $('[data-chain="public"]');
  const publicUrl = $("[data-public-url]");
  const publicState = $("[data-public-state]");
  const curl = $("[data-curl]");
  const keysBody = $("[data-keys]");
  const keysEmpty = $("[data-keys-empty]");
  const keyForm = $("[data-key-form]");
  const keyError = $("[data-key-error]");
  const queue = $("[data-queue]");
  const counts = $("[data-counts]");
  const events = $("[data-events]");
  const eventsEmpty = $("[data-events-empty]");
  const loadButton = $("[data-load]");
  const loadStatus = $("[data-load-status]");
  const loadRows = $("[data-load-rows]");
  const loadSummary = $("[data-load-summary]");
  const publicTarget = lab.querySelector('[name="target"][value="public"]');

  const number = (value, digits = 1) => Number(value).toLocaleString("ru-RU", { maximumFractionDigits: digits });
  const escape = (text) => String(text ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const readJson = async (response) => { try { return await response.json(); } catch { return {}; } };
  const origin = (o) => (o ? `${o.device} · ${o.address} · ${o.via}` : "");

  let lastSeq = 0;
  let running = false;
  let loading = false;

  // ── статус сервиса ────────────────────────────────────────────────────

  const setChain = (item, ok) => {
    item.classList.toggle("is-ok", ok);
    item.classList.toggle("is-bad", !ok);
  };

  const renderCurl = (base) => {
    curl.textContent = `curl -X POST ${base}/api/chat \\\n  -H "Authorization: Bearer $KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"message": "Привет! Кто ты?", "stream": false}'`;
  };

  const eventText = (event) => {
    switch (event.kind) {
      case "queued": return event.position > 0 ? `встал в очередь · перед ним ${event.position}` : "сразу к модели";
      case "start": return event.waited_s > 0 ? `модель отвечает · ждал ${number(event.waited_s)} с` : "модель отвечает";
      case "done": return `ответ за ${number(event.answer_s)} с · ${event.tokens ?? 0} ток.${event.truncated ? " · обрезан лимитом" : ""}`;
      case "rejected": return `отказ ${event.status} · ${event.reason}${event.retry_after ? ` · через ${event.retry_after} с` : ""}`;
      case "error": return `ошибка · ${event.reason}`;
      case "cancelled": return "клиент ушёл, место в очереди освобождено";
      default: return event.kind;
    }
  };

  const addEvents = (list) => {
    if (!list?.length) return;
    eventsEmpty.hidden = true;
    for (const event of list) {
      lastSeq = Math.max(lastSeq, event.seq);
      const row = document.createElement("tr");
      row.className = `day30-event day30-event--${event.kind}`;
      row.innerHTML = `<td>${escape(event.at)}</td><td>${escape(event.client)}</td><td>${escape(origin(event.origin))}</td><td>${escape(eventText(event))}</td>`;
      events.insertBefore(row, eventsEmpty.nextSibling);
    }
    [...events.querySelectorAll(".day30-event")].slice(15).forEach((row) => row.remove());
  };

  const renderQueue = (state) => {
    const { active, waiting, peak, size } = state.queue;
    const parts = [];
    parts.push(active
      ? `<p class="day30-queue__active"><b>отвечает</b> ${escape(active.client)} · ${escape(origin(active.origin))} · ${number(active.answering_s)} с</p>`
      : '<p class="day30-queue__active is-idle"><b>модель свободна</b></p>');
    parts.push(waiting.length
      ? `<ol class="day30-queue__line">${waiting.map((item) => `<li>${escape(item.client)} · ${escape(item.origin.device)} · ждёт ${number(item.waiting_s)} с</li>`).join("")}</ol>`
      : `<p class="day30-muted">в очереди никого · максимум было ${peak} из ${size + 1}</p>`);
    queue.innerHTML = parts.join("");
    const c = state.counts || {};
    const rejected = ["401", "413", "429", "503"].map((code) => (c[`rejected_${code}`] ? `${code} × ${c[`rejected_${code}`]}` : null)).filter(Boolean);
    counts.textContent = `ответов ${c.done || 0} · отказов ${rejected.length ? rejected.join(", ") : 0}${c.error ? ` · ошибок ${c.error}` : ""}`;
  };

  const renderKeysUsage = (clients, limit) => {
    const used = Object.fromEntries((clients || []).map((client) => [client.name, client.used]));
    keysBody.querySelectorAll("[data-key-row]").forEach((row) => {
      const cell = row.querySelector("[data-key-used]");
      cell.textContent = running ? `${used[row.dataset.keyRow] ?? 0} / ${limit}` : "—";
    });
  };

  let refreshing = false;
  const refresh = async () => {
    if (refreshing) return;
    refreshing = true;
    try {
      await refreshNow();
    } finally {
      refreshing = false;
    }
  };

  const refreshNow = async () => {
    const response = await fetch(`api/status?after=${lastSeq}`).catch(() => null);
    const state = response?.ok ? await readJson(response) : { running: false };
    running = Boolean(state.running);
    setChain(serviceItem, running);
    serviceState.textContent = running ? `работает с ${state.started_at.slice(11, 16)} · ${state.model}` : "не запущен — .venv/bin/python service.py";
    const url = running ? state.public_url : null;
    setChain(publicItem, Boolean(url));
    publicUrl.innerHTML = url ? `<a href="${escape(url)}" target="_blank" rel="noopener">${escape(url.replace("https://", ""))}</a>` : "туннель";
    publicState.textContent = url ? "доступ из интернета · откройте с телефона" : running ? "туннель не подключён — только localhost" : "сначала запустите сервис";
    publicTarget.disabled = !url;
    if (!url && publicTarget.checked) lab.querySelector('[name="target"][value="local"]').checked = true;
    renderCurl(url || initial.service_url);
    loadButton.disabled = loading || !running;
    if (!running) {
      queue.innerHTML = '<p class="day30-muted">Сервис не запущен</p>';
      counts.textContent = "—";
      renderKeysUsage([], 0);
      return;
    }
    for (const [name, value] of Object.entries(state.limits || {})) {
      lab.querySelectorAll(`[data-limit="${name}"]`).forEach((node) => { node.textContent = value; });
    }
    renderQueue(state);
    renderKeysUsage(state.clients, state.limits.rate_per_minute);
    addEvents(state.events);
  };

  // ── ключи ─────────────────────────────────────────────────────────────

  const keyRow = (item) => {
    const row = document.createElement("tr");
    row.dataset.keyRow = item.name;
    row.innerHTML = `<td>${escape(item.name)}</td><td><code>${escape(item.key)}</code></td><td data-key-used>—</td>`
      + `<td class="day30-actions"><button class="day30-link" type="button" data-copy="${escape(item.key)}">копировать</button> `
      + `<button class="day30-link day30-link--danger" type="button" data-revoke="${escape(item.name)}">отозвать</button></td>`;
    return row;
  };

  keyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    keyError.hidden = true;
    const field = keyForm.elements.name;
    const response = await fetch("api/keys", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: field.value }) }).catch(() => null);
    const data = response ? await readJson(response) : {};
    if (!response?.ok) {
      keyError.textContent = data.error || "Не удалось выдать ключ";
      keyError.hidden = false;
      return;
    }
    keysEmpty.hidden = true;
    keysBody.insertBefore(keyRow(data), keysEmpty);
    field.value = "";
    refresh();
  });

  keysBody.addEventListener("click", async (event) => {
    const copy = event.target.closest("[data-copy]");
    if (copy) {
      try {
        await navigator.clipboard.writeText(copy.dataset.copy);
        copy.textContent = "скопирован";
      } catch {
        copy.textContent = "не вышло";
      }
      window.setTimeout(() => { copy.textContent = "копировать"; }, 1500);
      return;
    }
    const revoke = event.target.closest("[data-revoke]");
    if (!revoke) return;
    const response = await fetch(`api/keys/${encodeURIComponent(revoke.dataset.revoke)}`, { method: "DELETE" }).catch(() => null);
    if (response?.ok) {
      revoke.closest("tr").remove();
      keysEmpty.hidden = Boolean(keysBody.querySelector("[data-key-row]"));
    }
  });

  // ── проверка нагрузки ─────────────────────────────────────────────────

  const resultText = (row) => {
    if (row.state === "ответ") return `<span class="is-ok">ответ</span> «${escape(row.answer)}» · ${number(row.answer_s)} с`;
    if (row.state === "отказ") return `<span class="is-bad">${row.status}</span> ${escape(row.error)}`;
    if (row.state === "ошибка") return `<span class="is-bad">ошибка</span> ${escape(row.error)}`;
    return `<span class="day30-muted">${escape(row.state)}…</span>`;
  };

  const queueText = (row) => {
    if (row.position === undefined || row.position === null) return "—";
    const where = row.position === 0 ? "сразу" : `перед ним ${row.position}`;
    return row.waited_s !== undefined ? `${where} · ждал ${number(row.waited_s)} с` : where;
  };

  const renderLoadRow = (row) => {
    let tr = loadRows.querySelector(`[data-load-row="${row.index}"]`);
    if (!tr) {
      tr = document.createElement("tr");
      tr.dataset.loadRow = row.index;
      loadRows.append(tr);
    }
    const message = row.chars > 200 ? `${escape(row.message)} <span class="day30-muted">(${number(row.chars, 0)} симв.)</span>` : escape(row.message);
    tr.className = `day30-load__row is-${row.state === "ответ" ? "ok" : row.state === "отказ" || row.state === "ошибка" ? "bad" : "wait"}`;
    tr.innerHTML = `<td>${row.index + 1}</td><td>${escape(row.client)}</td><td>${message}</td><td>${queueText(row)}</td><td>${resultText(row)}</td>`;
  };

  const renderSummary = (summary) => {
    const rejected = Object.entries(summary.rejected).map(([code, count]) => `${code} × ${count}`).join(", ") || "нет";
    loadSummary.innerHTML = `Отправлено <b>${summary.sent}</b> · ответов <b>${summary.answered}</b> · отказов: <b>${rejected}</b> · ошибок <b>${summary.errors}</b>`
      + ` · очередь до <b>${summary.max_position + 1}</b> · дольше всех ждал <b>${number(summary.max_wait_s)} с</b> · всё за <b>${number(summary.total_s)} с</b>`;
    loadSummary.hidden = false;
  };

  loadButton.addEventListener("click", async () => {
    if (loading) return;
    loading = true;
    loadButton.disabled = true;
    loadSummary.hidden = true;
    loadStatus.textContent = "отправляем…";
    const target = lab.querySelector('[name="target"]:checked').value;
    try {
      const response = await fetch("api/load", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ target }) });
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
          if (event.type === "load_start") {
            loadRows.innerHTML = "";
            event.rows.forEach(renderLoadRow);
            loadStatus.textContent = `цель: ${event.target}`;
          } else if (event.type === "load_row") {
            renderLoadRow(event);
          } else if (event.type === "load_summary") {
            renderSummary(event.summary);
          } else if (event.type === "error") {
            throw new Error(event.error);
          }
        }
      }
    } catch (error) {
      loadStatus.textContent = error.message || "Проверка не удалась";
    }
    loading = false;
    loadButton.disabled = !running;
    refresh();
  });

  renderCurl(initial.service_url);
  refresh();
  window.setInterval(() => { if (!document.hidden) refresh(); }, 1500);
})();
