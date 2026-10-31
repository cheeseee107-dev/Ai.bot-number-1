import os
import json
import base64
import threading
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")

ADMIN_ID = 123456789  # ВПИШИ СВОЙ TELEGRAM ID

MODELS = {
    "gpt": "openai/gpt-4o-mini",
    "deepseek": "deepseek/deepseek-chat",
    "claude": "anthropic/claude-haiku-4.5",
    "llama": "meta-llama/llama-3.1-70b-instruct",
    "kimi": "moonshotai/kimi-k2.6",
    "grok": "x-ai/grok-4.5",
    "qwen_vl": "qwen/qwen-2-vl-72b-instruct"
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

STYLES = {
    "default": "Отвечай понятно, без лишней воды, используя умеренное количество эмодзи.",
    "friendly": "Общайся максимально тепло, дружелюбно, как близкий друг. Используй много подходящих эмодзи, обращайся на «ты».",
    "strict": "Отвечай строго по фактам, максимально кратко и официально. Никаких эмодзи, приветствий и лишних отступлений.",
    "sarcastic": "Отвечай остроумно, с легким сарказмом, умеренной иронией и забавными подколками, но не теряй полезную суть ответа."
}

STYLE_NAMES = {
    "default": "Обычный 💡",
    "friendly": "Дружелюбный 😊",
    "strict": "Строгий 💼",
    "sarcastic": "Саркастичный 😏"
}

FORMATS = {
    "auto": "Форматируй ответ естественно: структурированный текст с логическими абзацами.",
    "table": "Все основные данные, факты, свойства или сравнения оформляй строго в виде Markdown-таблицы (| колонка 1 | колонка 2 |).",
    "bullets": "Отвечай исключительно нумерованным или маркированным списком (пунктами). По одному короткому тезису на строку.",
    "short": "Дай максимально краткую выжимку (TL;DR) в 2–3 предложения, только главная суть без предисловий."
}

FORMAT_NAMES = {
    "auto": "Обычный текст 📝",
    "table": "Таблица 📊",
    "bullets": "По пунктам 📌",
    "short": "Кратко (TL;DR) ⚡"
}

TEXT_TO_MODEL = {v: k for k, v in MODEL_NAMES.items()}

DEFAULT_PROMPT = (
    "Ты — полезный ассистент. Не рисуй псевдографику и не используй технический мусор."
)

user_models = {}
chat_history = {}
user_prompts = {}
user_styles = {}
user_formats = {}
user_states = {}
known_users = set()
user_search_enabled = set()
user_image_search_enabled = set()

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def load_users():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
        
    base_url = SUPABASE_URL.strip().rstrip("/")
    headers = {
        "apikey": SUPABASE_KEY.strip(),
        "Authorization": f"Bearer {SUPABASE_KEY.strip()}"
    }
    try:
        res = requests.get(f"{base_url}/rest/v1/bot_users?select=chat_id", headers=headers, timeout=10)
        if res.status_code == 200:
            for row in res.json():
                known_users.add(row["chat_id"])
    except Exception:
        pass

load_users()

def save_user(chat_id):
    known_users.add(chat_id)
    if not SUPABASE_URL or not SUPABASE_KEY:
        return

    base_url = SUPABASE_URL.strip().rstrip("/")
    api_key = SUPABASE_KEY.strip()

    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=ignore-duplicates"
    }
    try:
        requests.post(f"{base_url}/rest/v1/bot_users", headers=headers, json={"chat_id": chat_id}, timeout=5)
    except Exception:
        pass

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

