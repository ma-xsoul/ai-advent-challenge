"""
AI-ревью pull request'а — день 32. Его запускает GitHub Actions
(.github/workflows/ai-review.yml), но работает и локально:

    .venv/bin/python scripts/ai_review.py --base day-30 --head day-31

Пишет markdown-комментарий в --output (по умолчанию review.md) и, если
есть, в сводку шага GitHub ($GITHUB_STEP_SUMMARY). Без DEEPSEEK_API_KEY
ревью не будет — комментарий скажет, какой секрет добавить.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from advent.agent.transport import DeepSeekTransport, TransportError  # noqa: E402
from advent.rag.review import MARKER, LexicalRetriever, ReviewError, VectorRetriever, run, to_markdown  # noqa: E402


def retriever(kind: str):
    if kind == "vector":
        from advent.rag import OllamaEmbedder, RagIndex
        from advent.rag.assistant import DEFAULT_INDEX

        return VectorRetriever(RagIndex(DEFAULT_INDEX), OllamaEmbedder())
    return LexicalRetriever.from_project(ROOT)


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="AI-ревью изменений ветки")
    parser.add_argument("--base", required=True, help="с чем сравнивать: ветка или коммит, например origin/main")
    parser.add_argument("--head", default="HEAD", help="что ревьюим")
    parser.add_argument("--retriever", choices=("lexical", "vector"), default="lexical", help="поиск по словам (GitHub) или векторный (нужен Ollama)")
    parser.add_argument("--output", default="review.md")
    parser.add_argument("--json", help="сохранить ревью целиком в JSON")
    args = parser.parse_args()

    link_base = os.getenv("REVIEW_LINK_BASE")
    link = (lambda file, line: f"{link_base}/{file}" + (f"#L{line}" if line else "")) if link_base else None
    output = Path(args.output)
    code = 0
    if not os.getenv("DEEPSEEK_API_KEY"):
        body = f"{MARKER}\n## 🤖 AI-ревью\n\nНе запустилось: нет секрета `DEEPSEEK_API_KEY`. Добавьте его в Settings → Secrets and variables → Actions."
    else:
        try:
            result = run(args.base, args.head, transport=DeepSeekTransport(), retriever=retriever(args.retriever), root=ROOT,
                         emit=lambda event: print(f"· {event['type']}", flush=True))
            body = to_markdown(result, link=link)
            if args.json:
                Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        except (ReviewError, TransportError) as error:
            body = f"{MARKER}\n## 🤖 AI-ревью\n\nНе получилось: {error}"
            code = 1
    output.write_text(body, encoding="utf-8")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(body.replace(MARKER, "") + "\n")
    print(body)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
