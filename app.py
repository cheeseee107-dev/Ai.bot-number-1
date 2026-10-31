import os
import re
import json
import time
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

ADMIN_ID = 6749129869

SETTINGS_FILE = "user_settings.json"

MODELS = {
    "gpt": "openai/gpt-4o-mini",
    "deepseek": "deepseek/deepseek-chat",
    "claude": "anthropic/claude-haiku-4.5",
    "llama": "meta-llama/llama-3.3-70b-instruct",
    "kimi": "moonshotai/kimi-k2.6",
    "grok": "x-ai/grok-4.5",
    "qwen_vl": "qwen/qwen-2-vl-72b-instruct"
}

PAID_MODELS = {"gpt", "claude", "kimi", "grok", "qwen_vl", "llama", "deepseek"}

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
    "sarcastic": (
        "Отвечай остроумно, с легкой взрослой иронией и сухим юмором, но строго по существу. "
        "Категорически запрещены кривляния, клоунада, токсичность, детские подколки и натужные шутки. "
        "Главный приоритет — точный, грамотный и полезный ответ."
    )
}

STYLE_NAMES = {
    "default": "Обычный 💡",
    "friendly": "Дружелюбный 😊",
    "strict": "Строгий 💼",
    "sarcastic": "Саркастичный 😏"
}

FORMATS = {
    "auto": "Форматируй ответ естественно: структурированный текст с логическими абзацами.",
    "table": (
        "Все основные данные, факты, свойства или сравнения оформляй строго в виде текстовой таблицы. "
        "Обязательно оборачивай всю таблицу целиком в блок кода с тройными обратными кавычками (```текст таблицы```), "
        "чтобы в Telegram она отображалась моноширинным шрифтом с идеально ровными колонками."
    ),
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
    "Ты — полезный ассистент. Не рисуй псевдографику, не ставь числовые сноски источников в конце предложений и не используй технический мусор."
)

chat_history = {}
user_states = {}
known_users = set()
users_data = {}
_supabase_loaded = False

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def clean_ai_response(text):
    if not text:
        return ""
    cleaned = re.sub(r'\[\d+\]', '', text)
    cleaned = re.sub(r'(?<=\.)\d{2,}(?=\s|$)', '', cleaned)
    cleaned = re.sub(r'(?<=\w)\.\d{2,}(?=\s|$)', '.', cleaned)
    return cleaned.strip()

def load_local_settings():
    global users_data, chat_history
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
                users_data = {int(k): v.get("config", v) for k, v in raw.items()}
                for k, v in raw.items():
                    if isinstance(v, dict) and "history" in v:
                        chat_history[int(k)] = v["history"]
        except Exception as e:
            print("Ошибка чтения settings.json:", e)