def send_photos_with_text(chat_id, photo_urls, caption_text):
    if not photo_urls:
        send_message(chat_id, caption_text)
        return

    if len(photo_urls) == 1:
        if len(caption_text) <= 1024:
            payload = {
                "chat_id": chat_id,
                "photo": photo_urls[0],
                "caption": caption_text,
                "parse_mode": "Markdown"
            }
            res = requests.post(f"{TELEGRAM_API_URL}/sendPhoto", json=payload, timeout=20)
            if res.status_code != 200:
                del payload["parse_mode"]
                res = requests.post(f"{TELEGRAM_API_URL}/sendPhoto", json=payload, timeout=20)
                if res.status_code != 200:
                    send_message(chat_id, caption_text)
        else:
            requests.post(f"{TELEGRAM_API_URL}/sendPhoto", json={"chat_id": chat_id, "photo": photo_urls[0]}, timeout=20)
            send_message(chat_id, caption_text)
        return

    media = []
    for i, url in enumerate(photo_urls[:3]):
        item = {"type": "photo", "media": url}
        if i == 0 and len(caption_text) <= 1024:
            item["caption"] = caption_text
            item["parse_mode"] = "Markdown"
        media.append(item)

    payload = {"chat_id": chat_id, "media": media}
    res = requests.post(f"{TELEGRAM_API_URL}/sendMediaGroup", json=payload, timeout=25)
    
    if res.status_code != 200 or len(caption_text) > 1024:
        for m in media:
            m.pop("caption", None)
            m.pop("parse_mode", None)
        requests.post(f"{TELEGRAM_API_URL}/sendMediaGroup", json=payload, timeout=25)
        send_message(chat_id, caption_text)

def find_image_urls(query):
    clean_query = query.replace("покажи", "").replace("фото", "").replace("картинка", "").replace("изображение", "").strip()
    urls = []

    if SERPER_API_KEY:
        try:
            headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
            res = requests.post("https://google.serper.dev/images", headers=headers, json={"q": clean_query, "num": 3}, timeout=10)
            if res.status_code == 200:
                data = res.json()
                for img in data.get("images", [])[:3]:
                    link = img.get("imageUrl")
                    if link and link.startswith("http"):
                        urls.append(link)
                if urls:
                    return urls
        except Exception as e:
            print("Ошибка Serper:", e)

    try:
        url = f"https://en.wikipedia.org/w/api.php?action=query&prop=pageimages&format=json&piprop=original&titles={requests.utils.quote(clean_query)}"
        res = requests.get(url, timeout=5).json()
        pages = res.get("query", {}).get("pages", {})
        for _, val in pages.items():
            if "original" in val:
                urls.append(val["original"]["source"])
                break
    except Exception:
        pass
        
    return urls

def get_main_keyboard():
    return {
        "keyboard": [
            [{"text": "⚡ Gemini"}, {"text": "💬 ChatGPT"}, {"text": "🧠 Claude"}],
            [{"text": "👁 Qwen VL"}, {"text": "🤖 DeepSeek"}, {"text": "🦙 LLaMA"}],
            [{"text": "🌙 Kimi"}, {"text": "🚀 Grok"}],
            [{"text": "⚙️ Настройки"}, {"text": "🗑 Память"}]
        ],
        "resize_keyboard": True
    }

def get_settings_keyboard(chat_id):
    search_status = "ВКЛ 🟢" if chat_id in user_search_enabled else "ВЫКЛ 🔴"
    image_status = "ВКЛ 🟢" if chat_id in user_image_search_enabled else "ВЫКЛ 🔴"
    return {
        "keyboard": [
            [{"text": f"🌐 Поиск: {search_status}"}, {"text": f"🖼 Картинки: {image_status}"}],
            [{"text": "🎭 Стиль общения"}, {"text": "📐 Формат ответа"}],
            [{"text": "✏️ Промпт"}, {"text": "🔄 Сброс настроек"}],
            [{"text": "⬅️ Назад в меню"}]
        ],
        "resize_keyboard": True
    }

def get_style_keyboard():
    return {
        "keyboard": [
            [{"text": "💡 Обычный"}, {"text": "😊 Дружелюбный"}],
            [{"text": "💼 Строгий"}, {"text": "😏 Саркастичный"}],
            [{"text": "⬅️ Назад в настройки"}]
        ],
        "resize_keyboard": True
    }

