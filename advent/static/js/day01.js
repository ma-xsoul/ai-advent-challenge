(() => {
  const form = document.querySelector("#prompt-form");
  if (!form) return;

  const promptInput = form.querySelector("#prompt");
  const submitButton = form.querySelector("#submit-button");
  const requestList = document.querySelector("#request-list");
  const requestTemplate = document.querySelector("#request-template");
  const emptyState = document.querySelector("#empty-state");
  const requestCount = document.querySelector("#request-count");
  const chatUrl = form.dataset.chatUrl;

  let totalRequests = 0;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const prompt = promptInput.value.trim();
    if (!prompt) return;

    setLoading(true);
    const sentAt = new Date();
    let answerBlock = null;

    try {
      const response = await fetch(chatUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt }),
      });
      const data = await response.json();

      answerBlock = addRequest({
        prompt,
        sentAt,
        data,
        ok: response.ok,
        status: response.status,
      });

      if (response.ok) promptInput.value = "";
    } catch (error) {
      answerBlock = addRequest({
        prompt,
        sentAt,
        data: {
          error: "Браузер не смог получить ответ от сервера",
          details: error.message,
        },
        ok: false,
        status: "NETWORK",
      });
    } finally {
      setLoading(false);
      focusAnswer(answerBlock);
    }
  });

  promptInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      form.requestSubmit();
    }
  });

  function addRequest({ prompt, sentAt, data, ok, status }) {
    const card = requestTemplate.content.cloneNode(true);
    const statusElement = card.querySelector(".request-status");
    const answerBlock = card.querySelector(".answer-block");
    const jsonText = JSON.stringify(data, null, 2);
    const assistantAnswer = getAssistantAnswer(data);

    answerBlock.tabIndex = -1;
    card.querySelector(".request-time").textContent = sentAt.toLocaleString("ru-RU", {
      dateStyle: "medium",
      timeStyle: "medium",
    });
    card.querySelector(".request-prompt").textContent = prompt;
    card.querySelector(".assistant-answer").innerHTML = renderMessage(assistantAnswer);
    card.querySelector(".response-json").textContent = jsonText;

    statusElement.textContent = (ok ? "Успешно · " : "Ошибка · ") + status;
    statusElement.classList.add(ok ? "status--success" : "status--error");

    const copyButton = card.querySelector(".copy-button");
    copyButton.addEventListener("click", async () => {
      const copied = await copyText(jsonText);
      copyButton.textContent = copied ? "Скопировано" : "Не удалось";
      window.setTimeout(() => {
        copyButton.textContent = "Копировать";
      }, 1400);
    });

    requestList.prepend(card);
    emptyState.hidden = true;
    totalRequests += 1;
    requestCount.textContent = formatRequestCount(totalRequests);

    return answerBlock;
  }

  function focusAnswer(answerBlock) {
    if (!answerBlock) return;

    window.requestAnimationFrame(() => {
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      answerBlock.focus({ preventScroll: true });
      answerBlock.scrollIntoView({
        behavior: reduceMotion ? "auto" : "smooth",
        block: "center",
      });
    });
  }

  function renderMessage(message) {
    if (typeof window.renderMarkdown === "function") {
      return window.renderMarkdown(message);
    }

    // Запасной вариант, если общий рендерер не загрузился.
    return String(message).replace(
      /[&<>"']/g,
      (character) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#039;",
        })[character],
    );
  }

  function getAssistantAnswer(data) {
    const content = data?.choices?.[0]?.message?.content;
    if (typeof content === "string") return content;

    const error = data?.error;
    if (typeof error === "string") return error;
    if (typeof error?.message === "string") return error.message;
    if (error && typeof error === "object") return JSON.stringify(error, null, 2);

    return "В JSON-ответе нет поля choices[0].message.content";
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      return false;
    }
  }

  function setLoading(isLoading) {
    submitButton.disabled = isLoading;
    submitButton.classList.toggle("is-loading", isLoading);
    submitButton.setAttribute("aria-label", isLoading ? "Отправка запроса" : "Отправить");
    promptInput.disabled = isLoading;
    form.setAttribute("aria-busy", String(isLoading));
  }

  function formatRequestCount(count) {
    const lastTwo = count % 100;
    const last = count % 10;
    let word = "запросов";

    if (lastTwo < 11 || lastTwo > 14) {
      if (last === 1) word = "запрос";
      if (last >= 2 && last <= 4) word = "запроса";
    }

    return count + " " + word;
  }
})();
