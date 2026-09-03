(() => {
  const form = document.querySelector("#reasoning-form");
  if (!form) return;

  const taskField = document.querySelector("#reasoning-task");
  const modelField = document.querySelector("#day3-model");
  const approachField = document.querySelector("#reasoning-approach");
  const taskGeneratorButton = document.querySelector("#task-generator");
  const taskInput = document.querySelector(".day3-task-input");
  const taskLoading = document.querySelector("#task-loading");
  const submitButton = document.querySelector("#reasoning-submit");
  const status = document.querySelector("#reasoning-status");
  const resultsSection = document.querySelector("#reasoning-results");
  const errorBox = document.querySelector("#reasoning-error");
  const analysis = document.querySelector("#reasoning-analysis");
  const analysisText = document.querySelector("#reasoning-analysis-text");
  const selectRoots = [...document.querySelectorAll("[data-day3-select]")];
  const streamUrl = `${form.dataset.solveUrl}/stream`;
  const taskUrl = form.dataset.taskUrl;
  const approachLabels = {
    direct: "ответ без инструкции",
    step_by_step: "пошаговое решение",
    generated_prompt: "решение по промпту модели",
    experts: "решение группы экспертов",
  };

  // Ответ приходит потоком, поэтому сырой текст храним отдельно от разметки
  // и перерисовываем не чаще одного раза за кадр.
  const rawAnswers = new WeakMap();
  const pendingRender = new Set();
  let renderFrame = null;

  const renderRaw = (output) => {
    const raw = rawAnswers.get(output) || "";

    if (typeof window.renderMarkdown === "function") {
      output.innerHTML = window.renderMarkdown(raw);
    } else {
      output.textContent = raw;
    }
  };

  // Пока идёт поток, страница держит нижний край ответа в поле зрения.
  const FOLLOW_MARGIN = 90;
  let followTarget = null;
  let followEnabled = false;

  // Свёрнутое решение не годится как ориентир: браузер оставляет закрытому
  // <details> и размеры, и offsetParent, поэтому смотрим на сам признак open.
  const followTargetVisible = () => {
    if (!followTarget) return false;
    const fold = followTarget.closest("details");
    if (fold && !fold.open) return false;
    return followTarget.offsetParent !== null;
  };

  const nearTargetBottom = () => {
    if (!followTargetVisible()) return false;
    return followTarget.getBoundingClientRect().bottom - window.innerHeight < 160;
  };

  // Запоминаем позицию, которую поставили сами: всё остальное — это человек.
  let lastAutoScroll = -1;

  const followBottom = () => {
    if (!followEnabled || !followTarget) return;
    if (!followTargetVisible()) return;
    const overflow =
      followTarget.getBoundingClientRect().bottom - (window.innerHeight - FOLLOW_MARGIN);
    if (overflow <= 1) return;

    window.scrollBy({ top: overflow, behavior: "auto" });
    lastAutoScroll = Math.round(window.scrollY);
  };

  const startFollowing = (element) => {
    followTarget = element;
    followEnabled = true;
  };

  // Ручная прокрутка отменяет ведение; возврат к низу ответа включает его снова.
  const syncFollowToUser = () => {
    if (followTarget) followEnabled = nearTargetBottom();
  };

  window.addEventListener(
    "scroll",
    () => {
      if (Math.abs(Math.round(window.scrollY) - lastAutoScroll) > 2) syncFollowToUser();
    },
    { passive: true },
  );
  window.addEventListener("wheel", syncFollowToUser, { passive: true });
  window.addEventListener("touchmove", syncFollowToUser, { passive: true });

  const scheduleRender = (output) => {
    pendingRender.add(output);
    if (renderFrame !== null) return;
    renderFrame = window.requestAnimationFrame(() => {
      renderFrame = null;
      pendingRender.forEach(renderRaw);
      pendingRender.clear();
      followBottom();
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

  // Показываем блок, который начал печатать: block "nearest" не дёргает
  // страницу, если он и так на виду.
  const revealBlock = (element) => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    element.scrollIntoView({
      behavior: reduceMotion ? "auto" : "smooth",
      block: "nearest",
    });
  };

  const closeSelect = (root) => {
    root.classList.remove("is-open");
    root.querySelector(".day3-select__trigger").setAttribute("aria-expanded", "false");
    root.querySelector(".day3-select__menu").hidden = true;
  };

  const closeOtherSelects = (activeRoot) => {
    selectRoots.forEach((root) => {
      if (root !== activeRoot) closeSelect(root);
    });
  };

  selectRoots.forEach((root) => {
    const input = root.querySelector("input[type='hidden']");
    const trigger = root.querySelector(".day3-select__trigger");
    const value = trigger.querySelector("span:first-child");
    const menu = root.querySelector(".day3-select__menu");
    const options = [...root.querySelectorAll(".day3-select__option")];

    const openSelect = () => {
      closeOtherSelects(root);
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
      input.dispatchEvent(new Event("change", { bubbles: true }));
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

  // На время генерации поле показывает надпись вместо подсказки-примера.
  const setTaskGenerating = (isGenerating) => {
    taskLoading.hidden = !isGenerating;
    taskInput.classList.toggle("is-generating", isGenerating);
  };

  const setLoading = (isLoading) => {
    taskField.disabled = isLoading;
    taskGeneratorButton.disabled = isLoading;
    submitButton.disabled = isLoading;
    selectRoots.forEach((root) => {
      root.querySelector(".day3-select__trigger").disabled = isLoading;
      if (isLoading) closeSelect(root);
    });
  };

  const resetResults = () => {
    errorBox.hidden = true;
    errorBox.textContent = "";
    analysis.hidden = true;
    setAnswer(analysisText, "");
    analysisText.classList.remove("is-streaming");
    document.querySelectorAll("[data-approach-card]").forEach((card) => {
      card.hidden = true;
      setAnswer(card.querySelector("[data-answer]"), "");
      card.querySelector("[data-answer]").classList.remove("is-streaming");
      card.querySelector("[data-meta='tokens']").textContent = "—";
      card.querySelector("[data-meta='finish']").textContent = "—";
      const answerFold = card.querySelector(".answer-fold");
      if (answerFold) answerFold.open = true;
      const promptBlock = card.querySelector("[data-prompt-block]");
      if (promptBlock) {
        promptBlock.hidden = true;
        promptBlock.querySelector("[data-system-prompt]").textContent = "";
      }
      const generatedPrompt = card.querySelector(".generated-prompt");
      if (generatedPrompt) {
        generatedPrompt.hidden = true;
        generatedPrompt.open = false;
        generatedPrompt.querySelector("[data-generated-prompt]").textContent = "";
        generatedPrompt.querySelector("[data-generated-prompt]").classList.remove("is-streaming");
      }
    });
  };

  const setPromptBlock = (card, text) => {
    const block = card.querySelector("[data-prompt-block]");
    if (!block) return;
    const target = block.querySelector("[data-system-prompt]");

    target.textContent = text || "— системный промпт не передаётся —";
    target.classList.toggle("is-empty", !text);
    block.hidden = false;
  };

  const showApproach = (event) => {
    const approach = event.approach;
    const card = document.querySelector(`[data-approach-card="${approach}"]`);
    if (!card) return;
    card.hidden = false;
    if (approach === "generated_prompt") {
      card.querySelector("[data-meta='finish']").textContent = "готовится промпт…";
    } else {
      setPromptBlock(card, event.system_prompt);
      card.querySelector("[data-meta='finish']").textContent = "печатает…";
      card.querySelector("[data-answer]").classList.add("is-streaming");
    }
    revealBlock(card);
    startFollowing(card.querySelector("[data-answer]"));
  };

  const appendStreamDelta = (event) => {
    let output;
    if (event.target === "analysis") {
      analysis.hidden = false;
      output = analysisText;
    } else {
      const card = document.querySelector(`[data-approach-card="${event.approach}"]`);
      if (!card) return;
      if (event.target === "generated_prompt") {
        const details = card.querySelector(".generated-prompt");
        details.hidden = false;
        details.open = true;
        output = details.querySelector("[data-generated-prompt]");
      } else {
        output = card.querySelector("[data-answer]");
      }
    }

    output.classList.add("is-streaming");

    if (event.target === "generated_prompt") {
      output.textContent += event.content;
      output.scrollTop = output.scrollHeight;
      return;
    }
    appendAnswer(output, event.content);
  };

  const finishGeneratedPrompt = (event) => {
    const card = document.querySelector(`[data-approach-card="${event.approach}"]`);
    const details = card?.querySelector(".generated-prompt");
    if (!details) return;
    const output = details.querySelector("[data-generated-prompt]");
    output.textContent = event.prompt?.content || output.textContent;
    output.classList.remove("is-streaming");
    card.querySelector("[data-answer]").classList.add("is-streaming");
    card.querySelector("[data-meta='finish']").textContent = "печатает…";
  };

  const finishApproach = (event) => {
    const card = document.querySelector(`[data-approach-card="${event.approach}"]`);
    if (!card) return;
    const output = card.querySelector("[data-answer]");
    setAnswer(output, event.answer?.content || rawAnswer(output) || "Пустой ответ");
    output.classList.remove("is-streaming");
    card.querySelector("[data-meta='tokens']").textContent = event.answer?.output_tokens ?? "—";
    card.querySelector("[data-meta='finish']").textContent = event.answer?.finish_reason || "—";
  };

  const finishAnalysis = (event) => {
    analysis.hidden = false;
    setAnswer(analysisText, event.analysis?.content || rawAnswer(analysisText));
    analysisText.classList.remove("is-streaming");
  };

  const stopStreamingIndicators = () => {
    document.querySelectorAll(".is-streaming").forEach((output) => {
      output.classList.remove("is-streaming");
    });
  };

  taskGeneratorButton.addEventListener("click", async () => {
    setLoading(true);
    taskGeneratorButton.setAttribute("aria-busy", "true");
    status.textContent = "Генерирую задачу…";
    setTaskGenerating(true);
    let taskReady = false;

    try {
      const response = await fetch(taskUrl, {
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
        throw new Error("Модель не вернула условие задачи");
      }

      taskField.value = data.content.trim();
      taskField.dispatchEvent(new Event("input", { bubbles: true }));
      status.textContent = "Задача готова";
      taskReady = true;
    } catch (error) {
      status.textContent = error.message || "Не удалось сгенерировать задачу";
    } finally {
      taskGeneratorButton.setAttribute("aria-busy", "false");
      setTaskGenerating(false);
      setLoading(false);

      // Кнопка снова доступна только после setLoading(false), поэтому фокус здесь.
      if (taskReady) {
        submitButton.focus({ preventScroll: true });
        revealBlock(form.querySelector(".day3-form__actions"));
      }
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const payload = {
      task: taskField.value.trim(),
      model: modelField.value,
      approach: approachField.value,
    };
    if (!payload.task) {
      taskField.focus();
      return;
    }

    resetResults();
    resultsSection.hidden = false;
    setLoading(true);
    status.textContent = "Соединяюсь с моделью…";
    focusResults();
    let analysisFailed = false;

    try {
      await consumeSolutionStream(payload, (streamEvent) => {
        if (streamEvent.type === "approach_start") {
          showApproach(streamEvent);
          status.textContent = `Формируется ${approachLabels[streamEvent.approach]}…`;
        } else if (streamEvent.type === "generated_prompt_start") {
          const card = document.querySelector('[data-approach-card="generated_prompt"]');
          if (card) setPromptBlock(card, streamEvent.system_prompt);
          status.textContent = "Сначала модель составляет промпт…";
        } else if (streamEvent.type === "delta") {
          appendStreamDelta(streamEvent);
        } else if (streamEvent.type === "generated_prompt_done") {
          finishGeneratedPrompt(streamEvent);
          status.textContent = "Промпт готов, модель решает задачу…";
        } else if (streamEvent.type === "approach_done") {
          finishApproach(streamEvent);
        } else if (streamEvent.type === "analysis_start") {
          analysis.hidden = false;
          setAnswer(analysisText, "");
          analysisText.classList.add("is-streaming");
          status.textContent = "Формируется мета-анализ…";
          revealBlock(analysis);
          startFollowing(analysisText);
        } else if (streamEvent.type === "analysis_done") {
          finishAnalysis(streamEvent);
        } else if (streamEvent.type === "analysis_error") {
          analysisFailed = true;
          analysis.hidden = true;
          errorBox.textContent = `Решения готовы, но мета-анализ не выполнен: ${streamEvent.error}`;
          errorBox.hidden = false;
        }
      });
      status.textContent = analysisFailed ? "Решения готовы без мета-анализа" : "Готово";
    } catch (error) {
      stopStreamingIndicators();
      errorBox.textContent = error.message || "Не удалось получить решения";
      errorBox.hidden = false;
      status.textContent = "Ошибка";
    } finally {
      setLoading(false);
      followEnabled = false;
      followTarget = null;
    }
  });

  const consumeSolutionStream = async (payload, onEvent) => {
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
    return "Не удалось получить решения";
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

  taskField.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
})();
