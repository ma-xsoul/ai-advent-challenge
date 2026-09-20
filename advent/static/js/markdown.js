/*
 * Минимальный рендерер Markdown для ответов модели.
 * Весь текст экранируется до разбора, поэтому разметка из ответа
 * не может превратиться в исполняемый HTML.
 */
(() => {
  const HTML_ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  };

  const escapeHtml = (text) => String(text).replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch]);

  // Пропускаем только безопасные схемы: javascript: и data: не должны стать ссылкой.
  const safeUrl = (url) => {
    const trimmed = url.trim();
    return /^(https?:\/\/|mailto:|\/|#)/i.test(trimmed) ? escapeHtml(trimmed) : "";
  };

  const renderInline = (raw) => {
    const codeSpans = [];
    let html = escapeHtml(raw).replace(/`([^`\n]+)`/g, (_, code) => {
      codeSpans.push(`<code>${code}</code>`);
      return `\u0000${codeSpans.length - 1}\u0000`;
    });

    html = html
      .replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (match, label, url) => {
        const href = safeUrl(url);
        return href ? `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>` : match;
      })
      .replace(/\*\*([^\n]+?)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^\n]+?)__/g, "<strong>$1</strong>")
      .replace(/~~([^\n]+?)~~/g, "<del>$1</del>")
      .replace(/(^|[^*\w])\*([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>")
      .replace(/(^|[^_\w])_([^_\n]+?)_(?![\w_])/g, "$1<em>$2</em>");

    return html.replace(/\u0000(\d+)\u0000/g, (_, index) => codeSpans[Number(index)]);
  };

  const FENCE = /^\s*```+\s*[\w+-]*\s*$/;
  const HEADING = /^\s{0,3}(#{1,6})\s+(.*)$/;
  const QUOTE = /^\s{0,3}>\s?/;
  const BULLET = /^\s{0,3}[-*+]\s+/;
  const ORDERED = /^\s{0,3}\d{1,9}[.)]\s+/;
  const RULE = /^\s*([-*_])\s*(\1\s*){2,}$/;

  const startsBlock = (line) =>
    FENCE.test(line) ||
    HEADING.test(line) ||
    QUOTE.test(line) ||
    BULLET.test(line) ||
    ORDERED.test(line) ||
    RULE.test(line);

  const isTableSeparator = (line) =>
    /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);

  const splitRow = (line) =>
    line
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((cell) => cell.trim());

  const renderMarkdown = (text) => {
    const lines = String(text ?? "").replace(/\r\n?/g, "\n").split("\n");
    const blocks = [];
    let index = 0;

    while (index < lines.length) {
      const line = lines[index];

      if (FENCE.test(line)) {
        const body = [];
        index += 1;
        while (index < lines.length && !/^\s*```+\s*$/.test(lines[index])) {
          body.push(lines[index]);
          index += 1;
        }
        index += 1;
        blocks.push(`<pre class="md-code"><code>${escapeHtml(body.join("\n"))}</code></pre>`);
        continue;
      }

      if (!line.trim()) {
        index += 1;
        continue;
      }

      if (RULE.test(line)) {
        blocks.push("<hr>");
        index += 1;
        continue;
      }

      const heading = line.match(HEADING);
      if (heading) {
        // Заголовки ответа не должны конкурировать с заголовками страницы.
        const level = Math.min(heading[1].length + 3, 6);
        blocks.push(`<h${level}>${renderInline(heading[2].trim())}</h${level}>`);
        index += 1;
        continue;
      }

      if (line.includes("|") && index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
        const head = splitRow(line);
        index += 2;
        const rows = [];
        while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
          rows.push(splitRow(lines[index]));
          index += 1;
        }
        const headHtml = head.map((cell) => `<th>${renderInline(cell)}</th>`).join("");
        const bodyHtml = rows
          .map((row) => `<tr>${row.map((cell) => `<td>${renderInline(cell)}</td>`).join("")}</tr>`)
          .join("");
        blocks.push(
          `<div class="md-table-scroll"><table class="md-table">` +
            `<thead><tr>${headHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`,
        );
        continue;
      }

      if (QUOTE.test(line)) {
        const body = [];
        while (index < lines.length && QUOTE.test(lines[index])) {
          body.push(lines[index].replace(QUOTE, ""));
          index += 1;
        }
        blocks.push(`<blockquote>${renderMarkdown(body.join("\n"))}</blockquote>`);
        continue;
      }

      const bullet = BULLET.test(line);
      if (bullet || ORDERED.test(line)) {
        const marker = bullet ? BULLET : ORDERED;
        const items = [];
        while (index < lines.length) {
          if (!marker.test(lines[index])) {
            // Модели любят разделять пункты пустой строкой. Раньше на ней
            // список обрывался, и каждый следующий пункт открывал новый
            // <ol> — нумерация шла «1, 1, 1». Смотрим за пустые строки:
            // если дальше снова маркер того же списка, это тот же список.
            let look = index;
            while (look < lines.length && !lines[look].trim()) look += 1;
            if (look > index && look < lines.length && marker.test(lines[look])) {
              index = look;
              continue;
            }
            break;
          }
          const item = [lines[index].replace(marker, "")];
          index += 1;
          // Продолжение пункта: строки без собственного маркера.
          while (index < lines.length && lines[index].trim() && !startsBlock(lines[index])) {
            item.push(lines[index].trim());
            index += 1;
          }
          items.push(`<li>${renderInline(item.join(" "))}</li>`);
        }
        blocks.push(bullet ? `<ul>${items.join("")}</ul>` : `<ol>${items.join("")}</ol>`);
        continue;
      }

      const paragraph = [];
      while (index < lines.length && lines[index].trim() && !startsBlock(lines[index])) {
        paragraph.push(lines[index]);
        index += 1;
      }
      if (paragraph.length) {
        blocks.push(`<p>${renderInline(paragraph.join("\n")).replace(/\n/g, "<br>")}</p>`);
      } else {
        index += 1;
      }
    }

    return blocks.join("");
  };

  window.renderMarkdown = renderMarkdown;
})();
