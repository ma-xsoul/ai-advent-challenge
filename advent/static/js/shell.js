/*
 * Оболочка: кнопка «сбросить всё» в боковой панели.
 * Сносит все файлы сессий и память процесса, потом перезагружает страницу —
 * чистый лист перед записью видео без rm -rf руками.
 */
(() => {
  const button = document.querySelector("#reset-all");
  const status = document.querySelector("#reset-all-status");
  if (!button) return;

  button.addEventListener("click", async () => {
    if (!window.confirm("Удалить все диалоги, слои памяти, профили и задачи?")) return;
    button.disabled = true;
    if (status) status.textContent = "чищу…";
    try {
      const response = await fetch(button.dataset.resetAllUrl, { method: "POST" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.error || "Не удалось сбросить");
      if (status) status.textContent = `удалено файлов: ${data.files ?? 0}`;
      window.setTimeout(() => window.location.reload(), 350);
    } catch (error) {
      if (status) status.textContent = error.message || "Не удалось сбросить";
      button.disabled = false;
    }
  });
})();
