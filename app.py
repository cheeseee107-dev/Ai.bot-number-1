import os
import base64
import threading
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Обновленные и проверенные идентификаторы моделей
MODELS = {
    "gpt": "openai/gpt-4o-mini",
    "deepseek": "deepseek/deepseek-chat",
    "claude": "anthropic/claude-3-haiku",
    "llama": "meta-llama/llama-3.1-8b-instruct"
}

MODEL_NAMES = {
    "gemini": "⚡ Gemini",
    "gpt": "💬 ChatGPT",
    "deepseek": "🤖 DeepSeek",
    "claude": "🧠 Claude 3",
    "llama": "🦙 LLaMA 3.1"
}

user_models = {}
chat_history = {}
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
            [{"text": "⚡ Gemini", "callback_data": "gemini"}, {"text": "💬 ChatGPT", "callback_data": "gpt"}],
            [{"text": "🤖 DeepSeek", "callback_data": "deepseek"}, {"text": "🧠 Claude 3", "callback_data": "claude"}],
            [{"text": "🦙 LLaMA 3.1", "callback_data": "llama"}]
        ]
    }

def call_openrouter(chat_id, model_id, text, image_base64=None):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://render.com",
        "X-Title": "TelegramBot"
    }
    
    user_text = text if text else "Опиши, что находится на этой картинке, и распознай текст, если он есть."
    current_message_content = []
    
    if image_base64:
        if "deepseek" in model_id or "llama" in model_id:
            current_message_content = [{"type": "text", "text": user_text + "\n\n*(От бота: Картинка проигнорирована, так как эта модель не умеет распознавать фото. Выбери Gemini, ChatGPT или Claude!)*"}]
        else:
            current_message_content = [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
            ]
    else:
        current_message_content = user_text

    if chat_id not in chat_history:
        chat_history[chat_id] = []

    messages = [{"role": "system", "content": "Отвечай красиво, структурированно, дели текст на удобные абзацы и обязательно используй подходящие по смыслу эмодзи."}]
    
    messages.extend(chat_history[chat_id])
    messages.append({"role": "user", "content": current_message_content})

    payload = {
        "model": model_id,
        "messages": messages,
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

    answer = None
    last_err = "Ошибка связи с сервером."

    if current_key == "gemini":
        gemini_candidates = ["google/gemini-2.5-flash", "google/gemini-1.5-flash", "google/gemini-1.5-pro"]
        for model_id in gemini_candidates:
            try:
                response = call_openrouter(chat_id, model_id, text, image_base64)
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
    else:
        chosen_model = MODELS.get(current_key, MODELS["gpt"])
        try:
            response = call_openrouter(chat_id, chosen_model, text, image_base64)
            res_data = response.json()
            if response.status_code == 200 and "choices" in res_data:
                answer = res_data["choices"][0]["message"]["content"]
            else:
                err = res_data.get("error", {}).get("message", "Неизвестная ошибка")
                last_err = f"Ошибка ({response.status_code}): {err}"
        except Exception as e:
            last_err = f"Ошибка связи: {str(e)}"

    final_text = answer or last_err
    send_message(chat_id, final_text)

    if answer:
        text_for_history = text if text else "[Отправлено изображение]"
        chat_history[chat_id].append({"role": "user", "content": text_for_history})
        chat_history[chat_id].append({"role": "assistant", "content": answer})
        
        if len(chat_history[chat_id]) > 10:
            chat_history[chat_id] = chat_history[chat_id][-10:]

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

        if callback_data in MODEL_NAMES:
            user_models[chat_id] = callback_data
            nice_name = MODEL_NAMES[callback_data]
            chat_history[chat_id] = [] 
            send_message(chat_id, f"Выбрана модель: {nice_name}\nПамять диалога очищена. Можешь задавать вопрос 👇")
        else:
            send_message(chat_id, "Кнопка устарела. Напиши /start для вызова актуального меню.")
        return "OK", 200

    if "message" not in data:
        return "OK", 200

    message = data["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", message.get("caption", ""))
    
    photo = message.get("photo")
    image_base64 = None
    if photo:
        try:
            file_id = photo[-1]["file_id"]
            file_info = requests.get(f"{TELEGRAM_API_URL}/getFile?file_id={file_id}", timeout=10).json()
            if file_info.get("ok"):
                file_path = file_info["result"]["file_path"]
                file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
                img_data = requests.get(file_url, timeout=20).content
                image_base64 = base64.b64encode(img_data).decode("utf-8")
        except Exception as e:
            print(f"Ошибка загрузки фото: {e}")

    if text in ["/start", "/model", "/clear"]:
        current = user_models.get(chat_id, "gemini")
        active_name = MODEL_NAMES.get(current, "⚡ Gemini")
        chat_history[chat_id] = [] 
        send_message(chat_id, f"Привет! Память очищена.\nСейчас выбрана: {active_name}\nВыбери нейросеть из меню ниже:", reply_markup=get_keyboard())
        return "OK", 200

    if not text and not image_base64:
        return "OK", 200

    current_key = user_models.get(chat_id, "gemini")
    threading.Thread(target=process_ai_request, args=(chat_id, text, current_key, image_base64)).start()
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
