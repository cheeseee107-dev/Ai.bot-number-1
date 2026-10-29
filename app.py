import os
import base64
import threading
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

MODEL_NAMES = {
    "gemini": "⚡ Google Gemini",
    "gpt": "💬 ChatGPT 4o-mini",
    "deepseek": "🤖 DeepSeek V3"
}

user_models = {}
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id, 
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
        
    try:
        response = requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=20)
        if response.status_code == 400:
            del payload["parse_mode"]
            requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=20)
    except Exception:
        pass

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

def call_openrouter(model_id, text, image_base64=None):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://render.com",
        "X-Title": "TelegramBot"
    }
    
    user_text = text if text else "Опиши, что находится на этой картинке, и распознай текст, если он есть."
    
    if image_base64:
        if "deepseek" in model_id:
            content = [{"type": "text", "text": user_text + "\n\n*(От бота: Картинка проигнорирована, так как DeepSeek V3 не умеет распознавать фото. Пожалуйста, переключись на Gemini или ChatGPT!)*"}]
        else:
            content = [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
            ]
    else:
        content = user_text

    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": "Отвечай красиво, структурированно, дели текст на удобные абзацы и обязательно используй подходящие по смыслу эмодзи."},
            {"role": "user", "content": content}
        ],
        "max_tokens": 4000
    }
    return requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=60
    )

def process_ai_request(chat_id, text, current_key, image_base64):
    if not OPENROUTER_API_KEY:
        send_message(chat_id, "Ошибка: не найден OPENROUTER_API_KEY.")
        return

    if current_key == "gemini":
        gemini_candidates = [
            "google/gemini-2.5-flash",
            "google/gemini-1.5-flash",
            "google/gemini-1.5-pro"
        ]
        answer = None
        last_err = "Все серверы Gemini сейчас недоступны."

        for model_id in gemini_candidates:
            try:
                response = call_openrouter(model_id, text, image_base64)
                res_data = response.json()
                if response.status_code == 200 and "choices" in res_data:
                    answer = res_data["choices"][0]["message"].get("content")
                    if answer:
                        break
                else:
                    err = res_data.get("error", {}).get("message", "Неизвестная ошибка")
                    last_err = f"Ошибка ({response.status_code}): {err}"
            except Exception as e:
                last_err = f"Ошибка связи: {str(e)}"

        send_message(chat_id, answer or last_err)
        return

    chosen_model = "openai/gpt-4o-mini" if current_key == "gpt" else "deepseek/deepseek-chat"
    try:
        response = call_openrouter(chosen_model, text, image_base64)
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
            requests.post(f"{TELEGRAM_API_URL}/answerCallbackQuery", json={"callback_query_id": query_id}, timeout=5)
        except Exception:
            pass

        if callback_data in ["gemini", "gpt", "deepseek"]:
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
    
    # Обработка картинки
    photo = message.get("photo")
    image_base64 = None
    if photo:
        try:
            # Берем последнее фото из массива (оно в самом высоком разрешении)
            file_id = photo[-1]["file_id"]
            file_info = requests.get(f"{TELEGRAM_API_URL}/getFile?file_id={file_id}", timeout=10).json()
            if file_info.get("ok"):
                file_path = file_info["result"]["file_path"]
                file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
                img_data = requests.get(file_url, timeout=20).content
                image_base64 = base64.b64encode(img_data).decode("utf-8")
        except Exception as e:
            print(f"Ошибка загрузки фото: {e}")

    if text in ["/start", "/model"]:
        current = user_models.get(chat_id, "gemini")
        active_name = MODEL_NAMES.get(current, "Google Gemini")
        send_message(chat_id, f"Привет! Сейчас выбрана: {active_name}\nВыбери нейросеть из меню ниже:", reply_markup=get_keyboard())
        return "OK", 200

    if not text and not image_base64:
        return "OK", 200

    current_key = user_models.get(chat_id, "gemini")
    threading.Thread(target=process_ai_request, args=(chat_id, text, current_key, image_base64)).start()
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