def save_local_settings():
    try:
        combined = {}
        for uid in set(list(users_data.keys()) + list(chat_history.keys())):
            combined[uid] = {
                "config": users_data.get(uid, {}),
                "history": chat_history.get(uid, [])
            }
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(combined, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Ошибка сохранения settings.json:", e)

load_local_settings()

def get_user_config(chat_id):
    cid = int(chat_id)
    if cid not in users_data:
        users_data[cid] = {
            "model": "gemini",
            "search": False,
            "images": False,
            "style": "default",
            "format": "auto",
            "prompt": DEFAULT_PROMPT,
            "paid_date": "",
            "paid_count": 0,
            "sim_rounds": 8,
            "sim_length": "short"
        }
    return users_data[cid]

def update_user_config(chat_id, key, value):
    cfg = get_user_config(chat_id)
    cfg[key] = value
    save_local_settings()
    threading.Thread(target=save_user_to_supabase_async, args=(chat_id,)).start()

def check_and_update_paid_limit(chat_id, model_key):
    cid = int(chat_id)
    if cid == ADMIN_ID:
        return True
    
    if model_key not in PAID_MODELS:
        return True

    tz_msk = timezone(timedelta(hours=3))
    today_str = datetime.now(tz_msk).strftime("%Y-%m-%d")

    cfg = get_user_config(cid)
    last_date = cfg.get("paid_date", "")
    current_count = cfg.get("paid_count", 0)

    if last_date != today_str:
        cfg["paid_date"] = today_str
        cfg["paid_count"] = 1
        save_local_settings()
        return True
    else:
        if current_count >= 1:
            return False
        cfg["paid_count"] = current_count + 1
        save_local_settings()
        return True

def async_load_supabase():
    global _supabase_loaded
    if not SUPABASE_URL or not SUPABASE_KEY or _supabase_loaded:
        return
    _supabase_loaded = True
    base_url = SUPABASE_URL.strip().rstrip("/")
    headers = {
        "apikey": SUPABASE_KEY.strip(),
        "Authorization": f"Bearer {SUPABASE_KEY.strip()}"
    }
    try:
        res = requests.get(f"{base_url}/rest/v1/bot_users?select=chat_id,settings,history", headers=headers, timeout=5)
        if res.status_code == 200:
            for row in res.json():
                cid = row.get("chat_id")
                if cid:
                    cid = int(cid)
                    known_users.add(cid)
                    saved_settings = row.get("settings")
                    if isinstance(saved_settings, dict):
                        if cid not in users_data:
                            users_data[cid] = {}
                        users_data[cid].update(saved_settings)
                    saved_history = row.get("history")
                    if isinstance(saved_history, list):
                        chat_history[cid] = saved_history
            save_local_settings()
    except Exception as e:
        print("Фоновая загрузка Supabase завершилась:", e)

def save_user_to_supabase_async(chat_id):
    cid = int(chat_id)
    known_users.add(cid)
    if not SUPABASE_URL or not SUPABASE_KEY:
        return

    base_url = SUPABASE_URL.strip().rstrip("/")
    api_key = SUPABASE_KEY.strip()

    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    
    payload = {
        "chat_id": cid,
        "settings": get_user_config(cid),
        "history": chat_history.get(cid, [])[-10:]
    }
    try:
        res = requests.post(f"{base_url}/rest/v1/bot_users", headers=headers, json=payload, timeout=5)
        if res.status_code >= 400:
            headers["Prefer"] = "resolution=ignore-duplicates"
            requests.post(f"{base_url}/rest/v1/bot_users", headers=headers, json={"chat_id": cid}, timeout=5)
    except Exception:
        pass

def send_chat_action_typing(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            requests.post(f"{TELEGRAM_API_URL}/sendChatAction", json={"chat_id": chat_id, "action": "typing"}, timeout=5)
        except Exception:
            pass
        stop_event.wait(4)

def split_text(text, limit=4000):
    if len(text) <= limit:
        return [text]
    parts = []
    while len(text) > limit:
        split_pos = text.rfind("\n", 0, limit)
        if split_pos == -1 or split_pos < limit // 2:
            split_pos = text.rfind(" ", 0, limit)
        if split_pos == -1 or split_pos < limit // 2:
            split_pos = limit
        parts.append(text[:split_pos].strip())
        text = text[split_pos:].strip()
    if text:
        parts.append(text)
    return parts

def send_message(chat_id, text, reply_markup=None):
    if not text:
        return
    parts = split_text(text, 4000)
    for i, part in enumerate(parts):
        payload = {
            "chat_id": chat_id, 
            "text": part,
            "parse_mode": "Markdown"
        }
        if i == len(parts) - 1 and reply_markup:
            payload["reply_markup"] = reply_markup
            
        try:
            response = requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=10)
            if response.status_code == 400:
                del payload["parse_mode"]
                requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=10)
        except Exception as e:
            print("Ошибка sendMessage:", e)

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
            res = requests.post(f"{TELEGRAM_API_URL}/sendPhoto", json=payload, timeout=15)
            if res.status_code != 200:
                del payload["parse_mode"]
                res = requests.post(f"{TELEGRAM_API_URL}/sendPhoto", json=payload, timeout=15)
                if res.status_code != 200:
                    send_message(chat_id, caption_text)
        else:
            requests.post(f"{TELEGRAM_API_URL}/sendPhoto", json={"chat_id": chat_id, "photo": photo_urls[0]}, timeout=15)
            send_message(chat_id, caption_text)
        return

    if len(caption_text) <= 1024:
        media = []
        for i, url in enumerate(photo_urls[:3]):
            item = {"type": "photo", "media": url}
            if i == 0:
                item["caption"] = caption_text
                item["parse_mode"] = "Markdown"
            media.append(item)
        res = requests.post(f"{TELEGRAM_API_URL}/sendMediaGroup", json={"chat_id": chat_id, "media": media}, timeout=20)
        if res.status_code != 200:
            media[0].pop("parse_mode", None)
            res = requests.post(f"{TELEGRAM_API_URL}/sendMediaGroup", json={"chat_id": chat_id, "media": media}, timeout=20)
            if res.status_code != 200:
                send_message(chat_id, caption_text)
    else:
        media = [{"type": "photo", "media": url} for url in photo_urls[:3]]
        requests.post(f"{TELEGRAM_API_URL}/sendMediaGroup", json={"chat_id": chat_id, "media": media}, timeout=20)
        send_message(chat_id, caption_text)

