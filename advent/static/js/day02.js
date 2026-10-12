(() => {
  const form = document.querySelector("#comparison-form");
  if (!form) return;

  const promptInput = form.querySelector("#comparison-prompt");
  const modelInput = form.querySelector("#model");
  const formatInput = form.querySelector("#output-format");
  const maxTokensInput = form.querySelector("#max-tokens");
  const stopInput = form.querySelector("#stop-sequence");
  const modelControl = form.querySelector('[data-custom-select="model"]');
  const formatControl = form.querySelector('[data-custom-select="format"]');
  const stopControl = form.querySelector('[data-custom-select="stop"]');
  const stopHelp = form.querySelector("#stop-help");
  const submitButton = form.querySelector("#comparison-submit");
  const status = form.querySelector("#comparison-status");
  const results = document.querySelector("#comparison-results");
  const errorBox = document.querySelector("#comparison-error");
  const columns = document.querySelector(".comparison-columns");
  const analysis = document.querySelector(".comparison-analysis");
  const requestPreview = document.querySelector("#request-preview");
  const controlledPayload = document.querySelector("#controlled-payload");
  const baselinePayload = document.querySelector("#baseline-payload");
  const customSelects = [...form.querySelectorAll("[data-custom-select]")];
  const compareUrl = form.dataset.compareUrl;
  const streamUrl = `${compareUrl}/stream`;
  const metaAnalysisUrl = `${compareUrl}/meta-analysis`;

  // Выпадающие списки — общий компонент static/js/custom-select.js.
  const {
    init: initializeCustomSelect,
    reset: resetCustomSelect,
    select: selectCustomOption,
    setDisabled: setCustomSelectDisabled,
  } = window.CustomSelect;

  customSelects.forEach(initializeCustomSelect);
  formatInput.addEventListener("change", updateStopControl);
  updateStopControl();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const prompt = promptInput.value.trim();
    if (!prompt) {
      promptInput.focus();
      return;
    }

    const requestPayload = {
      prompt,
      model: modelInput.value,
      output_format: formatInput.value,
      max_tokens: Number(maxTokensInput.value),
      stop_sequence: formatInput.value === "json" ? "" : stopInput.value,
    };
    setLoading(true);
    hideError();
    const requestPreviewData = {};
    let finishedStreams = 0;
    prepareStreamingResults(requestPayload.model);
    focusResults();

    try {
      const streams = await Promise.allSettled(
        ["controlled", "baseline"].map((variant) =>
          consumeAnswerStream(variant, requestPayload, (streamEvent) => {
            if (streamEvent.type === "start") {
              requestPreviewData[variant] = streamEvent.request;
              if (requestPreviewData.controlled && requestPreviewData.baseline) {
                renderRequestPreview(requestPreviewData);
              }
            } else if (streamEvent.type === "delta") {
              appendStreamDelta(variant, streamEvent.content);
            } else if (streamEvent.type === "done") {
              finishedStreams += 1;
              renderAnswer(variant, streamEvent.answer);
              if (finishedStreams === 1) {
                status.textContent = "Один ответ готов, второй печатается…";
              }
            }
          }),
        ),
      );
      const failedStream = streams.find((stream) => stream.status === "rejected");
      if (failedStream) {
        showError(failedStream.reason.message, requestPreviewData);
        return;
      }

      const controlled = streams[0].value;
      const baseline = streams[1].value;
      const analysisItems = buildAnalysis(controlled, baseline, requestPayload);
      renderAnalysis([...analysisItems, "Мета-анализ формируется…"]);
      status.textContent = "Ответы готовы, формируется мета-анализ…";

      let completionStatus = controlled?.warning ? "Готово с предупреждением" : "Готово";
      try {
        const metaAnalysis = await requestMetaAnalysis(
          requestPayload,
          controlled,
          baseline,
        );
        renderAnalysis([...analysisItems, `Мета-анализ: ${metaAnalysis.content}`]);
      } catch (error) {
        renderAnalysis([...analysisItems, `Мета-анализ недоступен: ${error.message}`]);
        completionStatus = "Ответы готовы, мета-анализ недоступен";
      }

      form.reset();
      resetCustomSelect(modelControl);
      resetCustomSelect(formatControl);
      resetCustomSelect(stopControl);
      updateStopControl();
      status.textContent = completionStatus;
    } catch (error) {
      showError("Браузер не смог получить поток от сервера: " + error.message, requestPreviewData);
    } finally {
      setLoading(false);
    }
  });

  promptInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      form.requestSubmit();
    }
  });

  function updateStopControl() {
    const isJson = formatInput.value === "json";
    if (isJson) {
      selectCustomOption(stopControl, "", false);
      setCustomSelectDisabled(stopControl, true, "Недоступно для JSON");
      stopHelp.textContent = "Для строгого JSON stop выключен: он может оборвать JSON на середине.";
    } else {
      setCustomSelectDisabled(stopControl, false);
      stopHelp.textContent = "Stop sequence завершит ответ, когда модель встретит выбранный маркер.";
    }
  }

  function setLoading(isLoading) {
    submitButton.disabled = isLoading;
    promptInput.disabled = isLoading;
    maxTokensInput.disabled = isLoading;
    setCustomSelectDisabled(modelControl, isLoading);
    setCustomSelectDisabled(formatControl, isLoading);

    if (isLoading) {
      setCustomSelectDisabled(stopControl, true);
    } else {
      updateStopControl();
    }

    form.setAttribute("aria-busy", String(isLoading));
    if (isLoading) status.textContent = "Оба ответа печатаются…";
  }

  async function readJson(response) {
    try {
      return await response.json();
    } catch {
      return { error: "Сервер вернул неожиданный ответ" };
    }
  }

  async function consumeAnswerStream(variant, payload, onEvent) {
    const response = await fetch(streamUrl, {
      method: "POST",
      headers: {
        Accept: "application/x-ndjson",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ...payload, variant }),
    });

    if (!response.ok) {
      const data = await readJson(response);
      throw new Error(getErrorMessage(data));
    }
    if (!response.body) {
      throw new Error("Браузер не поддерживает потоковые ответы");
    }

    let answer = null;
    await readNdjson(response.body, (streamEvent) => {
      if (!streamEvent || streamEvent.variant !== variant) return;
      if (streamEvent.type === "error") {
        throw new Error(streamEvent.error || "Поток DeepSeek завершился с ошибкой");
      }
      onEvent(streamEvent);
      if (streamEvent.type === "done") answer = streamEvent.answer;
    });

    if (!answer) throw new Error("DeepSeek не завершил ответ");
    return answer;
  }

  async function requestMetaAnalysis(payload, controlled, baseline) {
    const response = await fetch(metaAnalysisUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, controlled, baseline }),
    });
    const data = await readJson(response);
    if (!response.ok) throw new Error(getErrorMessage(data));
    if (typeof data?.content !== "string" || !data.content.trim()) {
      throw new Error("модель не вернула текст");
    }
    return data;
  }

  async function readNdjson(body, onEvent) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.trim()) onEvent(JSON.parse(line));
      }
      if (done) break;
    }

    if (buffer.trim()) onEvent(JSON.parse(buffer));
  }

  function prepareStreamingResults(model) {
    requestPreview.hidden = true;
    columns.hidden = false;
    analysis.hidden = true;
    results.hidden = false;

    for (const prefix of ["controlled", "baseline"]) {
      const output = document.querySelector(`#${prefix}-answer`);
      output.textContent = "";
      output.classList.add("is-streaming");
      document.querySelector(`#${prefix}-model`).textContent = model;
      document.querySelector(`#${prefix}-finish`).textContent = "печатает…";
      document.querySelector(`#${prefix}-tokens`).textContent = "—";
      const warning = document.querySelector(`#${prefix}-warning`);
      if (warning) {
        warning.textContent = "";
        warning.hidden = true;
      }
    }
  }

  function appendStreamDelta(prefix, content) {
    if (typeof content !== "string" || !content) return;
    const output = document.querySelector(`#${prefix}-answer`);
    output.textContent += content;
    output.scrollTop = output.scrollHeight;
  }

  function renderComparison(data) {
    renderRequestPreview(data.request_preview, true);
    renderAnswer("controlled", data.controlled);
    renderAnswer("baseline", data.baseline);

    renderAnalysis(data.analysis);
    results.hidden = false;
  }

  function renderAnswer(prefix, answer) {
    const content = typeof answer?.content === "string" ? answer.content : "Ответ не получен";
    const warning = document.querySelector(`#${prefix}-warning`);

    document.querySelector(`#${prefix}-answer`).textContent = content;
    document.querySelector(`#${prefix}-answer`).classList.remove("is-streaming");
    document.querySelector(`#${prefix}-model`).textContent = answer?.model || "—";
    document.querySelector(`#${prefix}-finish`).textContent = answer?.finish_reason || "—";
    document.querySelector(`#${prefix}-tokens`).textContent = formatTokens(answer?.output_tokens);

    if (warning) {
      warning.textContent = answer?.warning || "";
      warning.hidden = !answer?.warning;
    }
  }

  function renderAnalysis(items) {
    const list = document.querySelector("#comparison-analysis-list");
    list.replaceChildren();
    for (const item of Array.isArray(items) ? items : []) {
      const listItem = document.createElement("li");
      listItem.textContent = String(item);
      list.append(listItem);
    }
    analysis.hidden = false;
  }

  function buildAnalysis(controlled, baseline, settings) {
    const controlledCharacters = controlled?.characters ?? controlled?.content?.length ?? 0;
    const baselineCharacters = baseline?.characters ?? baseline?.content?.length ?? 0;
    let size = "Оба ответа одинаковы по длине.";
    if (controlledCharacters < baselineCharacters) size = "Контролируемый ответ короче базового.";
    if (controlledCharacters > baselineCharacters) size = "Контролируемый ответ длиннее базового.";

    let formatNote = "Формат: для контролируемого ответа явно передан обычный текст.";
    if (controlled?.rejected) {
      formatNote = "Запрос отклонён: сервис отвечает только на запросы о списках стран.";
    } else if (settings.output_format === "json" && controlled?.valid_json) {
      formatNote = "Формат: JSON соответствует схеме query + result.countries.";
    } else if (settings.output_format === "json") {
      formatNote = controlled?.warning || "JSON не удалось проверить.";
    }

    const items = [
      `Модель: ${settings.model}.`,
      formatNote,
      `Лимит: в API передано до ${settings.max_tokens} токенов.`,
      `Длина: с параметрами — ${controlledCharacters} симв., без параметров — ${baselineCharacters} симв. ${size}`,
    ];
    if (settings.stop_sequence) {
      items.push(`Стоп-последовательность передана в API: ${settings.stop_sequence}.`);
    }
    return items;
  }

  function renderRequestPreview(preview, open = false) {
    if (!preview?.controlled || !preview?.baseline) {
      requestPreview.hidden = true;
      return;
    }

    controlledPayload.textContent = formatRequest(preview.controlled);
    baselinePayload.textContent = formatRequest(preview.baseline);
    requestPreview.hidden = false;
    requestPreview.open = open;
  }

  function formatRequest(request) {
    return `${request.method} ${request.url}\n\n${JSON.stringify(request.body, null, 2)}`;
  }

  function formatTokens(value) {
    return typeof value === "number" ? String(value) : "—";
  }

  function showError(message, preview) {
    document.querySelectorAll(".answer-output.is-streaming").forEach((output) => {
      output.classList.remove("is-streaming");
    });
    renderRequestPreview(preview, true);
    errorBox.textContent = message;
    errorBox.hidden = false;
    columns.hidden = true;
    analysis.hidden = true;
    results.hidden = false;
    status.textContent = "Не удалось сравнить";
    focusResults();
  }

  function hideError() {
    errorBox.hidden = true;
    errorBox.textContent = "";
  }

  function getErrorMessage(data) {
    if (typeof data?.error === "string") return data.error;
    if (typeof data?.error?.message === "string") return data.error.message;
    return "Не удалось выполнить запрос";
  }

  function focusResults() {
    window.requestAnimationFrame(() => {
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      results.focus({ preventScroll: true });
      results.scrollIntoView({
        behavior: reduceMotion ? "auto" : "smooth",
        block: "start",
      });
    });
  }
})();
