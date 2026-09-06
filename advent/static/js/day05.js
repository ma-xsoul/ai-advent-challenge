(() => {
  const form = document.querySelector("#models-form");
  if (!form) return;

  const promptField = document.querySelector("#models-prompt");
  const promptGeneratorButton = document.querySelector("#prompt-generator");
  const promptInput = document.querySelector(".day5-prompt-input");
  const promptLoading = document.querySelector("#prompt-loading");
  const submitButton = document.querySelector("#models-submit");
  const status = document.querySelector("#models-status");
  const resultsSection = document.querySelector("#models-results");
  const grid = document.querySelector("#models-grid");
  const errorBox = document.querySelector("#models-error");
  const analysis = document.querySelector("#models-analysis");
  const analysisText = document.querySelector("#models-analysis-text");
  const cardTemplate = document.querySelector("#models-card-template");
  const ladderItems = [...document.querySelectorAll("[data-ladder-tier]")];
  const promptUrl = form.dataset.promptUrl;
  const compareUrl = form.dataset.compareUrl;

  const rawAnswers = new WeakMap();
  const pendingRender = new Set();
  const cards = new Map();
  let renderFrame = null;

  /*
   * Пока идёт поток, страница сама держит нижний край печатающегося блока в
   * поле зрения: от нажатия кнопки до мета-анализа руки не нужны. Ручная
   * прокрутка ведение отменяет, возврат к низу — включает снова.
   */
  const FOLLOW_MARGIN = 90;
  let followTarget = null;
  let followEnabled = false;
  let lastAutoScroll = -1;

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

  const followBottom = () => {
    if (!followEnabled || !followTarget || !followTargetVisible()) return;
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

  const stopFollowing = () => {
    followEnabled = false;
    followTarget = null;
  };

  // Прокрутку, которую сделали мы сами, за действие человека не считаем.
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

  const revealBlock = (element) => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    element.scrollIntoView({
      behavior: reduceMotion ? "auto" : "smooth",
      block: "nearest",
    });
  };

  // Цена приходит долями цента, обычные два знака превратили бы её в ноль.
  const formatCost = (cost) => {
    if (typeof cost !== "number") return "—";
    if (cost === 0) return "$0";
    if (cost < 0.01) return `$${cost.toFixed(6)}`;
    return `$${cost.toFixed(4)}`;
  };

  const setPromptGenerating = (isGenerating) => {
    promptLoading.hidden = !isGenerating;
    promptInput.classList.toggle("is-generating", isGenerating);
  };

  const setLoading = (isLoading) => {
    form.classList.toggle("is-busy", isLoading);
    promptField.disabled = isLoading;
    promptGeneratorButton.disabled = isLoading;
    submitButton.disabled = isLoading;
  };

  const markLadder = (tier, state) => {
    ladderItems.forEach((item) => {
      if (item.dataset.ladderTier !== tier) return;
      item.classList.toggle("is-running", state === "running");
      item.classList.toggle("is-done", state === "done");
      item.classList.toggle("is-failed", state === "failed");
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
    ladderItems.forEach((item) => item.classList.remove("is-running", "is-done", "is-failed"));
  };

  const createCard = (event) => {
    if (cards.has(event.tier)) return cards.get(event.tier);

    const card = cardTemplate.content.firstElementChild.cloneNode(true);
    card.dataset.modelCard = event.tier;
    card.dataset.tier = event.tier;
    card.querySelector("[data-card-level]").textContent = event.label;
    card.querySelector("[data-card-slug]").textContent = event.slug;
    card.querySelector("[data-card-reasoning]").hidden = !event.reasoning;
    card.querySelector("[data-meta='finish']").textContent = "печатает…";
    card.querySelector("[data-answer]").classList.add("is-streaming");

    grid.append(card);
    cards.set(event.tier, card);
    revealBlock(card);
    startFollowing(card.querySelector("[data-answer]"));
    return card;
  };

  /*
   * Рассуждение показываем живьём: у старшей модели оно идёт первые несколько
   * секунд, и без него карточка выглядит зависшей. Как только пошёл сам ответ,
   * блок сворачивается, чтобы не заслонять результат.
   */
  const appendReasoning = (event) => {
    const card = cards.get(event.tier);
    if (!card) return;
    const fold = card.querySelector("[data-reasoning-fold]");
    const output = card.querySelector("[data-reasoning]");
    if (!fold || !output) return;

    if (fold.hidden) {
      fold.hidden = false;
      fold.open = true;
      card.querySelector("[data-meta='finish']").textContent = "размышляет…";
      startFollowing(output);
    }
    output.textContent += event.content;
    output.scrollTop = output.scrollHeight;
    card.querySelector("[data-reasoning-count]").textContent =
      `${output.textContent.length} символов`;
  };

  const collapseReasoning = (card) => {
    const fold = card.querySelector("[data-reasoning-fold]");
    if (fold && !fold.hidden && fold.open) fold.open = false;
  };

  const finishCard = (event) => {
    const card = cards.get(event.tier);
    if (!card) return;
    const answer = event.answer || {};
    const output = card.querySelector("[data-answer]");

    setAnswer(output, answer.content || rawAnswer(output) || "Пустой ответ");
    output.classList.remove("is-streaming");
    collapseReasoning(card);
    card.querySelector("[data-meta='seconds']").textContent =
      typeof answer.seconds === "number" ? `${answer.seconds} с` : "—";
    card.querySelector("[data-meta='first']").textContent =
      typeof answer.first_token_seconds === "number" ? `${answer.first_token_seconds} с` : "—";
    card.querySelector("[data-meta='tokens']").textContent =
      answer.input_tokens != null || answer.output_tokens != null
        ? `${answer.input_tokens ?? "—"} → ${answer.output_tokens ?? "—"}`
        : "—";
    card.querySelector("[data-meta='cost']").textContent = formatCost(answer.cost);
    card.querySelector("[data-meta='finish']").textContent = answer.finish_reason || "—";
  };

  const failCard = (event) => {
    const card = cards.get(event.tier);
    if (!card) return;
    const output = card.querySelector("[data-answer]");
    output.classList.remove("is-streaming");
    card.classList.add("is-failed");
    card.querySelector("[data-meta='finish']").textContent = "ошибка";
    if (!rawAnswer(output)) setAnswer(output, `Модель не ответила: ${event.error}`);
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
        body: JSON.stringify({ model: "deepseek-v4-flash" }),
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
        revealBlock(form.querySelector(".day5-form__actions"));
      }
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const prompt = promptField.value.trim();
    if (!prompt) {
      promptField.focus();
      return;
    }

    resetResults();
    resultsSection.hidden = false;
    setLoading(true);
    status.textContent = "Соединяюсь с OpenRouter…";
    focusResults();
    let analysisFailed = false;

    try {
      await consumeStream({ prompt }, (streamEvent) => {
        if (streamEvent.type === "model_start") {
          createCard(streamEvent);
          markLadder(streamEvent.tier, "running");
          status.textContent = `${streamEvent.label} модель: ${streamEvent.slug}…`;
        } else if (streamEvent.type === "delta") {
          // Дельты мета-анализа приходят без tier — их различает target.
          if (streamEvent.target === "analysis") {
            analysis.hidden = false;
            analysisText.classList.add("is-streaming");
            appendAnswer(analysisText, streamEvent.content);
          } else if (streamEvent.target === "reasoning") {
            appendReasoning(streamEvent);
          } else {
            const card = cards.get(streamEvent.tier);
            if (!card) return;
            const output = card.querySelector("[data-answer]");
            if (!rawAnswer(output)) {
              collapseReasoning(card);
              card.querySelector("[data-meta='finish']").textContent = "печатает…";
              startFollowing(output);
            }
            appendAnswer(output, streamEvent.content);
          }
        } else if (streamEvent.type === "model_done") {
          finishCard(streamEvent);
          markLadder(streamEvent.tier, "done");
        } else if (streamEvent.type === "model_error") {
          failCard(streamEvent);
          markLadder(streamEvent.tier, "failed");
        } else if (streamEvent.type === "analysis_start") {
          analysis.hidden = false;
          setAnswer(analysisText, "");
          analysisText.classList.add("is-streaming");
          status.textContent = "Формируется мета-анализ…";
          revealBlock(analysis);
          startFollowing(analysisText);
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
      errorBox.textContent = error.message || "Не удалось получить ответы";
      errorBox.hidden = false;
      status.textContent = "Ошибка";
    } finally {
      setLoading(false);
      stopFollowing();
    }
  });

  const consumeStream = async (payload, onEvent) => {
    const response = await fetch(compareUrl, {
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
        throw new Error(streamEvent.error || "Поток завершился с ошибкой");
      }
      onEvent(streamEvent);
      if (streamEvent.type === "complete") completed = true;
    });
    if (!completed) throw new Error("Поток не был завершён");
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
    return "Не удалось получить ответы";
  };

  const focusResults = () => {
    resultsSection.hidden = false;
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
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
