const form = document.querySelector("#prompt-form");
const promptInput = document.querySelector("#prompt");
const submitButton = document.querySelector("#submit-button");
const requestList = document.querySelector("#request-list");
const requestTemplate = document.querySelector("#request-template");
const emptyState = document.querySelector("#empty-state");
const requestCount = document.querySelector("#request-count");

let totalRequests = 0;

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const prompt = promptInput.value.trim();
  if (!prompt) return;

  setLoading(true);
  const sentAt = new Date();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    });
    const data = await response.json();

    addRequest({
      prompt,
      sentAt,
      data,
      ok: response.ok,
      status: response.status,
    });

    if (response.ok) promptInput.value = "";
  } catch (error) {
    addRequest({
      prompt,
      sentAt,
      data: { error: "Браузер не смог получить ответ от сервера", details: error.message },
      ok: false,
      status: "NETWORK",
    });
  } finally {
    setLoading(false);
    promptInput.focus();
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
  const jsonText = JSON.stringify(data, null, 2);
  const assistantAnswer =
    data?.choices?.[0]?.message?.content ??
    data?.error ??
    "В JSON-ответе нет поля choices[0].message.content";

  card.querySelector(".request-time").textContent = sentAt.toLocaleString("ru-RU", {
    dateStyle: "medium",
    timeStyle: "medium",
  });
  card.querySelector(".request-prompt").textContent = prompt;
  card.querySelector(".assistant-answer").innerHTML = renderMessage(assistantAnswer);
  card.querySelector(".response-json").textContent = jsonText;

  statusElement.textContent = ok ? `Успешно · ${status}` : `Ошибка · ${status}`;
  statusElement.classList.add(ok ? "status--success" : "status--error");

  const copyButton = card.querySelector(".copy-button");
  copyButton.addEventListener("click", async () => {
    await navigator.clipboard.writeText(jsonText);
    copyButton.textContent = "Скопировано";
    window.setTimeout(() => (copyButton.textContent = "Копировать"), 1400);
  });

  requestList.prepend(card);
  emptyState.hidden = true;
  totalRequests += 1;
  requestCount.textContent = formatRequestCount(totalRequests);
}

function renderMessage(message) {
  const escaped = String(message).replace(
    /[&<>"']/g,
    (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    })[character],
  );

  return escaped
    .replace(/^#{1,6}\s+(.+)$/gm, "<strong>$1</strong>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/`([^`\n]+)`/g, "<code>$1</code>");
}

function setLoading(isLoading) {
  submitButton.disabled = isLoading;
  submitButton.classList.toggle("is-loading", isLoading);
  promptInput.disabled = isLoading;
}

function formatRequestCount(count) {
  const lastTwo = count % 100;
  const last = count % 10;
  let word = "запросов";

  if (lastTwo < 11 || lastTwo > 14) {
    if (last === 1) word = "запрос";
    if (last >= 2 && last <= 4) word = "запроса";
  }

  return `${count} ${word}`;
}
