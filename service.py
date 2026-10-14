"""
Приватный сервис дня 30: модель на маке, доступ из интернета.

    .venv/bin/python service.py              # сервис на :8030 и туннель Cloudflare
    .venv/bin/python service.py --no-tunnel  # только на этом маке
    .venv/bin/python service.py --no-tunnel --public-url https://…  # свой туннель (ngrok и т. п.)

Туннель (cloudflared, бесплатный, без регистрации) даёт адрес
https://….trycloudflare.com — он меняется при каждом запуске. Ollama
остаётся на localhost: наружу смотрит только обёртка с ключами, очередью
и лимитами. Ключи выдаются на странице дня 30.
"""

import argparse
import atexit
import os
import re
import shutil
import socket
import subprocess
import threading

from dotenv import load_dotenv

from advent.local.server import create_service_app
from advent.local.service import KEYS_PATH, MODEL, PORT, KeyStore, PrivateService


TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def start_tunnel(service: PrivateService, port: int) -> None:
    binary = shutil.which("cloudflared")
    if binary is None:
        print("cloudflared не найден — сервис только на localhost. Установите: brew install cloudflared")
        return
    process = subprocess.Popen(
        [binary, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    atexit.register(process.terminate)

    def watch() -> None:
        tunnel_url = None
        for line in process.stderr:
            found = TUNNEL_URL.search(line)
            if found and tunnel_url is None:
                tunnel_url = found.group(0)
                print(f"\n  Адрес туннеля: {tunnel_url} — заработает, когда cloudflared подключится\n", flush=True)
            if "Registered tunnel connection" in line and tunnel_url:
                service.public_url = tunnel_url
                print(f"\n  Доступ из интернета: {tunnel_url}\n", flush=True)
            if " ERR " in line:
                print("cloudflared:", line.strip()[:200], flush=True)
        service.public_url = None
        print("Туннель остановился", flush=True)

    threading.Thread(target=watch, name="tunnel", daemon=True).start()


def main() -> None:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(description="Приватный сервис на локальной LLM")
    parser.add_argument("--port", type=int, default=int(os.getenv("SERVICE_PORT", PORT)))
    parser.add_argument("--model", default=os.getenv("SERVICE_MODEL", MODEL))
    parser.add_argument("--no-tunnel", action="store_true", help="не открывать доступ из интернета")
    parser.add_argument("--public-url", default=os.getenv("SERVICE_PUBLIC_URL"), help="адрес своего туннеля — покажется на странице дня 30")
    args = parser.parse_args()

    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            raise SystemExit(f"Порт {args.port} занят — сервис уже запущен? Остановите его: pkill -f service.py")

    keys = KeyStore(KEYS_PATH)
    keys.ensure()
    service = PrivateService(keys=keys, model=args.model)
    service.public_url = (args.public_url or "").rstrip("/") or None
    if not args.no_tunnel:
        start_tunnel(service, args.port)
    print(f"Сервис: http://127.0.0.1:{args.port} · модель {args.model}", flush=True)
    create_service_app(service).run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
