import os
import base64
import threading
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

MODELS = {
    "gpt": "openai/gpt-4o-mini",
    "deepseek": "deepseek/deepseek-chat",
    "claude": "anthropic/claude-haiku-4.5",
    "llama": "meta-llama/llama-3.1-70b-instruct",
    "kimi": "moonshotai/kimi-k2.6",
    "grok": "x-ai/grok-4.5",
    "qwen_vl": "qwen/qwen-2-vl-72b-instruct:free"
}

MODEL_NAMES = {
    "gemini": "⚡ Gemini",
    "gpt": "💬 ChatGPT",
    "claude": "🧠 Claude",
    "qwen_vl": "👁 Qwen VL",
    "deepseek": "🤖 DeepSeek",
    "llama": "🦙 LLaMA",
    "grok": "🚀 Grok",
    "kimi": "🌙 Kimi"
}

TEXT_TO_MODEL = {v: k for k, v in MODEL_NAMES.items()}

DEFAULT_PROMPT = (
    "Ты — полезный ассистент. Отвечай структурированно, без лишней воды и с эмодзи. "
    "Не используй технические символы вроде ### и не рисуй линии из букв L или слешей."
)

user_models = {}
chat_history = {}
user_prompts = {}
user_states = {}

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
        "keyboard": [
            [{"text": "⚡ Gemini"}, {"text": "💬 ChatGPT"}, {"text": "🧠 Claude"}],
            [{"text": "👁 Qwen VL"}, {"text": "🤖 DeepSeek"}, {"text": "🦙 LLaMA"}],
            [{"text": "🌙 Kimi"}, {"text": "🚀 Grok"}],
            [{"text": "✏️ Промпт"}, {"text": "🔄 Сброс"}, {"text": "🗑 Память"}]
        ],
        "resize_keyboard": True
    }

def call_openrouter(chat_id, model_id, text, image_base64=None):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://render.com",
        "X-Title": "TelegramBot"
    }
    
    user_text = text if text else "Разбери задание на фото и дай ответ строго по инструкции."
    current_message_content = []
    
    if image_base64:
        if any(m in model_id for m in ["deepseek", "llama", "grok"]):
            current_message_content = [{"type": "text", "text": user_text + "\n\n*(От бота: Картинка проигнорирована, так как эта модель не умеет распознавать фото. Выбери Gemini, ChatGPT, Claude или Qwen VL!)*"}]
        else:
            current_message_content = [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
            ]
    else:
        current_message_content = user_text

    if chat_id not in chat_history:
        chat_history[chat_id] = []

    active_prompt = user_prompts.get(chat_id, DEFAULT_PROMPT)
    
    messages = [{"role": "system", "content": active_prompt}]
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

    if text == "✏️ Промпт":
        user_states[chat_id] = "waiting_for_prompt"
        send_message(chat_id, "✏️ Напиши следующим сообщением системный промпт для бота:")
        return "OK", 200

    if text == "🔄 Сброс":
        user_prompts.pop(chat_id, None)
        user_states.pop(chat_id, None)
        send_message(chat_id, "🔄 Промпт сброшен на стандартный!")
        return "OK", 200

    if text == "🗑 Память":
        chat_history[chat_id] = []
        send_message(chat_id, "🗑 Память диалога полностью очищена.")
        return "OK", 200

    if text in TEXT_TO_MODEL:
        model_key = TEXT_TO_MODEL[text]
        user_models[chat_id] = model_key
        chat_history[chat_id] = [] 
        user_states.pop(chat_id, None)
        send_message(chat_id, f"Выбрана модель: {text}\nПамять диалога очищена. Можешь задавать вопрос 👇", reply_markup=get_keyboard())
        return "OK", 200

    if user_states.get(chat_id) == "waiting_for_prompt" and text:
        user_prompts[chat_id] = text.strip()
        user_states.pop(chat_id, None)
        send_message(chat_id, f"✅ Новый промпт успешно сохранен:\n\n«{text.strip()}»")
        return "OK", 200

    if text in ["/start", "/model", "/menu", "/clear"]:
        current = user_models.get(chat_id, "gemini")
        active_name = MODEL_NAMES.get(current, "⚡ Gemini")
        chat_history[chat_id] = [] 
        user_states.pop(chat_id, None)
        
        current_prompt = user_prompts.get(chat_id, "Стандартный (кратко)")
        
        # Обновленное практичное описание прямо в боте
        msg_text = (
            f"🤖 **Доступные нейросети:**\n\n"
            f"**👁 Читают фото и текст:**\n"
            f"⚡ **Gemini** — самая быстрая, на каждый день.\n"
            f"💬 **ChatGPT** — надежный универсал.\n"
            f"🧠 **Claude** — топ для кода, аналитики и сложных задач.\n"
            f"👁 **Qwen VL** — умная, мощная, отлично понимает русский.\n"
            f"🌙 **Kimi** — хороша для работы с длинными текстами.\n\n"
            f"**📝 Только текст (картинки игнорируют):**\n"
            f"🤖 **DeepSeek** — гений логики и математики.\n"
            f"🦙 **LLaMA** — огромная база знаний и эрудиция.\n"
            f"🚀 **Grok** — шустрая, отвечает без жесткой цензуры.\n\n"
            f"⚙️ **Текущая модель:** {active_name}\n"
            f"⚙️ **Твой промпт:** {current_prompt}\n\n"
            f"👇 Выбери нейросеть в меню ниже:"
        )
        send_message(chat_id, msg_text, reply_markup=get_keyboard())
        return "OK", 200

    if not text and not image_base64:
        return "OK", 200

    current_key = user_models.get(chat_id, "gemini")
    threading.Thread(target=process_ai_request, args=(chat_id, text, current_key, image_base64)).start()
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
