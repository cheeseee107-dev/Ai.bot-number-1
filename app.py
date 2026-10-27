import os
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Официальные модели с точными путями в OpenRouter
MODELS = {
    "gemini_flash": "google/gemini-flash-1.5",
    "gemini_pro": "google/gemini-pro-1.5",
    "claude_sonnet": "anthropic/claude-3.5-sonnet",
    "deepseek": "deepseek/deepseek-chat",
    "gpt": "openai/gpt-4o-mini",
    "kimi": "moonshotai/kimi-k0-i",
    "llama": "meta-llama/llama-3-70b-instruct",
}

# Красивые названия для кнопок в Телеграме
MODEL_NAMES = {
    "gemini_flash": "⚡ Gemini 1.5 Flash",
    "gemini_pro": "👑 Gemini 1.5 Pro",
    "claude_sonnet": "🧠 Claude 3.5 Sonnet",
    "deepseek": "🤖 DeepSeek V3",
    "gpt": "💬 ChatGPT 4o-mini",
    "kimi": "🌙 Kimi AI",
    "llama": "🦙 Meta Llama 3",
}

# Память для хранения выбранной модели пользователя
user_models = {}

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def send_message(chat_id, text, reply_markup=None, parse_mode="Markdown"):
  payload = {
      "chat_id": chat_id,
      "text": text,
      "parse_mode": parse_mode,
  }
  if reply_markup:
    payload["reply_markup"] = reply_markup
  requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)


@app.route("/", methods=["GET"])
def index():
  return "Bot is running!"


@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
  data = request.json
  if not data:
    return "OK", 200

  # Обработка нажатий на инлайн-кнопки (плашки)
  if "callback_query" in data:
    query = data["callback_query"]
    chat_id = query["message"]["chat"]["id"]
    callback_data = query["data"]

    if callback_data in MODELS:
      user_models[chat_id] = callback_data
      nice_name = MODEL_NAMES.get(callback_data, callback_data)
      send_message(
          chat_id,
          f"✅ *Модель успешно переключена!*\nТекущая нейросеть: *{nice_name}*\n\nМожешь задавать свой вопрос 👇",
      )

    requests.post(
        f"{TELEGRAM_API_URL}/answerCallbackQuery",
        json={"callback_query_id": query["id"]},
    )
    return "OK", 200

  if "message" not in data:
    return "OK", 200

  message = data["message"]
  chat_id = message["chat"]["id"]
  text = message.get("text", message.get("caption", ""))

  # Функция генерации красивой клавиатуры с кнопками
  def get_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "⚡ Gemini Flash", "callback_data": "gemini_flash"},
                {"text": "👑 Gemini Pro", "callback_data": "gemini_pro"},
            ],
            [
                {"text": "🧠 Claude Sonnet", "callback_data": "claude_sonnet"},
                {"text": "🤖 DeepSeek", "callback_data": "deepseek"},
            ],
            [
                {"text": "💬 ChatGPT", "callback_data": "gpt"},
                {"text": "🌙 Kimi", "callback_data": "kimi"},
            ],
            [{"text": "🦙 Llama 3", "callback_data": "llama"}],
        ]
    }

  if text == "/start":
    current = user_models.get(chat_id, "gemini_flash")
    active_name = MODEL_NAMES.get(current, "Gemini 1.5 Flash")
    welcome_text = (
        f"👋 *Привет! Я твой продвинутый AI-бот.*\n\n"
        f"⚙️ Активная нейросеть: *{active_name}*\n\n"
        f"Выбирай нужную модель на плашках ниже или отправляй текст/фото:"
    )
    send_message(chat_id, welcome_text, reply_markup=get_keyboard())
    return "OK", 200

  if text == "/model":
    current = user_models.get(chat_id, "gemini_flash")
    active_name = MODEL_NAMES.get(current, "Gemini 1.5 Flash")
    send_message(
        chat_id,
        f"🛠 *Выбери нейросеть для работы* (сейчас активна: *{active_name}*):",
        reply_markup=get_keyboard(),
    )
    return "OK", 200

  # По умолчанию используем Gemini Flash, если пользователь еще не выбрал модель
  current_model_key = user_models.get(chat_id, "gemini_flash")
  chosen_model = MODELS[current_model_key]

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
          {"type": "text", "text": text if text else "Что изображено на фото?"},
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
        "model": chosen_model,
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
      answer = f"⚠️ Ошибка от нейросети: {res_data.get('error', {}).get('message', 'Неизвестная ошибка')}"

  except Exception as e:
    answer = f"💥 Ошибка сервера: {str(e)}"

  send_message(chat_id, answer, parse_mode=None)
  return "OK", 200


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
