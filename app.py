import os
import requests
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Модели, которые реально работают без денег на балансе OpenRouter
MODELS = {
    "gemini": "google/gemini-2.0-flash-thinking-exp:free",
    "deepseek": "deepseek/deepseek-chat",
    "gpt": "openai/gpt-4o-mini",
    "llama": "meta-llama/llama-3.3-70b-instruct:free",
}

MODEL_NAMES = {
    "gemini": "⚡ Google Gemini Free",
    "deepseek": "🤖 DeepSeek V3",
    "gpt": "💬 ChatGPT 4o-mini",
    "llama": "🦙 Meta Llama 3.3 Free",
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
    print(f"Ошибка отправки: {e}")


def get_keyboard():
  return {
      "inline_keyboard": [
          [
              {"text": "⚡ Gemini Free", "callback_data": "gemini"},
              {"text": "🤖 DeepSeek", "callback_data": "deepseek"},
          ],
          [
              {"text": "💬 ChatGPT", "callback_data": "gpt"},
              {"text": "🦙 Llama 3.3 Free", "callback_data": "llama"},
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

  # Нажатие на инлайн-кнопки
  if "callback_query" in data:
    query = data["callback_query"]
    query_id = query["id"]
    chat_id = query["message"]["chat"]["id"]
    callback_data = query.get("data")

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
          f"Выбрана модель: {nice_name}\nТеперь напиши свой вопрос 👇",
      )
    else:
      send_message(
          chat_id,
          "Кнопка устарела. Напиши /start, чтобы открыть актуальное меню.",
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
        f"Привет! Сейчас выбрана: {active_name}\nВыбери нейросеть из списка ниже:",
        reply_markup=get_keyboard(),
    )
    return "OK", 200

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
                  else "Что на этой картинке? Опиши подробно по-русски."
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
      answer = f"Ошибка ({response.status_code}): {err}"

  except Exception as e:
    answer = f"Ошибка сервера: {str(e)}"

  send_message(chat_id, answer)
  return "OK", 200


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