def search_web_snippets(query):
    if not SERPER_API_KEY or not query:
        return ""
    try:
        headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
        res = requests.post("https://google.serper.dev/search", headers=headers, json={"q": query, "num": 4}, timeout=5)
        if res.status_code == 200:
            data = res.json()
            snippets = []
            if "organic" in data:
                for item in data["organic"][:3]:
                    title = item.get("title", "")
                    snippet = item.get("snippet", "")
                    if snippet:
                        snippets.append(f"— {title}: {snippet}")
            if snippets:
                return "\n".join(snippets)
    except Exception as e:
        print("Ошибка Serper Search:", e)
    return ""

def find_image_urls(query):
    clean_query = query.replace("покажи", "").replace("фото", "").replace("картинка", "").replace("изображение", "").strip()
    urls = []

    if SERPER_API_KEY:
        try:
            headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
            res = requests.post("https://google.serper.dev/images", headers=headers, json={"q": clean_query, "num": 5}, timeout=5)
            if res.status_code == 200:
                data = res.json()
                seen = set()
                for img in data.get("images", []):
                    link = img.get("imageUrl")
                    if link and link.startswith("http") and link not in seen:
                        seen.add(link)
                        urls.append(link)
                    if len(urls) >= 3:
                        break
                if urls:
                    return urls
        except Exception as e:
            print("Ошибка Serper Images:", e)

    try:
        url = f"https://en.wikipedia.org/w/api.php?action=query&prop=pageimages&format=json&piprop=original&titles={requests.utils.quote(clean_query)}"
        res = requests.get(url, timeout=3).json()
        pages = res.get("query", {}).get("pages", {})
        for _, val in pages.items():
            if "original" in val:
                urls.append(val["original"]["source"])
                break
    except Exception:
        pass
        
    return urls

def transcribe_voice_via_openrouter(file_url):
    try:
        audio_data = requests.get(file_url, timeout=15).content
        files = {
            "file": ("voice.oga", audio_data, "audio/ogg"),
            "model": (None, "openai/whisper")
        }
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}"
        }
        res = requests.post("https://openrouter.ai/api/v1/audio/transcriptions", headers=headers, files=files, timeout=30)
        if res.status_code == 200:
            return res.json().get("text", "").strip()
    except Exception as e:
        print("Ошибка распознавания Whisper:", e)
    return ""

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
    cfg = get_user_config(chat_id)
    search_status = "ВКЛ 🟢" if cfg.get("search") else "ВЫКЛ 🔴"
    image_status = "ВКЛ 🟢" if cfg.get("images") else "ВЫКЛ 🔴"
    
    rows = [
        [{"text": f"🌐 Поиск: {search_status}"}, {"text": f"🖼 Картинки: {image_status}"}],
        [{"text": "🎭 Стиль общения"}, {"text": "📐 Формат ответа"}],
        [{"text": "✏️ Промпт"}, {"text": "🔄 Сброс настроек"}]
    ]
    if int(chat_id) == ADMIN_ID:
        rows.append([{"text": "👑 Панель админа"}])
        
    rows.append([{"text": "⬅️ Назад в меню"}])
    return {"keyboard": rows, "resize_keyboard": True}

def get_admin_keyboard():
    return {
        "keyboard": [
            [{"text": "🎭 Симулятор"}, {"text": "📢 Сделать рассылку"}],
            [{"text": "📊 Статистика"}, {"text": "⬅️ Назад в настройки"}]
        ],
        "resize_keyboard": True
    }

def get_simulator_keyboard():
    return {
        "keyboard": [
            [{"text": "⚖️ Суд: Судья vs Подсудимый"}, {"text": "♟️ Шахматный блиц"}],
            [{"text": "💡 Философский батл"}, {"text": "✍️ Свой сценарий"}],
            [{"text": "⚙️ Настройки симуляции"}, {"text": "⬅️ Назад в админку"}]
        ],
        "resize_keyboard": True
    }

def get_sim_settings_keyboard(chat_id):
    cfg = get_user_config(chat_id)
    r = cfg.get("sim_rounds", 8)
    l_key = cfg.get("sim_length", "short")
    l_names = {"short": "Кратко ⚡", "medium": "Средне 📝", "long": "Развернуто 📚"}
    
    return {
        "keyboard": [
            [{"text": f"🔄 Количество реплик: {r}"}, {"text": f"📏 Объем: {l_names.get(l_key, 'Кратко')}"}],
            [{"text": "⬅️ Назад в симулятор"}]
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

def call_openrouter_raw(model_id, messages, max_tokens=600):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://render.com",
        "X-Title": "IvanGPT"
    }
    payload = {
        "model": model_id,
        "messages": messages,
        "max_tokens": max_tokens
    }
    return requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60)

