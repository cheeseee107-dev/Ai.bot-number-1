import os
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Проверенные модели с точными рабочими путями
MODELS = {
    "gpt": "openai/gpt-4o-mini",
    "deepseek": "deepseek/deepseek-chat",
    "grok": "x-ai/grok-2",
    "claude": "anthropic/claude-3.5-sonnet",
}

MODEL_NAMES = {
    "gpt": "💬 ChatGPT 4o-mini",
    "deepseek": "🤖 DeepSeek V3",
    "grok": "⚡ Grok 2",
    "claude": "🧠 Claude 3.5 Sonnet",
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
          f"✅ Модель успешно переключена на: {nice_name}\nМожешь задавать вопрос"
          " 👇",
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
                {"text": "💬 ChatGPT", "callback_data": "gpt"},
                {"text": "🤖 DeepSeek", "callback_data": "deepseek"},
            ],
            [
                {"text": "⚡ Grok 2", "callback_data": "grok"},
                {"text": "🧠 Claude", "callback_data": "claude"},
            ],
        ]
    }

  if text in ["/start", "/model"]:
    send_message(
        chat_id,
        "👋 Выбери нужную нейросеть с помощью кнопок ниже:",
        reply_markup=get_keyboard(),
    )
    return "OK", 200

  current_model_key = user_models.get(chat_id, "gpt")
  chosen_model = MODELS[current_model_key]

  try:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://render.com",
        "X-Title": "TelegramAIBot",
    }

    payload = {
        "model": chosen_model,
        "messages": [{"role": "user", "content": text}],
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
      answer = f"⚠️ Ошибка ({response.status_code}): {error_msg}"

  except Exception as e:
    answer = f"💥 Ошибка сервера: {str(e)}"

  send_message(chat_id, answer)
  return "OK", 200


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