def get_format_keyboard():
    return {
        "keyboard": [
            [{"text": "📝 Текст"}, {"text": "📊 Таблица"}],
            [{"text": "📌 По пунктам"}, {"text": "⚡ Кратко (TL;DR)"}],
            [{"text": "⬅️ Назад в настройки"}]
        ],
        "resize_keyboard": True
    }

def call_openrouter(chat_id, model_id, prompt_text, image_base64=None, use_web=False):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://render.com",
        "X-Title": "IvanGPT"
    }
    
    user_text = prompt_text if prompt_text else "Разбери фото и дай ответ."
    
    if image_base64:
        if any(m in model_id for m in ["deepseek", "llama", "grok", "sonar"]):
            current_message_content = [{"type": "text", "text": user_text + "\n\n*(Модель без распознавания изображений)*"}]
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
    chosen_style_key = user_styles.get(chat_id, "default")
    style_instruction = STYLES.get(chosen_style_key, STYLES["default"])

    chosen_format_key = user_formats.get(chat_id, "auto")
    format_instruction = FORMATS.get(chosen_format_key, FORMATS["auto"])

    tz_msk = timezone(timedelta(hours=3))
    current_time = datetime.now(tz_msk).strftime("%d.%m.%Y, время: %H:%M (МСК)")
    
    full_system_prompt = (
        f"{active_prompt}\n\n"
        f"Характер общения: {style_instruction}\n"
        f"Требование к оформлению: {format_instruction}\n\n"
        f"[Системная информация: сейчас {current_time}]"
    )
    
    messages = [{"role": "system", "content": full_system_prompt}]
    messages.extend(chat_history[chat_id])
    messages.append({"role": "user", "content": current_message_content})

    actual_model = "perplexity/sonar" if use_web else model_id

    payload = {
        "model": actual_model,
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

    use_web = chat_id in user_search_enabled
    use_img = chat_id in user_image_search_enabled and text

    found_img_urls = []
    if use_img:
        found_img_urls = find_image_urls(text)

    if use_web:
        send_message(chat_id, "🔎 Ищу актуальные данные в сети через Perplexity Sonar...")

    answer = None
    last_err = "Ошибка связи с сервером."

    if current_key == "gemini" and not use_web:
        gemini_candidates = ["google/gemini-2.5-flash", "google/gemini-1.5-flash"]
        for model_id in gemini_candidates:
            try:
                response = call_openrouter(chat_id, model_id, text, image_base64, use_web=False)
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
            response = call_openrouter(chat_id, chosen_model, text, image_base64, use_web=use_web)
            res_data = response.json()
            if response.status_code == 200 and "choices" in res_data:
                answer = res_data["choices"][0]["message"]["content"]
            else:
                err = res_data.get("error", {}).get("message", "Неизвестная ошибка")
                last_err = f"Ошибка ({response.status_code}): {err}"
        except Exception as e:
            last_err = f"Ошибка связи: {str(e)}"

    final_text = answer or last_err
    
    if found_img_urls and answer:
        send_photos_with_text(chat_id, found_img_urls, final_text)
    else:
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
    if not data or "message" not in data:
        return "OK", 200

    message = data["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", message.get("caption", ""))
    
    save_user(chat_id)
    
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
        except Exception:
            pass

    if text in ["⚙️ Настройки", "⬅️ Назад в настройки"]:
        user_states.pop(chat_id, None)
        search_status = "Включен 🟢" if chat_id in user_search_enabled else "Выключен 🔴"
        image_status = "Включен 🟢" if chat_id in user_image_search_enabled else "Выключен 🔴"
        current_style = STYLE_NAMES.get(user_styles.get(chat_id, "default"), "Обычный 💡")
        current_format = FORMAT_NAMES.get(user_formats.get(chat_id, "auto"), "Обычный текст 📝")
        current_prompt = user_prompts.get(chat_id, "Стандартный")
        
        settings_text = (
            f"⚙️ **Панель настроек бота:**\n\n"
            f"🌐 **Поиск в сети:** {search_status}\n"
            f"🖼 **Прикреплять картинки:** {image_status}\n"
            f"🎭 **Стиль:** {current_style}\n"
            f"📐 **Формат ответа:** {current_format}\n"
            f"✏️ **Промпт:** {current_prompt}\n\n"
            f"Выбирай нужный параметр ниже:"
        )
        send_message(chat_id, settings_text, reply_markup=get_settings_keyboard(chat_id))
        return "OK", 200

    if text == "🎭 Стиль общения":
        send_message(
            chat_id,
            "🎭 **Выбери характер общения бота:**\n\n"
            "💡 **Обычный** — спокойный, четкий, умеренные эмодзи.\n"
            "😊 **Дружелюбный** — на «ты», теплый, с кучей эмодзи.\n"
            "💼 **Строгий** — деловой, сухие факты, без эмодзи.\n"
            "😏 **Саркастичный** — с иронией, подколками и юмором.",
            reply_markup=get_style_keyboard()
        )
        return "OK", 200

    style_map = {
        "💡 Обычный": "default",
        "😊 Дружелюбный": "friendly",
        "💼 Строгий": "strict",
        "😏 Саркастичный": "sarcastic"
    }
    if text in style_map:
        user_styles[chat_id] = style_map[text]
        send_message(chat_id, f"✅ Стиль общения изменен на: **{text}**", reply_markup=get_settings_keyboard(chat_id))
        return "OK", 200

    if text == "📐 Формат ответа":
        send_message(
            chat_id,
            "📐 **В каком виде бот должен подавать информацию?**\n\n"
            "📝 **Текст** — стандартный связный текст с абзацами.\n"
            "📊 **Таблица** — группирует параметры и сравнения в Markdown-таблицу.\n"
            "📌 **По пунктам** — тезисный список без лишних фраз.\n"
            "⚡ **Кратко (TL;DR)** — суть вопроса в 2–3 предложениях.",
            reply_markup=get_format_keyboard()
        )
        return "OK", 200

    format_map = {
        "📝 Текст": "auto",
        "📊 Таблица": "table",
        "📌 По пунктам": "bullets",
        "⚡ Кратко (TL;DR)": "short"
    }
    if text in format_map:
        user_formats[chat_id] = format_map[text]
        send_message(chat_id, f"✅ Формат ответа изменен на: **{text}**", reply_markup=get_settings_keyboard(chat_id))
        return "OK", 200

    if text in ["⬅️ Назад в меню", "/menu"]:
        user_states.pop(chat_id, None)
        current = user_models.get(chat_id, "gemini")
        active_name = MODEL_NAMES.get(current, "⚡ Gemini")
        send_message(
            chat_id,
            f"Главное меню.\nТекущая модель: **{active_name}**\nЗадавай вопрос или выбирай модель 👇",
            reply_markup=get_main_keyboard()
        )
        return "OK", 200

    if text and text.startswith("🌐 Поиск:"):
        if chat_id in user_search_enabled:
            user_search_enabled.remove(chat_id)
            status = "выключен 🔴"
        else:
            user_search_enabled.add(chat_id)
            status = "включен 🟢"
        send_message(chat_id, f"Веб-поиск {status}.", reply_markup=get_settings_keyboard(chat_id))
        return "OK", 200

    if text and text.startswith("🖼 Картинки:"):
        if chat_id in user_image_search_enabled:
            user_image_search_enabled.remove(chat_id)
            status = "выключен 🔴"
        else:
            user_image_search_enabled.add(chat_id)
            status = "включен 🟢"
        send_message(chat_id, f"Поиск картинок {status}.", reply_markup=get_settings_keyboard(chat_id))
        return "OK", 200

    if text and text.startswith("/post"):
        if chat_id != ADMIN_ID:
            send_message(chat_id, "⛔ У тебя нет прав для выполнения этой команды.")
            return "OK", 200
            
        post_text = text.replace("/post", "", 1).strip()
        success_count = 0
        
        for uid in known_users:
            if photo:
                file_id = photo[-1]["file_id"]
                payload = {
                    "chat_id": uid,
                    "photo": file_id,
                    "caption": f"📢 **Уведомление:**\n\n{post_text}" if post_text else "📢 **Уведомление:**",
                    "parse_mode": "Markdown"
                }
                try:
                    requests.post(f"{TELEGRAM_API_URL}/sendPhoto", json=payload, timeout=5)
                    success_count += 1
                except Exception:
                    pass
            else:
                if not post_text:
                    continue
                payload = {
                    "chat_id": uid,
                    "text": f"📢 **Уведомление:**\n\n{post_text}",
                    "parse_mode": "Markdown"
                }
                try:
                    requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=5)
                    success_count += 1
                except Exception:
                    pass
                    
        send_message(chat_id, f"✅ Рассылка завершена. Доставлено {success_count} пользователям.")
        return "OK", 200

    if text == "✏️ Промпт":
        user_states[chat_id] = "waiting_for_prompt"
        send_message(chat_id, "✏️ Напиши следующим сообщением персональный промпт для бота:")
        return "OK", 200

    if text == "🔄 Сброс настроек":
        user_prompts.pop(chat_id, None)
        user_styles.pop(chat_id, None)
        user_formats.pop(chat_id, None)
        user_states.pop(chat_id, None)
        send_message(chat_id, "🔄 Промпт, стиль и формат сброшены на стандартные!", reply_markup=get_settings_keyboard(chat_id))
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
        send_message(chat_id, f"Выбрана модель: {text}\nПамять очищена. Можешь задавать вопрос 👇", reply_markup=get_main_keyboard())
        return "OK", 200

    if user_states.get(chat_id) == "waiting_for_prompt" and text:
        user_prompts[chat_id] = text.strip()
        user_states.pop(chat_id, None)
        send_message(chat_id, f"✅ Новый промпт сохранен:\n\n«{text.strip()}»", reply_markup=get_settings_keyboard(chat_id))
        return "OK", 200

    if text in ["/start", "/clear"]:
        current = user_models.get(chat_id, "gemini")
        active_name = MODEL_NAMES.get(current, "⚡ Gemini")
        chat_history[chat_id] = [] 
        user_states.pop(chat_id, None)
        
        msg_text = (
            f"🤖 **Доступные нейросети в боте:**\n\n"
            f"👁 **Читают фото и текст:**\n"
            f"⚡ **Gemini** — быстрая, отлично подходит для любых повседневных вопросов.\n"
            f"💬 **ChatGPT** — сбалансированная и надежная классика от OpenAI.\n"
            f"🧠 **Claude** — превосходна в аналитике, коде и рассуждениях.\n"
            f"👁 **Qwen VL** — мощное распознавание деталей на фото и схем.\n"
            f"🌙 **Kimi** — отлично переваривает длинные тексты и статьи.\n\n"
            f"📝 **Только текст:**\n"
            f"🤖 **DeepSeek** — профи в математике, логике и алгоритмах.\n"
            f"🦙 **LLaMA** — огромная база знаний от Meta.\n"
            f"🚀 **Grok** — дерзкая, быстрая, без излишних формальностей.\n\n"
            f"⚙️ **Текущая модель:** {active_name}\n\n"
            f"Выбирай нейросеть внизу или загляни в **⚙️ Настройки**, чтобы настроить стиль общения, формат ответа, интернет и картинки!"
        )
        send_message(chat_id, msg_text, reply_markup=get_main_keyboard())
        return "OK", 200

    if not text and not image_base64:
        return "OK", 200

    current_key = user_models.get(chat_id, "gemini")
    threading.Thread(target=process_ai_request, args=(chat_id, text, current_key, image_base64)).start()
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
