/*
 * Свой выпадающий список — дни 2 и 26. Нативный <select> рисует система,
 * и в терминальную тему он не попадает, поэтому список свой: кнопка-триггер,
 * меню role="listbox", значение — в скрытом input. Выбор шлёт событие change
 * у этого input, как обычное поле формы.
 *
 * Разметка:
 *   <div class="custom-select" data-custom-select>
 *     <input type="hidden" name="…" value="…">
 *     <button class="custom-select__trigger" aria-haspopup="listbox" aria-expanded="false">
 *       <span>подпись</span><span class="custom-select__chevron"></span>
 *     </button>
 *     <div class="custom-select__menu" role="listbox" hidden>
 *       <button class="custom-select__option" role="option" data-value="…" data-label="…" aria-selected="…">…</button>
 *     </div>
 *   </div>
 */
(() => {
  const controls = new Set();
  const parts = (control) => ({
    input: control.querySelector('input[type="hidden"]'),
    trigger: control.querySelector(".custom-select__trigger"),
    menu: control.querySelector(".custom-select__menu"),
    options: [...control.querySelectorAll(".custom-select__option")],
  });

  function open(control) {
    const { trigger, menu } = parts(control);
    if (trigger.disabled) return;
    menu.hidden = false;
    control.classList.add("is-open");
    trigger.setAttribute("aria-expanded", "true");
  }

  function close(control) {
    const { trigger, menu } = parts(control);
    menu.hidden = true;
    control.classList.remove("is-open");
    trigger.setAttribute("aria-expanded", "false");
  }

  function select(control, value, dispatch = true) {
    const { input, trigger, options } = parts(control);
    const option = options.find((item) => item.dataset.value === value) || options[0];
    if (!option) return;
    input.value = option.dataset.value;
    trigger.querySelector("span:first-child").textContent = option.dataset.label;
    options.forEach((item) => item.setAttribute("aria-selected", String(item === option)));
    close(control);
    if (dispatch) input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function reset(control) {
    const { input } = parts(control);
    input.value = input.defaultValue;
    select(control, input.value, false);
  }

  function setDisabled(control, disabled, disabledLabel = "") {
    const { input, trigger } = parts(control);
    input.disabled = disabled;
    trigger.disabled = disabled;
    trigger.setAttribute("aria-disabled", String(disabled));
    control.classList.toggle("is-disabled", disabled);
    close(control);
    if (disabled && disabledLabel) trigger.querySelector("span:first-child").textContent = disabledLabel;
    else if (!disabled) select(control, input.value, false);
  }

  function init(control) {
    if (controls.has(control)) return;
    controls.add(control);
    const { trigger, menu, options } = parts(control);

    trigger.addEventListener("click", () => {
      if (trigger.disabled) return;
      const shouldOpen = menu.hidden;
      controls.forEach(close);
      if (shouldOpen) open(control);
    });

    options.forEach((option) => {
      option.addEventListener("click", () => {
        select(control, option.dataset.value);
        trigger.focus();
      });
    });

    control.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        close(control);
        trigger.focus();
        return;
      }
      if (event.target === trigger && ["ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        open(control);
        options.find((option) => option.getAttribute("aria-selected") === "true")?.focus();
        return;
      }
      if (!menu.hidden && ["ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        const index = options.indexOf(document.activeElement);
        const step = event.key === "ArrowDown" ? 1 : -1;
        options[(index + step + options.length) % options.length].focus();
      }
    });
  }

  // Клик мимо закрывает все открытые списки страницы.
  document.addEventListener("click", (event) => {
    controls.forEach((control) => {
      if (!control.contains(event.target)) close(control);
    });
  });

  window.CustomSelect = { init, open, close, select, reset, setDisabled };
})();