def call_openrouter(chat_id, model_id, prompt_text, image_base64=None, search_context=""):
    user_text = prompt_text if prompt_text else "Разбери фото и дай ответ."
    
    if image_base64:
        if any(m in model_id for m in ["deepseek", "llama", "grok"]):
            current_message_content = [{"type": "text", "text": user_text + "\n\n*(Модель без распознавания изображений)*"}]
        else:
            current_message_content = [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
            ]
    else:
        current_message_content = user_text

    cid = int(chat_id)
    if cid not in chat_history:
        chat_history[cid] = []

    cfg = get_user_config(cid)
    active_prompt = cfg.get("prompt", DEFAULT_PROMPT)
    chosen_style_key = cfg.get("style", "default")
    style_instruction = STYLES.get(chosen_style_key, STYLES["default"])

    chosen_format_key = cfg.get("format", "auto")
    format_instruction = FORMATS.get(chosen_format_key, FORMATS["auto"])

    tz_msk = timezone(timedelta(hours=3))
    current_time = datetime.now(tz_msk).strftime("%d.%m.%Y, время: %H:%M (МСК)")
    
    full_system_prompt = (
        f"{active_prompt}\n\n"
        f"Характер общения: {style_instruction}\n"
        f"Требование к оформлению: {format_instruction}\n"
        f"ВАЖНОЕ ПРАВИЛО: Не ставь числовые индексы сносок источников (вроде [1], [2], 1234) в конце предложений.\n\n"
        f"[Системная информация: сейчас {current_time}]"
    )

    if search_context:
        full_system_prompt += f"\n\n[Свежие результаты поиска в сети по запросу]:\n{search_context}\nОпирайся на эти данные при ответе."
    
    messages = [{"role": "system", "content": full_system_prompt}]
    messages.extend(chat_history[cid])
    messages.append({"role": "user", "content": current_message_content})

    return call_openrouter_raw(model_id, messages, max_tokens=1500)

def run_simulation(chat_id, role1_name, role1_prompt, role2_name, role2_prompt, initial_context):
    cid = int(chat_id)
    cfg = get_user_config(cid)
    rounds = cfg.get("sim_rounds", 8)
    sim_length = cfg.get("sim_length", "short")
    
    length_instruction = {
        "short": "ОТВЕЧАЙ МАКСИМАЛЬНО КРАТКО! Строго 1-2 хлестких коротких предложения. Никаких длинных рассуждений и воды.",
        "medium": "Отвечай ровно одним абзацем среднего размера (3-4 предложения).",
        "long": "Отвечай подробно и развернуто, раскрывай свою мысль, используй аргументы."
    }.get(sim_length, "Отвечай кратко.")

    send_message(
        cid,
        f"🎬 **Симулятор запущен!**\n"
        f"🎭 Участники: **{role1_name}** и **{role2_name}**\n"
        f"📌 Тема: _{initial_context}_\n"
        f"⏳ Запланировано реплик: **{rounds}**.\n\n"
        f"Начинаем представление 👇"
    )

    history1 = [{"role": "system", "content": f"{role1_prompt}\n\n{length_instruction}"}]
    history2 = [{"role": "system", "content": f"{role2_prompt}\n\n{length_instruction}"}]

    current_speaker = 1
    last_message = f"Начало диалога. Твоя вводная: {initial_context}"
    model_id = "google/gemini-2.0-flash:free"

    for r in range(1, rounds + 1):
        stop_event = threading.Event()
        t = threading.Thread(target=send_chat_action_typing, args=(cid, stop_event))
        t.start()

        speaker_name = role1_name if current_speaker == 1 else role2_name
        current_history = history1 if current_speaker == 1 else history2
        current_history.append({"role": "user", "content": last_message})

        ans_text = "..."
        try:
            res = call_openrouter_raw(model_id, current_history, max_tokens=600)
            data = res.json()
            if res.status_code == 200 and "choices" in data:
                ans_text = data["choices"][0]["message"].get("content", "").strip()
            else:
                err_res = call_openrouter_raw("google/gemini-2.5-flash", current_history, max_tokens=600).json()
                ans_text = err_res.get("choices", [{}])[0].get("message", {}).get("content", "...")
        except Exception as e:
            ans_text = f"[Сбой генерации: {e}]"
        finally:
            stop_event.set()

        current_history.append({"role": "assistant", "content": ans_text})
        last_message = ans_text

        send_message(cid, f"**{speaker_name}**:\n{ans_text}")

        current_speaker = 2 if current_speaker == 1 else 1
        time.sleep(2)

    send_message(cid, "🏁 **Симуляция завершена!**", reply_markup=get_simulator_keyboard())

