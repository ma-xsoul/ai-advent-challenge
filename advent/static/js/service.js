/* Чат приватного сервиса дня 30: ключ, диалог, место в очереди, поток ответа. */
(() => {
  const root = document.querySelector(".svc");
  if (!root) return;

  const limits = JSON.parse(root.dataset.limits || "{}");
  const keyForm = root.querySelector("[data-key-form]");
  const keyField = keyForm.querySelector("input");
  const who = root.querySelector("[data-who]");
  const whoName = root.querySelector("[data-who-name]");
  const whoUsed = root.querySelector("[data-who-used]");
  const log = root.querySelector("[data-log]");
  const empty = root.querySelector("[data-empty]");
  const status = root.querySelector("[data-status]");
  const health = root.querySelector("[data-health]");
  const form = root.querySelector("[data-chat-form]");
  const field = form.querySelector("textarea");
  const send = root.querySelector("[data-send]");
  const count = root.querySelector("[data-count]");

  const KEY = "day30-service-key";
  const HISTORY = "day30-service-history";
  const store = {
    get(name) { try { return window.localStorage.getItem(name); } catch { return null; } },
    set(name, value) { try { window.localStorage.setItem(name, value); } catch { /* приватный режим */ } },
    drop(name) { try { window.localStorage.removeItem(name); } catch { /* приватный режим */ } },
  };

  let key = store.get(KEY) || "";
  let history = [];
  let busy = false;
  try { history = JSON.parse(store.get(HISTORY) || "[]").filter((m) => m && m.role && m.content); } catch { history = []; }

  const number = (value, digits = 1) => Number(value).toLocaleString("ru-RU", { maximumFractionDigits: digits });
  const setStatus = (text, kind = "") => {
    status.textContent = text;
    status.className = `svc-status${kind ? ` is-${kind}` : ""}`;
  };
  const authHeaders = () => ({ Authorization: `Bearer ${key}`, "Content-Type": "application/json" });
  const readJson = async (response) => { try { return await response.json(); } catch { return {}; } };

  const bubble = (role, text) => {
    empty.hidden = true;
    const item = document.createElement("article");
    item.className = `svc-msg svc-msg--${role}`;
    if (role === "assistant") {
      const body = document.createElement("div");
      body.className = "markdown-body";
      item.append(body);
      paint(body, text);
    } else {
      item.textContent = text;
    }
    log.append(item);
    return item;
  };

  const paint = (body, text) => {
    if (typeof window.renderMarkdown === "function") body.innerHTML = window.renderMarkdown(text);
    else body.textContent = text;
  };

  const meta = (item, text) => {
    const line = document.createElement("p");
    line.className = "svc-msg__meta";
    line.textContent = text;
    item.append(line);
  };

  const contextSize = (extra = "") => history.reduce((sum, m) => sum + m.content.length, 0) + extra.length;

  const updateCount = () => {
    const length = field.value.length;
    const total = contextSize(field.value);
    count.textContent = `${length} / ${limits.max_message_chars} · диалог ${total} / ${limits.max_context_chars}`;
    count.classList.toggle("is-over", length > limits.max_message_chars || total > limits.max_context_chars);
    send.disabled = busy || !key || !field.value.trim();
  };

  const showWho = (data) => {
    whoName.textContent = data.name;
    whoUsed.textContent = `${data.used} из ${data.limits.rate_per_minute} запросов за минуту`;
  };

  const login = async (candidate) => {
    const response = await fetch("api/me", { headers: { Authorization: `Bearer ${candidate}` } }).catch(() => null);
    if (!response) {
      setStatus("Сервис недоступен — проверьте адрес и интернет", "error");
      return null;
    }
    const data = await readJson(response);
    if (!response.ok) {
      setStatus(data.error || `Ошибка ${response.status}`, "error");
      return false;
    }
    key = candidate;
    store.set(KEY, key);
    keyForm.hidden = true;
    who.hidden = false;
    field.disabled = false;
    showWho(data);
    setStatus("");
    updateCount();
    return true;
  };

  const logout = () => {
    key = "";
    store.drop(KEY);
    keyForm.hidden = false;
    who.hidden = true;
    field.disabled = true;
    keyField.value = "";
    updateCount();
    keyField.focus();
  };

  const refreshWho = async () => {
    const response = await fetch("api/me", { headers: { Authorization: `Bearer ${key}` } }).catch(() => null);
    if (response?.ok) showWho(await readJson(response));
  };

  const ask = async (text) => {
    busy = true;
    updateCount();
    const userItem = bubble("user", text);
    history.push({ role: "user", content: text });
    setStatus("отправляем…");

    const started = performance.now();
    let response;
    try {
      response = await fetch("api/chat", { method: "POST", headers: authHeaders(), body: JSON.stringify({ messages: history }) });
    } catch {
      response = null;
    }

    const fail = (message) => {
      history.pop();
      userItem.classList.add("svc-msg--failed");
      field.value = text;
      setStatus(message, "error");
    };

    if (!response || !response.ok) {
      const data = response ? await readJson(response) : {};
      fail(response ? `${response.status} · ${data.error || "ошибка сервиса"}` : "Сервис недоступен — проверьте интернет");
      busy = false;
      updateCount();
      refreshWho();
      return;
    }

    const item = bubble("assistant", "");
    const body = item.querySelector(".markdown-body");
    let answer = "";
    let done = null;
    let firstPosition = null;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    const handle = (event) => {
      if (event.type === "queued") {
        if (firstPosition === null) firstPosition = event.position;
        setStatus(event.position > 0 ? `в очереди · перед вами ${event.position}` : "ваша очередь…", "queue");
      } else if (event.type === "start") {
        setStatus(event.waited_s > 0 ? `модель отвечает · ждали в очереди ${number(event.waited_s)} с` : "модель отвечает…");
      } else if (event.type === "delta") {
        answer += event.text;
        paint(body, answer);
      } else if (event.type === "done") {
        done = event;
      } else if (event.type === "error") {
        throw new Error(event.error);
      }
    };

    try {
      for (;;) {
        const { value, done: finished } = await reader.read();
        if (finished) break;
        buffer += decoder.decode(value, { stream: true });
        let newline;
        while ((newline = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, newline).trim();
          buffer = buffer.slice(newline + 1);
          if (line) handle(JSON.parse(line));
        }
      }
      if (!done) throw new Error("Ответ оборвался");
      history.push({ role: "assistant", content: answer.trim() });
      store.set(HISTORY, JSON.stringify(history));
      const total = (performance.now() - started) / 1000;
      const parts = [
        firstPosition > 0 ? `в очереди ${number(done.waited_s)} с (перед вами было ${firstPosition})` : "без очереди",
        `ответ ${number(done.wall_s)} с`,
        done.output_tps ? `${number(done.output_tps, 0)} ток/с` : null,
        `всего ${number(total)} с`,
      ].filter(Boolean);
      meta(item, parts.join(" · "));
      setStatus("");
    } catch (error) {
      item.remove();
      fail(error.message || "Ответ оборвался");
    }
    busy = false;
    updateCount();
    refreshWho();
    field.focus();
  };

  const checkHealth = async () => {
    const response = await fetch("api/health").catch(() => null);
    const data = response?.ok ? await readJson(response) : null;
    if (!data) {
      health.textContent = "сервис не отвечает";
      health.className = "svc-health is-down";
      return;
    }
    health.textContent = data.busy ? `модель занята${data.waiting ? ` · ждут ${data.waiting}` : ""}` : "модель свободна";
    health.className = `svc-health ${data.busy ? "is-busy" : "is-free"}`;
  };

  keyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const candidate = keyField.value.trim();
    if (candidate && await login(candidate)) field.focus();
  });
  root.querySelector("[data-logout]").addEventListener("click", logout);
  root.querySelector("[data-reset]").addEventListener("click", () => {
    if (busy) return;
    history = [];
    store.drop(HISTORY);
    log.querySelectorAll(".svc-msg").forEach((item) => item.remove());
    empty.hidden = false;
    setStatus("");
    updateCount();
  });
  field.addEventListener("input", updateCount);
  field.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && window.matchMedia("(hover: hover)").matches) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = field.value.trim();
    if (!text || busy || !key) return;
    field.value = "";
    ask(text);
  });

  history.forEach((message) => bubble(message.role, message.content));
  updateCount();
  checkHealth();
  window.setInterval(checkHealth, 4000);
  if (key) login(key).then((ok) => { if (ok === false) logout(); });
})();
