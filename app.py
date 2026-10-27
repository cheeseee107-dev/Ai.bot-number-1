import os
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Точные и проверенные пути к моделям на OpenRouter
MODELS = {
    "gemini": "google/gemini-2.5-flash",
    "gpt": "openai/gpt-4o-mini",
    "deepseek": "deepseek/deepseek-chat",
    "claude": "anthropic/claude-3.5-sonnet",
    "grok": "x-ai/grok-2-1212",
    "llama": "meta-llama/llama-3.1-70b-instruct",
}

MODEL_NAMES = {
    "gemini": "⚡ Google Gemini",
    "gpt": "💬 ChatGPT 4o-mini",
    "deepseek": "🤖 DeepSeek V3",
    "claude": "🧠 Claude 3.5",
    "grok": "🚀 Grok 2",
    "llama": "🦙 Llama 3.1",
}

user_models = {}
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def send_message(chat_id, text, reply_markup=None):
  payload = {"chat_id": chat_id, "text": text}
  if reply_markup:
    payload["reply_markup"] = reply_markup
  try:
    requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=15)
  except Exception as e:
    print(f"Ошибка отправки сообщения: {e}")


def get_keyboard():
  return {
      "inline_keyboard": [
          [
              {"text": "⚡ Gemini", "callback_data": "gemini"},
              {"text": "🤖 DeepSeek", "callback_data": "deepseek"},
          ],
          [
              {"text": "💬 ChatGPT", "callback_data": "gpt"},
              {"text": "🧠 Claude", "callback_data": "claude"},
          ],
          [
              {"text": "🚀 Grok", "callback_data": "grok"},
              {"text": "🦙 Llama", "callback_data": "llama"},
          ],
      ]
  }


@app.route("/", methods=["GET"])
def index():
  return "Bot is running!"


@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
  data = request.json
  if not data:
    return "OK", 200

  # Обработка нажатий на кнопки
  if "callback_query" in data:
    query = data["callback_query"]
    query_id = query["id"]
    chat_id = query["message"]["chat"]["id"]
    callback_data = query.get("data")

    # Обязательно подтверждаем нажатие, чтобы кнопка не зависала с часиками
    try:
      requests.post(
          f"{TELEGRAM_API_URL}/answerCallbackQuery",
          json={"callback_query_id": query_id},
          timeout=10,
      )
    except Exception:
      pass

    if callback_data in MODELS:
      user_models[chat_id] = callback_data
      nice_name = MODEL_NAMES[callback_data]
      send_message(
          chat_id,
          f"Выбрана нейросеть: {nice_name}\nНапиши мне вопрос или отправь картинку с подписью.",
      )
    else:
      send_message(
          chat_id,
          "Эта кнопка устарела. Отправь /start, чтобы получить новое меню.",
      )

    return "OK", 200

  if "message" not in data:
    return "OK", 200

  message = data["message"]
  chat_id = message["chat"]["id"]
  text = message.get("text", message.get("caption", ""))

  if text in ["/start", "/model"]:
    current = user_models.get(chat_id, "gpt")
    active_name = MODEL_NAMES.get(current, "ChatGPT")
    send_message(
        chat_id,
        f"Привет! Сейчас активна: {active_name}.\nВыбери нейросеть из меню ниже:",
        reply_markup=get_keyboard(),
    )
    return "OK", 200

  # Обработка отправки фото
  image_url = None
  if "photo" in message:
    photo_file_id = message["photo"][-1]["file_id"]
    try:
      file_info = requests.get(
          f"{TELEGRAM_API_URL}/getFile?file_id={photo_file_id}", timeout=10
      ).json()
      if file_info.get("ok"):
        file_path = file_info["result"]["file_path"]
        image_url = (
            f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
        )
    except Exception:
      pass

  if not text and not image_url:
    return "OK", 200

  current_key = user_models.get(chat_id, "gpt")
  chosen_model = MODELS.get(current_key, MODELS["gpt"])

  try:
    if image_url:
      content = [
          {
              "type": "text",
              "text": (
                  text
                  if text
                  else "Что на этой картинке? Опиши подробно на русском."
              ),
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
      err = res_data.get("error", {}).get("message", "Неизвестная ошибка")
      answer = f"Ошибка OpenRouter ({response.status_code}): {err}"

  except Exception as e:
    answer = f"Ошибка связи с сервером: {str(e)}"

  send_message(chat_id, answer)
  return "OK", 200


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
