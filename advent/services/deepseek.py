import os

import requests


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"


def get_model() -> str:
    return os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


def request_completion(prompt: str) -> tuple[dict, int]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return {"error": "Не задан DEEPSEEK_API_KEY в файле .env"}, 500

    try:
        response = requests.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": get_model(),
                "messages": [{"role": "user", "content": prompt}],
                "thinking": {"type": "disabled"},
                "stream": False,
            },
            timeout=120,
        )
    except requests.RequestException as error:
        return {
            "error": "Не удалось связаться с DeepSeek",
            "details": str(error),
        }, 502

    try:
        return response.json(), response.status_code
    except ValueError:
        return {
            "error": "DeepSeek вернул ответ не в формате JSON",
            "status": response.status_code,
            "body": response.text[:1000],
        }, 502
