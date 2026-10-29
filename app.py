import os
import threading
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

MODELS = {
    "gemini": "auto_gemini",
    "gpt": "openai/gpt-4o-mini",
    "deepseek": "deepseek/deepseek-chat"
}

MODEL_NAMES = {
    "gemini": "⚡ Google Gemini",
    "gpt": "💬 ChatGPT 4o-mini",
    "deepseek": "🤖 DeepSeek V3"
}

user_models = {}
working_gemini_model = None
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=20)
    except Exception as e:
        print(f"Ошибка отправки: {e}")


def get_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "⚡ Gemini", "callback_data": "gemini"}],
            [
                {"text": "💬 ChatGPT", "callback_data": "gpt"},
                {"text": "🤖 DeepSeek", "callback_data": "deepseek"}
            ]
        ]
    }


def get_gemini_candidates():
    candidates = []
    # Добавлено слово batch, чтобы исключить неподдерживаемые модели
    skip_words = ("embedding", "tts", "image", "audio", "robotics", "batch")
    try:
        resp = requests.get("https://openrouter.ai/api/v1/models", timeout=15)
        if resp.status_code == 200:
            all_models = resp.json().get("data", [])
            free_flash = []
            free_other = []
            paid_flash = []
            paid_other = []
            for m in all_models:
                mid = m.get("id", "")
                if not mid.startswith("google/gemini"):
                    continue
                if any(w in mid for w in skip_words):
                    continue
                if ":free" in mid and "flash" in mid:
                    free_flash.append(mid)
                elif ":free" in mid:
                    free_other.append(mid)
                elif "flash" in mid:
                    paid_flash.append(mid)
                else:
                    paid_other.append(mid)
            candidates = free_flash + free_other + paid_flash + paid_other
    except Exception as e:
        print(f"Ошибка получения списка моделей: {e}")

    if not candidates:
        candidates = ["google/gemini-2.5-flash", "google/gemini-2.0-flash-001"]
    return candidates


def call_openrouter(model_id, text):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://render.com",
        "X-Title": "TelegramBot"
    }
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": text}]
    }
    return requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=50
    )


def process_ai_request(chat_id, text, current_key):
    global working_gemini_model

    if not OPENROUTER_API_KEY:
        send_message(chat_id, "Ошибка: не найден OPENROUTER_API_KEY.")
        return

    if current_key == "gemini":
        if working_gemini_model:
            try:
                response = call_openrouter(working_gemini_model, text)
                res_data = response.json()
                if response.status_code == 200 and "choices" in res_data:
                    content = res_data["choices"][0]["message"].get("content")
                    if content:
                        send_message(chat_id, content)
                        return
            except Exception:
                pass
            working_gemini_model = None

        candidates = get_gemini_candidates()
        answer = None
        last_err = "Не удалось подключиться ни к одной модели Gemini."

        for model_id in candidates[:8]:
            try:
                response = call_openrouter(model_id, text)
                res_data = response.json()
                if response.status_code == 200 and "choices" in res_data:
                    content = res_data["choices"][0]["message"].get("content")
                    if content:
                        working_gemini_model = model_id
                        answer = content
                        break
                else:
                    err = res_data.get("error", {}).get("message", "Неизвестная ошибка")
                    last_err = f"Ошибка ({response.status_code}): {err}"
                    if response.status_code == 401:
                        break
            except Exception as e:
                last_err = f"Ошибка связи: {str(e)}"
                continue

        send_message(chat_id, answer or last_err)
        return

    chosen_model = MODELS.get(current_key, MODELS["gpt"])
    try:
        response = call_openrouter(chosen_model, text)
        res_data = response.json()
        if response.status_code == 200 and "choices" in res_data:
            answer = res_data["choices"][0]["message"]["content"]
        else:
            err = res_data.get("error", {}).get("message", "Неизвестная ошибка")
            answer = f"Ошибка ({response.status_code}): {err}"
    except Exception as e:
        answer = f"Ошибка связи с сервером: {str(e)}"

    send_message(chat_id, answer)


@app.route("/", methods=["GET"])
def index():
    return "Bot is running!"


@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    data = request.json
    if not data:
        return "OK", 200

    if "callback_query" in data:
        query = data["callback_query"]
        query_id = query["id"]
        chat_id = query["message"]["chat"]["id"]
        callback_data = query.get("data")

        try:
            requests.post(
                f"{TELEGRAM_API_URL}/answerCallbackQuery",
                json={"callback_query_id": query_id},
                timeout=5
            )
        except Exception:
            pass

        if callback_data in MODELS:
            user_models[chat_id] = callback_data
            nice_name = MODEL_NAMES[callback_data]
            send_message(chat_id, f"Выбрана модель: {nice_name}\nМожешь задавать вопрос 👇")
        else:
            send_message(chat_id, "Кнопка устарела. Напиши /start для вызова актуального меню.")

        return "OK", 200

    if "message" not in data:
        return "OK", 200

    message = data["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", message.get("caption", ""))

    if text in ["/start", "/model"]:
        current = user_models.get(chat_id, "gemini")
        active_name = MODEL_NAMES.get(current, "Google Gemini")
        send_message(
            chat_id,
            f"Привет! Сейчас выбрана: {active_name}\nВыбери нейросеть из меню ниже:",
            reply_markup=get_keyboard()
        )
        return "OK", 200

    if not text:
        return "OK", 200

    current_key = user_models.get(chat_id, "gemini")
    threading.Thread(target=process_ai_request, args=(chat_id, text, current_key)).start()
    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
