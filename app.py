import os
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
AI_MODEL = os.getenv("AI_MODEL", "openrouter/free")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


@app.route("/", methods=["GET"])
def index():
    return "Bot is running!"


@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    data = request.json
    if not data or "message" not in data:
        return "OK", 200

    message = data["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", message.get("caption", ""))

    image_url = None
    if "photo" in message:
        photo_file_id = message["photo"][-1]["file_id"]
        file_info_res = requests.get(
            f"{TELEGRAM_API_URL}/getFile?file_id={photo_file_id}"
        ).json()
        if file_info_res.get("ok"):
            file_path = file_info_res["result"]["file_path"]
            image_url = (
                f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
            )

    if not text and not image_url:
        return "OK", 200

    try:
        if image_url:
            content = [
                {
                    "type": "text",
                    "text": text if text else "Что изображено на фото?",
                },
                {"type": "image_url", "image_url": {"url": image_url}},
            ]
        else:
            content = text

        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://render.com",
            "X-Title": "TelegramAIBot",
        }

        payload = {
            "model": AI_MODEL,
            "messages": [{"role": "user", "content": content}],
        }

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        res_data = response.json()

        if response.status_code == 200 and "choices" in res_data:
            answer = res_data["choices"][0]["message"]["content"]
        else:
            answer = f"Ошибка от нейросети: {res_data.get('error', {}).get('message', 'Неизвестная ошибка')}"

    except Exception as e:
        answer = f"Ошибка сервера: {str(e)}"

    requests.post(
        f"{TELEGRAM_API_URL}/sendMessage",
        json={"chat_id": chat_id, "text": answer},
    )

    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
