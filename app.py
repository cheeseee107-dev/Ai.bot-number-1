import os
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

MODELS = {
    "gemini_flash": "google/gemini-flash-1.5",
    "gemini_pro": "google/gemini-pro-1.5",
    "claude_sonnet": "anthropic/claude-3.5-sonnet",
    "deepseek": "deepseek/deepseek-chat",
    "gpt": "openai/gpt-4o-mini",
    "kimi": "moonshotai/kimi-k0-i",
    "llama": "meta-llama/llama-3-70b-instruct",
}

MODEL_NAMES = {
    "gemini_flash": "⚡ Gemini 1.5 Flash",
    "gemini_pro": "👑 Gemini 1.5 Pro",
    "claude_sonnet": "🧠 Claude 3.5 Sonnet",
    "deepseek": "🤖 DeepSeek V3",
    "gpt": "💬 ChatGPT 4o-mini",
    "kimi": "🌙 Kimi AI",
    "llama": "🦙 Meta Llama 3",
}

user_models = {}
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def send_message(chat_id, text, reply_markup=None):
  payload = {"chat_id": chat_id, "text": text}
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

  if "callback_query" in data:
    query = data["callback_query"]
    chat_id = query["message"]["chat"]["id"]
    callback_data = query["data"]

    if callback_data in MODELS:
      user_models[chat_id] = callback_data
      nice_name = MODEL_NAMES.get(callback_data, callback_data)
      send_message(
          chat_id,
          f"✅ Модель переключена на: {nice_name}\nМожешь задавать вопрос 👇",
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
    send_message(
        chat_id,
        "👋 Привет! Выбери нейросеть кнопками ниже:",
        reply_markup=get_keyboard(),
    )
    return "OK", 200

  if text == "/model":
    send_message(
        chat_id, "🛠 Выбери нейросеть для работы:", reply_markup=get_keyboard()
    )
    return "OK", 200

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
      error_msg = res_data.get("error", {}).get(
          "message", "Неизвестная ошибка API"
      )
      answer = f"⚠️ Ошибка от OpenRouter ({response.status_code}): {error_msg}"

  except Exception as e:
    answer = f"💥 Ошибка кода на сервере: {str(e)}"

  send_message(chat_id, answer)
  return "OK", 200


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
