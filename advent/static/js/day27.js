/* Локальный чат дня 27: поток ответа, история и состояние Ollama. */
(() => {
  const form = document.querySelector("#agent-form");
  if (!form) return;

  const field = document.querySelector("#agent-message");
  const submit = document.querySelector("#agent-submit");
  const reset = document.querySelector("#agent-reset");
  const status = document.querySelector("#agent-status");
  const log = document.querySelector("#agent-log");
  const empty = log.querySelector("[data-log-empty]");
  const template = document.querySelector("#agent-turn-template");
  const storageError = document.querySelector("#day27-storage-error");
  const storageStatus = document.querySelector("[data-history-status]");
  const serviceCheck = document.querySelector("[data-service-check]");
  const serviceStatus = document.querySelector("[data-service-status]");
  const serviceDetail = document.querySelector("[data-service-detail]");
  const presets = [...document.querySelectorAll("[data-preset]")];
  const prompts = {
    context: "Я делаю проект «AI Advent». Мой следующий шаг — подключить локальную модель к RAG. У меня уже есть индекс документов проекта. Запомни это и коротко подтверди.",
    memory: "Как называется мой проект и какой следующий шаг я запланировал? Ответь двумя короткими пунктами.",
    explain: "Объясни простыми словами, чем локальная языковая модель отличается от облачной. Приведи три отличия.",
  };

  let busy = false;
  let checkingService = false;
  let failedTurns = [];
  let followBody = null;
  let following = false;
  let renderFrame = null;
  const rawBodies = new WeakMap();
  const pendingRender = new Set();

  const errorMessage = (error, fallback) => error?.message || fallback;
  const readJson = async (response) => {
    try {
      return await response.json();
    } catch {
      return {};
    }
  };

  const nearAnswer = () =>
    followBody && followBody.getBoundingClientRect().bottom - window.innerHeight < 160;

  // Прокручивается сама страница; чтение предыдущих сообщений прерывает следование.
  window.addEventListener("wheel", (event) => {
    if (event.deltaY < 0) following = false;
    else if (nearAnswer()) following = true;
  }, { passive: true });
  window.addEventListener("touchstart", () => { following = false; }, { passive: true });
  window.addEventListener("keydown", (event) => {
    if (["PageUp", "Home", "ArrowUp"].includes(event.key)) following = false;
  });

  const setBody = (body, content) => {
    rawBodies.set(body, content);
    pendingRender.add(body);
    if (renderFrame !== null) return;
    renderFrame = window.requestAnimationFrame(() => {
      renderFrame = null;
      pendingRender.forEach((element) => {
        const raw = rawBodies.get(element) || "";
        if (typeof window.renderMarkdown === "function") {
          element.innerHTML = window.renderMarkdown(raw);
        } else {
          element.textContent = raw;
        }
      });
      pendingRender.clear();
      if (following && followBody?.isConnected) {
        const overflow = followBody.getBoundingClientRect().bottom - window.innerHeight + 70;
        if (overflow > 1) window.scrollBy({ top: overflow, behavior: "instant" });
      }
    });
  };

  const addTurn = (role, content) => {
    empty.hidden = true;
    const turn = template.content.firstElementChild.cloneNode(true);
    turn.dataset.turn = role;
    turn.querySelector("[data-turn-role]").textContent = role === "user" ? "вы" : "модель";
    setBody(turn.querySelector("[data-turn-body]"), content);
    log.append(turn);
    return turn;
  };

  const renderMessages = (messages) => {
    log.querySelectorAll("[data-turn]").forEach((turn) => turn.remove());
    empty.hidden = false;
    (messages || []).forEach((message) => {
      if (["user", "assistant"].includes(message.role)) {
        addTurn(message.role, message.content || "");
      }
    });
    failedTurns = [];
  };

  const showStorageError = (message) => {
    storageError.textContent = message;
    storageError.hidden = !message;
  };

  const renderSnapshot = (snapshot) => {
    const storage = snapshot?.storage;
    if (!storage) {
      storageStatus.textContent = "История сохраняется на компьютере";
      return;
    }
    const count = storage.messages ?? snapshot.messages?.length ?? 0;
    let text = count ? `На компьютере сохранено сообщений: ${count}` : "Новый диалог · история пуста";
    if (storage.saved_at && count) {
      const savedAt = new Date(storage.saved_at);
      if (!Number.isNaN(savedAt.getTime())) {
        text += ` · ${savedAt.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`;
      }
    }
    storageStatus.textContent = text;
  };

  const setBusy = (value) => {
    busy = value;
    field.disabled = value;
    submit.disabled = value;
    reset.disabled = value;
    presets.forEach((button) => { button.disabled = value; });
    serviceCheck.disabled = value || checkingService;
    log.setAttribute("aria-busy", String(value));
  };

  const checkService = async () => {
    if (checkingService || busy) return;
    checkingService = true;
    serviceCheck.disabled = true;
    serviceStatus.textContent = "Проверяю Ollama…";
    serviceStatus.dataset.state = "checking";
    serviceDetail.textContent = "";
    try {
      const response = await fetch(form.dataset.statusUrl, {
        headers: { Accept: "application/json" },
        signal: AbortSignal.timeout(15000),
      });
      const data = await readJson(response);
      if (!response.ok || !data.ok || data.installed === false) {
        throw new Error(data.error || (data.installed === false
          ? "Модель gpt-oss:20b не установлена в Ollama."
          : "Не удалось подключиться к Ollama. Запустите её и повторите проверку."));
      }
      serviceStatus.textContent = "Ollama доступна";
      serviceStatus.dataset.state = "ready";
      serviceDetail.textContent = data.version ? `Версия ${data.version}` : "Готова к разговору";
      if (data.url) document.querySelector("[data-service-url]").textContent = data.url;
    } catch (error) {
      serviceStatus.textContent = "Ollama недоступна";
      serviceStatus.dataset.state = "unavailable";
      serviceDetail.textContent = errorMessage(error, "Запустите Ollama и повторите проверку.");
    } finally {
      checkingService = false;
      serviceCheck.disabled = busy;
    }
  };

  const readStream = async (body, onEvent) => {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (line.trim() && onEvent(JSON.parse(line)) === false) return;
        }
        if (done) break;
      }
      if (buffer.trim()) onEvent(JSON.parse(buffer));
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const message = field.value.trim();
    if (!message) { field.focus(); return; }
    failedTurns.forEach((turn) => turn.remove());
    failedTurns = [];
    const userTurn = addTurn("user", message);
    const answerTurn = addTurn("assistant", "");
    const body = answerTurn.querySelector("[data-turn-body]");
    const meta = answerTurn.querySelector("[data-turn-meta]");
    let finished = false;
    let saveError = null;
    field.value = "";
    body.classList.add("is-streaming");
    followBody = body;
    following = true;
    answerTurn.scrollIntoView({ block: "nearest", behavior: "smooth" });
    setBusy(true);
    status.textContent = "Модель думает…";

    try {
      const response = await fetch(form.dataset.chatUrl, {
        method: "POST",
        headers: { Accept: "application/x-ndjson", "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      if (!response.ok) {
        const data = await readJson(response);
        throw new Error(data.error || "Не удалось получить ответ. Повторите запрос.");
      }
      if (!response.body) throw new Error("Браузер не поддерживает потоковые ответы.");
      await readStream(response.body, (item) => {
        if (item.type === "delta" && typeof item.content === "string") {
          setBody(body, (rawBodies.get(body) || "") + item.content);
          status.textContent = "Модель отвечает…";
        } else if (item.type === "done") {
          if (typeof item.reply?.content !== "string") throw new Error("Ответ модели не получен.");
          finished = true;
          setBody(body, item.reply.content);
          const tokens = item.reply.output_tokens;
          meta.textContent = typeof tokens === "number" ? `${tokens} токенов` : "";
          renderSnapshot(item.snapshot);
          saveError = item.save_error || null;
          return false;
        } else if (item.type === "error") {
          throw new Error(item.error || "Ответ прервался. Повторите запрос.");
        }
      });
      if (!finished) throw new Error("Ответ прервался. Повторите запрос.");
      showStorageError(saveError ? `Ответ получен, но историю не удалось сохранить: ${saveError}` : "");
      status.textContent = saveError ? "Ответ готов · история не сохранена" : "Ответ готов";
    } catch (error) {
      answerTurn.classList.add("is-failed");
      setBody(body, errorMessage(error, "Не удалось получить ответ. Повторите запрос."));
      meta.textContent = "запрос не завершён";
      status.textContent = "Не удалось ответить · можно повторить запрос";
      field.value = message;
      failedTurns = [userTurn, answerTurn];
    } finally {
      body.classList.remove("is-streaming");
      following = false;
      followBody = null;
      setBusy(false);
      field.focus({ preventScroll: true });
    }
  });

  reset.addEventListener("click", async () => {
    if (busy) return;
    setBusy(true);
    status.textContent = "Открываю новый диалог…";
    try {
      const response = await fetch(form.dataset.resetUrl, { method: "POST" });
      const data = await readJson(response);
      if (!response.ok) throw new Error(data.error || "Не удалось начать новый диалог.");
      renderMessages(data.messages);
      renderSnapshot(data);
      showStorageError("");
      field.value = "";
      status.textContent = "Новый диалог · модель не помнит прошлые сообщения";
    } catch (error) {
      status.textContent = errorMessage(error, "Не удалось начать новый диалог.");
    } finally {
      setBusy(false);
      field.focus({ preventScroll: true });
    }
  });

  presets.forEach((button) => {
    button.addEventListener("click", () => {
      if (busy) return;
      field.value = prompts[button.dataset.preset] || "";
      field.focus({ preventScroll: true });
      field.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  });

  field.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !busy) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  serviceCheck.addEventListener("click", checkService);
  try {
    const initial = JSON.parse(document.querySelector("#agent-initial").textContent);
    renderMessages(initial?.messages);
    renderSnapshot(initial);
    if (initial?.messages?.length) status.textContent = "История восстановлена";
  } catch {
    showStorageError("Не удалось восстановить историю на странице. Обновите страницу или начните новый диалог.");
  }
  checkService();
})();