def process_ai_request(chat_id, text, current_key, image_base64):
    cid = int(chat_id)
    if not OPENROUTER_API_KEY:
        send_message(cid, "Ошибка: не найден OPENROUTER_API_KEY.")
        return

    if not check_and_update_paid_limit(cid, current_key):
        send_message(
            cid, 
            "⏳ **Дневной лимит исчерпан!**\n\n"
            "Для экономии ресурсов на платные премиум-модели установлен лимит: **1 запрос в день**.\n"
            "Ты можешь бесплатно и без ограничений переключиться на **⚡ Gemini** в главном меню!"
        )
        return

    stop_typing = threading.Event()
    typing_thread = threading.Thread(target=send_chat_action_typing, args=(cid, stop_typing))
    typing_thread.start()

    try:
        cfg = get_user_config(cid)
        use_web = cfg.get("search", False)
        use_img = cfg.get("images", False) and text

        found_img_urls = []
        if use_img:
            found_img_urls = find_image_urls(text)

        search_context = ""
        if use_web and text:
            search_context = search_web_snippets(text)

        answer = None
        last_err = "Ошибка связи с сервером."

        if current_key == "gemini":
            gemini_candidates = ["google/gemini-2.0-flash:free", "google/gemini-2.5-flash"]
            for model_id in gemini_candidates:
                try:
                    response = call_openrouter(cid, model_id, text, image_base64, search_context=search_context)
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
                response = call_openrouter(cid, chosen_model, text, image_base64, search_context=search_context)
                res_data = response.json()
                if response.status_code == 200 and "choices" in res_data:
                    answer = res_data["choices"][0]["message"]["content"]
                else:
                    err = res_data.get("error", {}).get("message", "Неизвестная ошибка")
                    last_err = f"Ошибка ({response.status_code}): {err}"
            except Exception as e:
                last_err = f"Ошибка связи: {str(e)}"

        raw_text = answer or last_err
        final_text = clean_ai_response(raw_text) if answer else raw_text
        
        if found_img_urls and answer:
            send_photos_with_text(cid, found_img_urls, final_text)
        else:
            send_message(cid, final_text)

        if answer:
            text_for_history = text if text else "[Отправлено изображение]"
            if cid not in chat_history:
                chat_history[cid] = []
            chat_history[cid].append({"role": "user", "content": text_for_history})
            chat_history[cid].append({"role": "assistant", "content": final_text})
            
            if len(chat_history[cid]) > 10:
                chat_history[cid] = chat_history[cid][-10:]
            
            save_local_settings()
            threading.Thread(target=save_user_to_supabase_async, args=(cid,)).start()
    finally:
        stop_typing.set()

@app.route("/", methods=["GET"])
def index():
    return "Bot is running!"

