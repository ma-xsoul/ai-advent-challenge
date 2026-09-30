"""
python -m advent.scheduler <команда>

  tick       — один запуск по расписанию: снять курс, при необходимости сводка (его вызывает cron)
  summary    — сводка прямо сейчас, не дожидаясь расписания
  install    — поставить строку в crontab (по умолчанию: тик и сводка раз в минуту — режим для видео)
  uninstall  — убрать нашу строку из crontab
  status     — что стоит в crontab
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from dotenv import load_dotenv

from ..mcp.client import PROJECT_ROOT
from . import DATA_DIR, SUMMARY_EVERY, TICK_EVERY, Paths, summarize, tick
from . import cron
from .inbox import Inbox


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m advent.scheduler", description="Расписание дня 18")
    commands = parser.add_subparsers(dest="command", required=True)

    tick_parser = commands.add_parser("tick", help="снять курс и, если пора, прислать сводку")
    tick_parser.add_argument("--summary-every", type=int, default=SUMMARY_EVERY)

    summary_parser = commands.add_parser("summary", help="сводка прямо сейчас")
    summary_parser.add_argument("--minutes", type=int, default=SUMMARY_EVERY)

    install_parser = commands.add_parser("install", help="поставить строку в crontab")
    install_parser.add_argument("--every", type=int, default=TICK_EVERY)
    install_parser.add_argument("--summary-every", type=int, default=SUMMARY_EVERY)

    commands.add_parser("uninstall", help="убрать строку из crontab")
    commands.add_parser("status", help="что стоит в crontab")

    args = parser.parse_args(argv)
    # cron запускает процесс с пустым окружением — ключ DeepSeek берём из .env сами.
    load_dotenv(PROJECT_ROOT / ".env")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if args.command == "tick":
        result = tick(Inbox(Paths.under(DATA_DIR).inbox), summary_every=args.summary_every)
        record, summary = result["tick"], result["summary"]
        rate = f"курс {record['price']}" if record["ok"] else f"ошибка: {record['error']}"
        sent = f"сводка #{summary['id']} во входящих" if summary else "сводка не нужна"
        print(f"{stamp} tick · {rate} · {record['ms']} мс · {sent}", flush=True)
        return 0 if record["ok"] else 1

    if args.command == "summary":
        summary = summarize(Inbox(Paths.under(DATA_DIR).inbox), minutes=args.minutes, trigger="manual")
        print(f"{stamp} summary #{summary['id']} · {'ok' if summary['ok'] else 'ошибка'}\n{summary['text']}", flush=True)
        return 0 if summary["ok"] else 1

    if args.command == "install":
        line = cron.install(args.every, args.summary_every, DATA_DIR / "cron.log")
        print(f"В crontab стоит:\n{line}\nТик раз в {args.every} мин, сводка раз в {args.summary_every} мин. Лог: {DATA_DIR / 'cron.log'}")
        return 0

    if args.command == "uninstall":
        removed = cron.uninstall()
        print("Строка дня 18 убрана из crontab." if removed else "Строки дня 18 в crontab не было.")
        return 0

    state = cron.status()
    print(state["line"] or "Строки дня 18 в crontab нет.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
