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
    if (!window.confirm("Удалить все диалоги, слои памяти, профили, задачи, данные дня 18 и отчёты дней 19–20?")) return;
    button.disabled = true;
    if (status) status.textContent = "чищу…";
    try {
      const response = await fetch(button.dataset.resetAllUrl, { method: "POST" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.error || "Не удалось сбросить");
      const day18 = data.day18 || {};
      const cleared = (day18.summaries || 0) + (day18.ticks || 0) + (day18.snapshots || 0);
      const reports = (data.day19_reports || 0) + (data.day20_reports || 0);
      if (status) {
        status.textContent =
          `удалено файлов: ${data.files ?? 0}` +
          (cleared ? ` · записей дня 18: ${cleared}` : "") +
          (reports ? ` · отчётов: ${reports}` : "");
      }
      window.setTimeout(() => window.location.reload(), 350);
    } catch (error) {
      if (status) status.textContent = error.message || "Не удалось сбросить";
      button.disabled = false;
    }
  });
})();
