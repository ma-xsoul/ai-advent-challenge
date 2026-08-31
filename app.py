import os

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request


load_dotenv()

app = Flask(__name__)

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


@app.get("/")
def index():
    return render_template("index.html", model=DEEPSEEK_MODEL)


@app.post("/api/chat")
def chat():
    prompt = (request.get_json(silent=True) or {}).get("prompt", "").strip()

    if not prompt:
        return jsonify({"error": "Введите запрос"}), 400

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return jsonify({"error": "Не задан DEEPSEEK_API_KEY в файле .env"}), 500

    try:
        response = requests.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "thinking": {"type": "disabled"},
                "stream": False,
            },
            timeout=120,
        )
    except requests.RequestException as error:
        return jsonify({"error": "Не удалось связаться с DeepSeek", "details": str(error)}), 502

    try:
        response_json = response.json()
    except ValueError:
        return jsonify({
            "error": "DeepSeek вернул ответ не в формате JSON",
            "status": response.status_code,
            "body": response.text[:1000],
        }), 502

    return jsonify(response_json), response.status_code


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