@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        global _supabase_loaded
        if not _supabase_loaded:
            threading.Thread(target=async_load_supabase).start()

        data = request.json
        if not data or "message" not in data:
            return "OK", 200

        message = data["message"]
        chat_id = int(message["chat"]["id"])
        text = message.get("text", message.get("caption", ""))
        
        threading.Thread(target=save_user_to_supabase_async, args=(chat_id,)).start()
        cfg = get_user_config(chat_id)
        
        voice = message.get("voice")
        if voice:
            try:
                file_id = voice["file_id"]
                file_info = requests.get(f"{TELEGRAM_API_URL}/getFile?file_id={file_id}", timeout=5).json()
                if file_info.get("ok"):
                    file_path = file_info["result"]["file_path"]
                    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
                    recognized_text = transcribe_voice_via_openrouter(file_url)
                    if recognized_text:
                        text = recognized_text
                        send_message(chat_id, f"🎙 *Распознано:* _{text}_")
                    else:
                        send_message(chat_id, "⚠️ Не удалось разобрать голос.")
                        return "OK", 200
            except Exception as e:
                print("Ошибка голосового:", e)
                return "OK", 200

        photo = message.get("photo")
        image_base64 = None
        if photo:
            try:
                file_id = photo[-1]["file_id"]
                file_info = requests.get(f"{TELEGRAM_API_URL}/getFile?file_id={file_id}", timeout=5).json()
                if file_info.get("ok"):
                    file_path = file_info["result"]["file_path"]
                    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
                    img_data = requests.get(file_url, timeout=10).content
                    image_base64 = base64.b64encode(img_data).decode("utf-8")
            except Exception:
                pass

        if chat_id == ADMIN_ID and user_states.get(chat_id) == "waiting_for_broadcast":
            user_states.pop(chat_id, None)
            success_count = 0
            for uid in list(known_users):
                if photo:
                    file_id = photo[-1]["file_id"]
                    payload = {
                        "chat_id": uid,
                        "photo": file_id,
                        "caption": f"📢 **Уведомление:**\n\n{text}" if text else "📢 **Уведомление:**",
                        "parse_mode": "Markdown"
                    }
                    try:
                        res = requests.post(f"{TELEGRAM_API_URL}/sendPhoto", json=payload, timeout=5)
                        if res.status_code == 200:
                            success_count += 1
                    except Exception:
                        pass
                else:
                    if not text:
                        continue
                    payload = {
                        "chat_id": uid,
                        "text": f"📢 **Уведомление:**\n\n{text}",
                        "parse_mode": "Markdown"
                    }
                    try:
                        res = requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=5)
                        if res.status_code == 200:
                            success_count += 1
                    except Exception:
                        pass
            send_message(chat_id, f"✅ Рассылка успешно отправлена {success_count} пользователям!", reply_markup=get_admin_keyboard())
            return "OK", 200

        if chat_id == ADMIN_ID and user_states.get(chat_id) == "waiting_for_custom_sim" and text:
            user_states.pop(chat_id, None)
            parts = [p.strip() for p in text.split("|")]
            if len(parts) >= 3:
                r1, r2, theme = parts[0], parts[1], parts[2]
                r1_p = f"Ты {r1}. Веди диалог в этом характере с {r2} на тему: {theme}."
                r2_p = f"Ты {r2}. Веди диалог в этом характере с {r1} на тему: {theme}."
                threading.Thread(target=run_simulation, args=(chat_id, f"👤 {r1}", r1_p, f"👥 {r2}", r2_p, theme)).start()
            else:
                send_message(chat_id, "⚠️ Неверный формат! Нужно прислать: `Роль 1 | Роль 2 | Тема`", reply_markup=get_simulator_keyboard())
            return "OK", 200

        if text in ["⚙️ Настройки", "⬅️ Назад в настройки"]:
            user_states.pop(chat_id, None)
            search_status = "Включен 🟢" if cfg.get("search") else "Выключен 🔴"
            image_status = "Включен 🟢" if cfg.get("images") else "Выключен 🔴"
            current_style = STYLE_NAMES.get(cfg.get("style", "default"), "Обычный 💡")
            current_format = FORMAT_NAMES.get(cfg.get("format", "auto"), "Обычный текст 📝")
            current_prompt = cfg.get("prompt", DEFAULT_PROMPT)
            
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

        if text in ["👑 Панель админа", "⬅️ Назад в админку"]:
            if chat_id != ADMIN_ID:
                send_message(chat_id, "⛔ Доступ запрещен.")
                return "OK", 200
            user_states.pop(chat_id, None)
            admin_text = (
                f"👑 **Панель администратора**\n\n"
                f"Здесь доступны закрытые функции управления ботом, рассылки и интерактивный симулятор ролей.\n"
                f"Выбери действие ниже:"
            )
            send_message(chat_id, admin_text, reply_markup=get_admin_keyboard())
            return "OK", 200

        if text in ["🎭 Симулятор", "⬅️ Назад в симулятор"] and chat_id == ADMIN_ID:
            user_states.pop(chat_id, None)
            sim_text = (
                f"🎭 **Мультиагентный Симулятор**\n\n"
                f"Здесь две роли оживают и общаются друг с другом.\n"
                f"Загляни в **⚙️ Настройки симуляции**, чтобы изменить размер текста или количество раундов, либо сразу запускай готовый сценарий:"
            )
            send_message(chat_id, sim_text, reply_markup=get_simulator_keyboard())
            return "OK", 200

        if text == "⚙️ Настройки симуляции" and chat_id == ADMIN_ID:
            send_message(chat_id, "⚙️ Управление параметрами симуляции:", reply_markup=get_sim_settings_keyboard(chat_id))
            return "OK", 200
            
        if text and text.startswith("🔄 Количество реплик:") and chat_id == ADMIN_ID:
            r = cfg.get("sim_rounds", 8)
            new_r = {4: 8, 8: 12, 12: 20, 20: 4}.get(r, 8)
            update_user_config(chat_id, "sim_rounds", new_r)
            send_message(chat_id, f"✅ Количество реплик изменено на **{new_r}**", reply_markup=get_sim_settings_keyboard(chat_id))
            return "OK", 200

        if text and text.startswith("📏 Объем:") and chat_id == ADMIN_ID:
            l = cfg.get("sim_length", "short")
            new_l = {"short": "medium", "medium": "long", "long": "short"}.get(l, "short")
            update_user_config(chat_id, "sim_length", new_l)
            send_message(chat_id, "✅ Насыщенность текста обновлена.", reply_markup=get_sim_settings_keyboard(chat_id))
            return "OK", 200

        if text == "⚖️ Суд: Судья vs Подсудимый" and chat_id == ADMIN_ID:
            r1_name = "👨‍⚖️ Судья"
            r1_prompt = "Ты строгий, неподкупный судья. Допрашиваешь подсудимого, давишь фактами, задаешь острые вопросы."
            r2_name = "🦊 Подсудимый"
            r2_prompt = "Ты хитрый и изворотливый подсудимый. Вину отрицаешь, придумываешь нелепые оправдания, споришь с судьей."
            theme = "Дело о ночной краже всех сосисок из столовой."
            threading.Thread(target=run_simulation, args=(chat_id, r1_name, r1_prompt, r2_name, r2_prompt, theme)).start()
            return "OK", 200

        if text == "♟️ Шахматный блиц" and chat_id == ADMIN_ID:
            r1_name = "⚪ Белые"
            r1_prompt = "Ты высокомерный гроссмейстер, играющий белыми. Пиши свой ход ЖИРНЫМ ШРИФТОМ. ОБЯЗАТЕЛЬНО нарисуй текущую текстовую шахматную ASCII-доску 8x8 в блоке кода ``` в конце ответа."
            r2_name = "⚫ Черные"
            r2_prompt = "Ты дерзкий вундеркинд, играющий черными. Ловишь оппонента, отвечаешь едко. Пиши свой ход ЖИРНЫМ ШРИФТОМ. ОБЯЗАТЕЛЬНО нарисуй текущую текстовую шахматную ASCII-доску 8x8 в блоке кода ``` в конце ответа."
            theme = "Острая сицилианская защита, блиц-партия."
            threading.Thread(target=run_simulation, args=(chat_id, r1_name, r1_prompt, r2_name, r2_prompt, theme)).start()
            return "OK", 200

        if text == "💡 Философский батл" and chat_id == ADMIN_ID:
            r1_name = "🌧️ Пессимист"
            r1_prompt = "Ты мрачный философ-пессимист. Утверждай, что мир полон страданий и суеты."
            r2_name = "☀️ Оптимист"
            r2_prompt = "Ты неунывающий оптимист. Доказывай, что жизнь прекрасна и наука ведет к счастью."
            theme = "Смысл существования человечества в эпоху ИИ."
            threading.Thread(target=run_simulation, args=(chat_id, r1_name, r1_prompt, r2_name, r2_prompt, theme)).start()
            return "OK", 200

        if text == "✍️ Свой сценарий" and chat_id == ADMIN_ID:
            user_states[chat_id] = "waiting_for_custom_sim"
            send_message(
                chat_id,
                "✍️ **Создание своего сценария:**\n\n"
                "Отправь параметры через вертикальную черту в формате:\n"
                "`Персонаж 1 | Персонаж 2 | Тема или ситуация`\n\n"
                "Пример:\n"
                "`Шерлок Холмс | Доктор Ватсон | Таинственное исчезновение чая на Бейкер-стрит`"
            )
            return "OK", 200

        if text == "📢 Сделать рассылку" and chat_id == ADMIN_ID:
            user_states[chat_id] = "waiting_for_broadcast"
            send_message(chat_id, "📢 **Режим создания рассылки:**\n\nОтправь следующим сообщением текст или картинку с описанием, и я разошлю это всем пользователям бота.")
            return "OK", 200

        if text == "📊 Статистика" and chat_id == ADMIN_ID:
            users_count = len(known_users)
            send_message(chat_id, f"📊 **Статистика бота:**\n\n👥 Всего пользователей в базе: **{users_count}**", reply_markup=get_admin_keyboard())
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
            update_user_config(chat_id, "style", style_map[text])
            send_message(chat_id, f"✅ Стиль общения сохранен: **{text}**", reply_markup=get_settings_keyboard(chat_id))
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
            update_user_config(chat_id, "format", format_map[text])
            send_message(chat_id, f"✅ Формат ответа сохранен: **{text}**", reply_markup=get_settings_keyboard(chat_id))
            return "OK", 200

        if text in ["⬅️ Назад в меню", "/menu"]:
            user_states.pop(chat_id, None)
            active_name = MODEL_NAMES.get(cfg.get("model", "gemini"), "⚡ Gemini")
            send_message(
                chat_id,
                f"Главное меню.\nТекущая модель: **{active_name}**\nЗадавай вопрос или выбирай модель 👇",
                reply_markup=get_main_keyboard()
            )
            return "OK", 200

        if text and text.startswith("🌐 Поиск:"):
            new_val = not cfg.get("search", False)
            update_user_config(chat_id, "search", new_val)
            status = "включен 🟢" if new_val else "выключен 🔴"
            send_message(chat_id, f"Веб-поиск сохранен: {status}.", reply_markup=get_settings_keyboard(chat_id))
            return "OK", 200

        if text and text.startswith("🖼 Картинки:"):
            new_val = not cfg.get("images", False)
            update_user_config(chat_id, "images", new_val)
            status = "включен 🟢" if new_val else "выключен 🔴"
            send_message(chat_id, f"Поиск картинок сохранен: {status}.", reply_markup=get_settings_keyboard(chat_id))
            return "OK", 200

        if text == "✏️ Промпт":
            user_states[chat_id] = "waiting_for_prompt"
            send_message(chat_id, "✏️ Напиши следующим сообщением персональный промпт для бота:")
            return "OK", 200

        if text == "🔄 Сброс настроек":
            users_data[chat_id] = {
                "model": cfg.get("model", "gemini"),
                "search": False,
                "images": False,
                "style": "default",
                "format": "auto",
                "prompt": DEFAULT_PROMPT,
                "paid_date": "",
                "paid_count": 0,
                "sim_rounds": 8,
                "sim_length": "short"
            }
            save_local_settings()
            threading.Thread(target=save_user_to_supabase_async, args=(chat_id,)).start()
            user_states.pop(chat_id, None)
            send_message(chat_id, "🔄 Все настройки сброшены на стандартные!", reply_markup=get_settings_keyboard(chat_id))
            return "OK", 200

        if text == "🗑 Память":
            chat_history[chat_id] = []
            save_local_settings()
            threading.Thread(target=save_user_to_supabase_async, args=(chat_id,)).start()
            send_message(chat_id, "🗑 Память диалога полностью очищена.")
            return "OK", 200

        if text in TEXT_TO_MODEL:
            model_key = TEXT_TO_MODEL[text]
            update_user_config(chat_id, "model", model_key)
            chat_history[chat_id] = [] 
            save_local_settings()
            threading.Thread(target=save_user_to_supabase_async, args=(chat_id,)).start()
            user_states.pop(chat_id, None)
            send_message(chat_id, f"Выбрана модель: {text}\nПамять диалога очищена. Можешь задавать вопрос 👇", reply_markup=get_main_keyboard())
            return "OK", 200

        if user_states.get(chat_id) == "waiting_for_prompt" and text:
            update_user_config(chat_id, "prompt", text.strip())
            user_states.pop(chat_id, None)
            send_message(chat_id, f"✅ Новый промпт сохранен:\n\n«{text.strip()}»", reply_markup=get_settings_keyboard(chat_id))
            return "OK", 200

        if text in ["/start", "/clear"]:
            active_name = MODEL_NAMES.get(cfg.get("model", "gemini"), "⚡ Gemini")
            search_status = "Вкл 🟢" if cfg.get("search") else "Выкл 🔴"
            image_status = "Вкл 🟢" if cfg.get("images") else "Выкл 🔴"
            style_name = STYLE_NAMES.get(cfg.get("style", "default"), "Обычный 💡")
            format_name = FORMAT_NAMES.get(cfg.get("format", "auto"), "Обычный текст 📝")
            
            chat_history[chat_id] = [] 
            save_local_settings()
            threading.Thread(target=save_user_to_supabase_async, args=(chat_id,)).start()
            user_states.pop(chat_id, None)
            
            msg_text = (
                f"🤖 **Доступные нейросети в боте:**\n\n"
                f"⚡ **Gemini** — быстрая, без ограничений.\n"
                f"💬 **ChatGPT** — надежная классика от OpenAI.\n"
                f"🧠 **Claude** — логика, разборы и код.\n"
                f"👁 **Qwen VL** — распознавание деталей на фото.\n"
                f"🌙 **Kimi** — длинные тексты и документы.\n"
                f"🤖 **DeepSeek** — профи в математике и коде.\n"
                f"🦙 **LLaMA** — огромная база знаний от Meta.\n"
                f"🚀 **Grok** — дерзкая и быстрая.\n\n"
                f"⚙️ **Твои активные настройки:**\n"
                f"• Модель: **{active_name}**\n"
                f"• Веб-поиск: **{search_status}**\n"
                f"• Картинки к ответам: **{image_status}**\n"
                f"• Стиль общения: **{style_name}**\n"
                f"• Формат: **{format_name}**\n\n"
                f"Задавай вопрос, меняй нейросеть или загляни в **⚙️ Настройки**!"
            )
            send_message(chat_id, msg_text, reply_markup=get_main_keyboard())
            return "OK", 200

        if not text and not image_base64:
            return "OK", 200

        current_key = cfg.get("model", "gemini")
        threading.Thread(target=process_ai_request, args=(chat_id, text, current_key, image_base64)).start()
        return "OK", 200
    except Exception as e:
        print("Критическая ошибка вебхука:", e)
        return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
