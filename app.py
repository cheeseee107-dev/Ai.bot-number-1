import os
import threading
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

MODELS = {
    "gemini": "gemini-3.8-flash",
    "gpt": "openai/gpt-4o-mini",
    "deepseek": "deepseek/deepseek-chat",
}

MODEL_NAMES = {
    "gemini": "⚡ Google Gemini Direct",
    "gpt": "💬 ChatGPT 4o-mini",
    "deepseek": "🤖 DeepSeek V3",
}

user_models = {}
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def send_message(chat_id, text, reply_markup=None):
  payload = {"chat_id": chat_id, "text": text}
  if reply_markup:
    payload["reply_markup"] = reply_markup
  try:
    requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=20)
  except Exception as e:
    print(f"Ошибка отправки в TG: {e}")


def get_keyboard():
  return {
      "inline_keyboard": [
          [{"text": "⚡ Gemini Direct", "callback_data": "gemini"}],
          [
              {"text": "💬 ChatGPT", "callback_data": "gpt"},
              {"text": "🤖 DeepSeek", "callback_data": "deepseek"},
          ],
      ]
  }


def process_ai_request(chat_id, text, current_key):
  if current_key == "gemini":
    if not GEMINI_API_KEY:
      send_message(
          chat_id,
          "Ошибка: переменная GEMINI_API_KEY не найдена в настройках Render.",
      )
      return

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": text}]}]}

    try:
      response = requests.post(url, json=payload, timeout=60)
      res_data = response.json()
      if response.status_code == 200 and "candidates" in res_data:
        answer = res_data["candidates"][0]["content"]["parts"][0]["text"]
      else:
        err = res_data.get("error", {}).get("message", "Неизвестная ошибка")
        answer = f"Ошибка Google Gemini ({response.status_code}): {err}"
    except Exception as e:
      answer = f"Ошибка соединения с Gemini: {str(e)}"

  else:
    chosen_model = MODELS.get(current_key, MODELS["gpt"])
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://render.com",
        "X-Title": "TelegramBot",
    }
    payload = {
        "model": chosen_model,
        "messages": [{"role": "user", "content": text}],
    }

    try:
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
        err = res_data.get("error", {}).get("message", "Неизвестная ошибка")
        answer = f"Ошибка OpenRouter ({response.status_code}): {err}"
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
          timeout=5,
      )
    except Exception:
      pass

    if callback_data in MODELS:
      user_models[chat_id] = callback_data
      nice_name = MODEL_NAMES[callback_data]
      send_message(
          chat_id, f"Выбрана модель: {nice_name}\nМожешь задавать вопрос 👇"
      )
    else:
      send_message(
          chat_id, "Кнопка устарела. Напиши /start для вызова актуального меню."
      )

    return "OK", 200

  if "message" not in data:
    return "OK", 200

  message = data["message"]
  chat_id = message["chat"]["id"]
  text = message.get("text", message.get("caption", ""))

  if text in ["/start", "/model"]:
    current = user_models.get(chat_id, "gemini")
    active_name = MODEL_NAMES.get(current, "Google Gemini Direct")
    send_message(
        chat_id,
        f"Привет! Сейчас выбрана: {active_name}\nВыбери нейросеть из меню ниже:",
        reply_markup=get_keyboard(),
    )
    return "OK", 200

  if not text:
    return "OK", 200

  current_key = user_models.get(chat_id, "gemini")

  threading.Thread(
      target=process_ai_request, args=(chat_id, text, current_key)
  ).start()
  return "OK", 200


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
