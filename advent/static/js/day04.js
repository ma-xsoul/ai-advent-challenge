(() => {
  const form = document.querySelector("#temperature-form");
  if (!form) return;

  const promptField = document.querySelector("#temperature-prompt");
  const modelField = document.querySelector("#day4-model");
  const slider = document.querySelector("#temperature-slider");
  const readout = document.querySelector("#temperature-readout");
  const heat = document.querySelector(".day4-field--heat");
  const ticks = [...document.querySelectorAll(".day4-heat__tick")];
  const promptGeneratorButton = document.querySelector("#prompt-generator");
  const promptInput = document.querySelector(".day4-prompt-input");
  const promptLoading = document.querySelector("#prompt-loading");
  const submitButton = document.querySelector("#temperature-submit");
  const compareButton = document.querySelector("#temperature-compare");
  const status = document.querySelector("#temperature-status");
  const resultsSection = document.querySelector("#temperature-results");
  const grid = document.querySelector("#temperature-grid");
  const errorBox = document.querySelector("#temperature-error");
  const analysis = document.querySelector("#temperature-analysis");
  const analysisText = document.querySelector("#temperature-analysis-text");
  const cardTemplate = document.querySelector("#temperature-card-template");
  const selectRoots = [...document.querySelectorAll("[data-day4-select]")];
  const streamUrl = `${form.dataset.answerUrl}/stream`;
  const promptUrl = form.dataset.promptUrl;
  const sliderMax = Number(slider.max) || 2;

  // Ответы приходят потоком: сырой текст храним отдельно от разметки
  // и перерисовываем не чаще одного раза за кадр.
  const rawAnswers = new WeakMap();
  const pendingRender = new Set();
  const cards = new Map();
  let renderFrame = null;

  const renderRaw = (output) => {
    const raw = rawAnswers.get(output) || "";

    if (typeof window.renderMarkdown === "function") {
      output.innerHTML = window.renderMarkdown(raw);
    } else {
      output.textContent = raw;
    }
  };

  const scheduleRender = (output) => {
    pendingRender.add(output);
    if (renderFrame !== null) return;
    renderFrame = window.requestAnimationFrame(() => {
      renderFrame = null;
      pendingRender.forEach(renderRaw);
      pendingRender.clear();
    });
  };

  const setAnswer = (output, text) => {
    rawAnswers.set(output, text);
    scheduleRender(output);
  };

  const appendAnswer = (output, chunk) => {
    setAnswer(output, (rawAnswers.get(output) || "") + chunk);
  };

  const rawAnswer = (output) => rawAnswers.get(output) || "";

  const revealBlock = (element) => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    element.scrollIntoView({
      behavior: reduceMotion ? "auto" : "smooth",
      block: "nearest",
    });
  };

  // Ползунок: заливка и подпись идут за значением, чтобы цвет читался как температура.
  const describeTemperature = (value) => {
    if (value <= 0.3) return "холодная";
    if (value <= 0.9) return "рабочая";
    if (value <= 1.5) return "горячая";
    return "предельная";
  };

  const cardDescription = (value) => {
    if (value <= 0.3) return "Почти всегда выбирается самый вероятный токен.";
    if (value <= 0.9) return "Связный текст, но формулировки меняются между запусками.";
    if (value <= 1.5) return "Заметный разброс: больше неожиданных ходов и лексики.";
    return "Край диапазона: связность может теряться.";
  };

  const syncSlider = () => {
    const value = Number(slider.value);
    heat.style.setProperty("--t", String(value / sliderMax));
    readout.textContent = formatTemperature(value);
    readout.dataset.mood = describeTemperature(value);
    ticks.forEach((tick) => {
      tick.classList.toggle("is-active", Number(tick.dataset.temperature) === value);
    });
  };

  const formatTemperature = (value) => String(Number(value.toFixed(2)));

  slider.addEventListener("input", syncSlider);
  ticks.forEach((tick) => {
    tick.addEventListener("click", () => {
      slider.value = tick.dataset.temperature;
      syncSlider();
      slider.focus({ preventScroll: true });
    });
  });
  syncSlider();

  const closeSelect = (root) => {
    root.classList.remove("is-open");
    root.querySelector(".day4-select__trigger").setAttribute("aria-expanded", "false");
    root.querySelector(".day4-select__menu").hidden = true;
  };

  selectRoots.forEach((root) => {
    const input = root.querySelector("input[type='hidden']");
    const trigger = root.querySelector(".day4-select__trigger");
    const value = trigger.querySelector("span:first-child");
    const menu = root.querySelector(".day4-select__menu");
    const options = [...root.querySelectorAll(".day4-select__option")];

    const openSelect = () => {
      root.classList.add("is-open");
      trigger.setAttribute("aria-expanded", "true");
      menu.hidden = false;
      (options.find((option) => option.getAttribute("aria-selected") === "true") || options[0])?.focus();
    };

    const chooseOption = (option) => {
      options.forEach((candidate) => {
        candidate.setAttribute("aria-selected", String(candidate === option));
      });
      input.value = option.dataset.value;
      value.textContent = option.dataset.label;
      closeSelect(root);
      trigger.focus();
    };

    trigger.addEventListener("click", () => {
      if (root.classList.contains("is-open")) {
        closeSelect(root);
      } else {
        openSelect();
      }
    });

    trigger.addEventListener("keydown", (event) => {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
        event.preventDefault();
        openSelect();
      }
    });

    options.forEach((option, index) => {
      option.addEventListener("click", () => chooseOption(option));
      option.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          closeSelect(root);
          trigger.focus();
          return;
        }
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          chooseOption(option);
          return;
        }
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          const direction = event.key === "ArrowDown" ? 1 : -1;
          options[(index + direction + options.length) % options.length].focus();
        }
      });
    });
  });

  document.addEventListener("click", (event) => {
    selectRoots.forEach((root) => {
      if (!root.contains(event.target)) closeSelect(root);
    });
  });

  const setPromptGenerating = (isGenerating) => {
    promptLoading.hidden = !isGenerating;
    promptInput.classList.toggle("is-generating", isGenerating);
  };

  // Пока идёт запрос, форма целиком недоступна: иначе легко отправить второй.
  const setLoading = (isLoading) => {
    form.classList.toggle("is-busy", isLoading);
    promptField.disabled = isLoading;
    promptGeneratorButton.disabled = isLoading;
    submitButton.disabled = isLoading;
    compareButton.disabled = isLoading;
    slider.disabled = isLoading;
    ticks.forEach((tick) => {
      tick.disabled = isLoading;
    });
    selectRoots.forEach((root) => {
      root.querySelector(".day4-select__trigger").disabled = isLoading;
      if (isLoading) closeSelect(root);
    });
  };

  const resetResults = () => {
    errorBox.hidden = true;
    errorBox.textContent = "";
    analysis.hidden = true;
    setAnswer(analysisText, "");
    analysisText.classList.remove("is-streaming");
    grid.textContent = "";
    cards.clear();
  };

  const cardFor = (label) => cards.get(label);

  const createCard = (event) => {
    const label = event.label;
    if (cards.has(label)) return cards.get(label);

    const card = cardTemplate.content.firstElementChild.cloneNode(true);
    const value = Number(event.temperature);
    card.dataset.temperatureCard = label;
    card.style.setProperty("--t", String(value / sliderMax));
    card.querySelector("[data-card-value]").textContent = label;
    card.querySelector("[data-card-label]").textContent = label;
    card.querySelector("[data-card-badge]").textContent = describeTemperature(value);
    card.querySelector("[data-card-description]").textContent = cardDescription(value);
    card.querySelector("[data-meta='finish']").textContent = "печатает…";
    card.querySelector("[data-answer]").classList.add("is-streaming");

    grid.append(card);
    cards.set(label, card);
    revealBlock(card);
    return card;
  };

  const appendStreamDelta = (event) => {
    if (event.target === "analysis") {
      analysis.hidden = false;
      analysisText.classList.add("is-streaming");
      appendAnswer(analysisText, event.content);
      return;
    }

    const card = cardFor(event.label);
    if (!card) return;
    appendAnswer(card.querySelector("[data-answer]"), event.content);
  };

  const finishAnswer = (event) => {
    const card = cardFor(event.label);
    if (!card) return;
    const output = card.querySelector("[data-answer]");
    setAnswer(output, event.answer?.content || rawAnswer(output) || "Пустой ответ");
    output.classList.remove("is-streaming");
    card.querySelector("[data-meta='tokens']").textContent = event.answer?.output_tokens ?? "—";
    card.querySelector("[data-meta='finish']").textContent = event.answer?.finish_reason || "—";
  };

  const stopStreamingIndicators = () => {
    document.querySelectorAll(".is-streaming").forEach((output) => {
      output.classList.remove("is-streaming");
    });
  };

  promptGeneratorButton.addEventListener("click", async () => {
    setLoading(true);
    promptGeneratorButton.setAttribute("aria-busy", "true");
    status.textContent = "Придумываю запрос…";
    setPromptGenerating(true);
    let promptReady = false;

    try {
      const response = await fetch(promptUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: modelField.value }),
      });
      let data;
      try {
        data = await response.json();
      } catch {
        data = {};
      }
      if (!response.ok) throw new Error(responseError(data));
      if (typeof data?.content !== "string" || !data.content.trim()) {
        throw new Error("Модель не вернула текст запроса");
      }

      promptField.value = data.content.trim();
      status.textContent = "Запрос готов";
      promptReady = true;
    } catch (error) {
      status.textContent = error.message || "Не удалось сгенерировать запрос";
    } finally {
      promptGeneratorButton.setAttribute("aria-busy", "false");
      setPromptGenerating(false);
      setLoading(false);

      if (promptReady) {
        submitButton.focus({ preventScroll: true });
        revealBlock(form.querySelector(".day4-form__actions"));
      }
    }
  });

  const run = async (temperature) => {
    const payload = {
      prompt: promptField.value.trim(),
      model: modelField.value,
      temperature,
    };
    if (!payload.prompt) {
      promptField.focus();
      return;
    }

    resetResults();
    resultsSection.hidden = false;
    setLoading(true);
    status.textContent = "Соединяюсь с моделью…";
    focusResults();
    let analysisFailed = false;

    try {
      await consumeAnswerStream(payload, (streamEvent) => {
        if (streamEvent.type === "answer_start") {
          createCard(streamEvent);
          status.textContent = `Ответ при temperature = ${streamEvent.label}…`;
        } else if (streamEvent.type === "delta") {
          appendStreamDelta(streamEvent);
        } else if (streamEvent.type === "answer_done") {
          finishAnswer(streamEvent);
        } else if (streamEvent.type === "analysis_start") {
          analysis.hidden = false;
          setAnswer(analysisText, "");
          analysisText.classList.add("is-streaming");
          status.textContent = "Формируется мета-анализ…";
          revealBlock(analysis);
        } else if (streamEvent.type === "analysis_done") {
          analysis.hidden = false;
          setAnswer(analysisText, streamEvent.analysis?.content || rawAnswer(analysisText));
          analysisText.classList.remove("is-streaming");
        } else if (streamEvent.type === "analysis_error") {
          analysisFailed = true;
          analysis.hidden = true;
          errorBox.textContent = `Ответы готовы, но мета-анализ не выполнен: ${streamEvent.error}`;
          errorBox.hidden = false;
        }
      });
      status.textContent = analysisFailed ? "Ответы готовы без мета-анализа" : "Готово";
    } catch (error) {
      stopStreamingIndicators();
      errorBox.textContent = error.message || "Не удалось получить ответ";
      errorBox.hidden = false;
      status.textContent = "Ошибка";
    } finally {
      setLoading(false);
    }
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    run(Number(slider.value));
  });

  compareButton.addEventListener("click", () => run("all"));

  const consumeAnswerStream = async (payload, onEvent) => {
    const response = await fetch(streamUrl, {
      method: "POST",
      headers: {
        Accept: "application/x-ndjson",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      let data;
      try {
        data = await response.json();
      } catch {
        data = {};
      }
      throw new Error(responseError(data));
    }
    if (!response.body) throw new Error("Браузер не поддерживает потоковые ответы");

    let completed = false;
    await readNdjson(response.body, (streamEvent) => {
      if (!streamEvent) return;
      if (streamEvent.type === "error") {
        throw new Error(streamEvent.error || "Поток DeepSeek завершился с ошибкой");
      }
      onEvent(streamEvent);
      if (streamEvent.type === "complete") completed = true;
    });
    if (!completed) throw new Error("DeepSeek не завершил поток ответа");
  };

  const readNdjson = async (body, onEvent) => {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      lines.filter((line) => line.trim()).forEach((line) => onEvent(JSON.parse(line)));
      if (done) break;
    }
    if (buffer.trim()) onEvent(JSON.parse(buffer));
  };

  const responseError = (payload) => {
    if (typeof payload?.error === "string") return payload.error;
    if (typeof payload?.error?.message === "string") return payload.error.message;
    if (typeof payload?.details === "string") return payload.details;
    return "Не удалось получить ответ";
  };

  const focusResults = () => {
    resultsSection.hidden = false;
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
    resultsSection.focus({ preventScroll: true });

    window.requestAnimationFrame(() => {
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      resultsSection.scrollIntoView({
        behavior: reduceMotion ? "auto" : "smooth",
        block: "start",
      });
    });
  };

  promptField.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
})();
