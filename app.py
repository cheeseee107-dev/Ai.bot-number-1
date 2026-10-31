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
    "sarcastic": "Отвечай остроумно, с легкой взрослой иронией и сухим юмором, но строго по существу. Категорически запрещены кривляния."
}

STYLE_NAMES = {
    "default": "Обычный 💡",
    "friendly": "Дружелюбный 😊",
    "strict": "Строгий 💼",
    "sarcastic": "Саркастичный 😏"
}

FORMATS = {
    "auto": "Форматируй ответ естественно: структурированный текст с логическими абзацами.",
    "table": "Все основные данные, факты, свойства или сравнения оформляй строго в виде текстовой таблицы. Обязательно оборачивай всю таблицу целиком в блок кода с тройными обратными кавычками.",
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
DEFAULT_PROMPT = "Ты — полезный ассистент. Не рисуй псевдографику, не ставь числовые сноски источников в конце предложений."

chat_history = {}
user_states = {}
known_users = set()
users_data = {}
_supabase_loaded = False

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def log_error(err_text):
    admin_cfg = get_admin_config()
    if "errors" not in admin_cfg:
        admin_cfg["errors"] = []
    tz_msk = timezone(timedelta(hours=3))
    now_str = datetime.now(tz_msk).strftime("%d.%m %H:%M")
    admin_cfg["errors"].insert(0, f"[{now_str}] {err_text}")
    admin_cfg["errors"] = admin_cfg["errors"][:10]
    save_local_settings()

def get_admin_config():
    if 0 not in users_data:
        users_data[0] = {
            "daily_limit": 1,
            "blacklist": [],
            "errors": []
        }
    return users_data[0]

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
            "sim_length": 5,
            "sim_logic": 8,
            "last_active": ""
        }
    return users_data[cid]

def update_user_config(chat_id, key, value):
    cfg = get_user_config(chat_id)
    cfg[key] = value
    save_local_settings()
    threading.Thread(target=save_user_to_supabase_async, args=(chat_id,)).start()

def update_activity(chat_id):
    tz_msk = timezone(timedelta(hours=3))
    today_str = datetime.now(tz_msk).strftime("%Y-%m-%d")
    cfg = get_user_config(chat_id)
    cfg["last_active"] = today_str

def check_and_update_paid_limit(chat_id, model_key):
    cid = int(chat_id)
    if cid == ADMIN_ID:
        return True
    if model_key not in PAID_MODELS:
        return True

    tz_msk = timezone(timedelta(hours=3))
    today_str = datetime.now(tz_msk).strftime("%Y-%m-%d")
    cfg = get_user_config(cid)
    admin_cfg = get_admin_config()
    limit = admin_cfg.get("daily_limit", 1)

    last_date = cfg.get("paid_date", "")
    current_count = cfg.get("paid_count", 0)

    if last_date != today_str:
        cfg["paid_date"] = today_str
        cfg["paid_count"] = 1
        save_local_settings()
        return limit > 0
    else:
        if current_count >= limit:
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
        print("Фоновая загрузка Supabase:", e)

def save_user_to_supabase_async(chat_id):
    cid = int(chat_id)
    if cid == 0:
        return
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
    except Exception as e:
        log_error(f"Supabase save: {str(e)}")

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
            log_error(f"SendMessage: {str(e)}")

def send_photos_with_text(chat_id, photo_urls, caption_text):
    if not photo_urls:
        send_message(chat_id, caption_text)
        return
    if len(photo_urls) == 1:
        if len(caption_text) <= 1024:
            payload = {"chat_id": chat_id, "photo": photo_urls[0], "caption": caption_text, "parse_mode": "Markdown"}
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
        media = [{"type": "photo", "media": url} for url in photo_urls[:3]]
        media[0]["caption"] = caption_text
        media[0]["parse_mode"] = "Markdown"
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
        log_error(f"Serper Search: {str(e)}")
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
            log_error(f"Serper Images: {str(e)}")
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
    search_status = "ВКЛ" if cfg.get("search") else "ВЫКЛ"
    image_status = "ВКЛ" if cfg.get("images") else "ВЫКЛ"
    
    rows = [
        [{"text": f"🌐 Поиск: {search_status}"}, {"text": f"🖼 Картинки: {image_status}"}],
        [{"text": "🎭 Стиль общения"}, {"text": "📐 Формат ответа"}],
        [{"text": "✏️ Промпт"}, {"text": "🔄 Сброс настроек"}]
    ]
    if int(chat_id) == ADMIN_ID:
        rows.append([{"text": "Панель администратора"}])
    rows.append([{"text": "Назад"}])
    return {"keyboard": rows, "resize_keyboard": True}

def get_admin_keyboard():
    return {
        "keyboard": [
            [{"text": "Симулятор"}, {"text": "Рассылка"}],
            [{"text": "Статистика"}, {"text": "Лимиты"}],
            [{"text": "Черный список"}, {"text": "Журнал ошибок"}],
            [{"text": "Назад"}]
        ],
        "resize_keyboard": True
    }

def get_simulator_keyboard():
    return {
        "keyboard": [
            [{"text": "Суд: Судья vs Подсудимый"}, {"text": "Шахматный блиц"}],
            [{"text": "Философский батл"}, {"text": "Свой сценарий"}],
            [{"text": "Настройки симуляции"}, {"text": "Назад в админку"}]
        ],
        "resize_keyboard": True
    }

def get_sim_settings_keyboard(chat_id):
    cfg = get_user_config(chat_id)
    r = cfg.get("sim_rounds", 8)
    l = cfg.get("sim_length", 5)
    s = cfg.get("sim_logic", 8)
    
    return {
        "keyboard": [
            [{"text": f"Длина ответов (1-10): {l}"}],
            [{"text": f"Цельность/Логика (1-10): {s}"}],
            [{"text": f"Количество раундов: {r}"}],
            [{"text": "Назад в симулятор"}]
        ],
        "resize_keyboard": True
    }

def call_openrouter_raw(model_id, messages, max_tokens=1000):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://render.com",
        "X-Title": "IvanGPT"
    }
    payload = {"model": model_id, "messages": messages, "max_tokens": max_tokens}
    return requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60)

def generate_sim_instructions(length_val, logic_val):
    if length_val <= 3:
        len_str = "Отвечай максимально кратко, строго 1-2 предложения. Избегай воды."
    elif length_val <= 7:
        len_str = "Отвечай средним абзацем, 3-5 предложений. Давай суть без лишних предисловий."
    else:
        len_str = "Отвечай развернуто и подробно, аргументируй позицию несколькими абзацами."

    if logic_val <= 3:
        log_str = "Веди себя максимально эмоционально, театрально и наигранно. Полностью погрузись в роль с перегибами."
    elif logic_val <= 6:
        log_str = "Держи баланс между эмоциями роли и фактами."
    else:
        log_str = "Убери любую театральность и наигранность. Отвечай предельно серьезно, опираясь исключительно на логику, факты и цельность аргументации. Сохраняй позицию персонажа, но говори сухим языком фактов."

    return f"{len_str}\n{log_str}"

def run_simulation(chat_id, role1_name, role1_prompt, role2_name, role2_prompt, initial_context):
    cid = int(chat_id)
    cfg = get_user_config(cid)
    rounds = cfg.get("sim_rounds", 8)
    
    l_val = cfg.get("sim_length", 5)
    s_val = cfg.get("sim_logic", 8)
    instructions = generate_sim_instructions(l_val, s_val)

    send_message(cid, f"**Симулятор запущен**\nУчастники: {role1_name} и {role2_name}\nТема: {initial_context}\nРаундов: {rounds}")

    history1 = [{"role": "system", "content": f"{role1_prompt}\n\nИНСТРУКЦИЯ К ФОРМАТУ:\n{instructions}"}]
    history2 = [{"role": "system", "content": f"{role2_prompt}\n\nИНСТРУКЦИЯ К ФОРМАТУ:\n{instructions}"}]

    current_speaker = 1
    last_message = f"Начало диалога. Ситуация: {initial_context}"
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
            res = call_openrouter_raw(model_id, current_history, max_tokens=1000)
            data = res.json()
            if res.status_code == 200 and "choices" in data:
                ans_text = data["choices"][0]["message"].get("content", "").strip()
            else:
                err_res = call_openrouter_raw("google/gemini-2.5-flash", current_history, max_tokens=1000).json()
                ans_text = err_res.get("choices", [{}])[0].get("message", {}).get("content", "...")
        except Exception as e:
            ans_text = f"[Сбой генерации: {e}]"
            log_error(f"Sim error: {str(e)}")
        finally:
            stop_event.set()

        current_history.append({"role": "assistant", "content": ans_text})
        last_message = ans_text

        send_message(cid, f"**{speaker_name}**:\n{ans_text}")
        current_speaker = 2 if current_speaker == 1 else 1
        time.sleep(2)

    send_message(cid, "Симуляция завершена.", reply_markup=get_simulator_keyboard())

def process_ai_request(chat_id, text, current_key, image_base64):
    cid = int(chat_id)
    if not OPENROUTER_API_KEY:
        send_message(cid, "Ошибка: не найден OPENROUTER_API_KEY.")
        return

    if not check_and_update_paid_limit(cid, current_key):
        admin_cfg = get_admin_config()
        limit = admin_cfg.get("daily_limit", 1)
        send_message(
            cid, 
            f"Дневной лимит исчерпан. Для экономии ресурсов установлен лимит: {limit} запрос(ов) в день.\nПереключись на Gemini в главном меню."
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

        user_text = text if text else "Разбери фото и дай ответ."
        
        if image_base64:
            if any(m in current_key for m in ["deepseek", "llama", "grok"]):
                msg_content = [{"type": "text", "text": user_text + "\n\n*(Модель без распознавания изображений)*"}]
            else:
                msg_content = [{"type": "text", "text": user_text}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}]
        else:
            msg_content = user_text

        if cid not in chat_history:
            chat_history[cid] = []

        active_prompt = cfg.get("prompt", DEFAULT_PROMPT)
        style_inst = STYLES.get(cfg.get("style", "default"), STYLES["default"])
        format_inst = FORMATS.get(cfg.get("format", "auto"), FORMATS["auto"])
        
        sys_prompt = f"{active_prompt}\n\nХарактер общения: {style_inst}\nОформление: {format_inst}\nВАЖНОЕ ПРАВИЛО: Не ставь индексы сносок в конце предложений."
        if search_context:
            sys_prompt += f"\n\n[Результаты поиска]:\n{search_context}"

        messages = [{"role": "system", "content": sys_prompt}]
        messages.extend(chat_history[cid])
        messages.append({"role": "user", "content": msg_content})

        if current_key == "gemini":
            gemini_candidates = ["google/gemini-2.0-flash:free", "google/gemini-2.5-flash"]
            for model_id in gemini_candidates:
                try:
                    res = call_openrouter_raw(model_id, messages, max_tokens=1500)
                    res_data = res.json()
                    if res.status_code == 200 and "choices" in res_data:
                        answer = res_data["choices"][0]["message"].get("content")
                        if answer:
                            break
                    else:
                        last_err = f"Ошибка ({res.status_code}): {res_data.get('error', {}).get('message', '')}"
                except Exception as e:
                    last_err = f"Ошибка связи: {str(e)}"
        else:
            chosen_model = MODELS.get(current_key, MODELS["gpt"])
            try:
                res = call_openrouter_raw(chosen_model, messages, max_tokens=1500)
                res_data = res.json()
                if res.status_code == 200 and "choices" in res_data:
                    answer = res_data["choices"][0]["message"]["content"]
                else:
                    last_err = f"Ошибка ({res.status_code}): {res_data.get('error', {}).get('message', '')}"
            except Exception as e:
                last_err = f"Ошибка связи: {str(e)}"

        raw_text = answer or last_err
        final_text = clean_ai_response(raw_text) if answer else raw_text
        
        if not answer:
            log_error(f"Chat error ({cid}): {last_err}")
            
        if found_img_urls and answer:
            send_photos_with_text(cid, found_img_urls, final_text)
        else:
            send_message(cid, final_text)

        if answer:
            text_for_history = text if text else "[Отправлено изображение]"
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
        
        admin_cfg = get_admin_config()
        if chat_id in admin_cfg.get("blacklist", []):
            return "OK", 200

        update_activity(chat_id)
        threading.Thread(target=save_user_to_supabase_async, args=(chat_id,)).start()
        cfg = get_user_config(chat_id)
        
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
            except Exception as e:
                log_error(f"Image load: {str(e)}")

        if chat_id == ADMIN_ID and user_states.get(chat_id) == "waiting_for_sim_length" and text:
            try:
                val = int(text.strip())
                if 1 <= val <= 10:
                    update_user_config(chat_id, "sim_length", val)
                    send_message(chat_id, "Длина ответов сохранена.", reply_markup=get_sim_settings_keyboard(chat_id))
                else:
                    send_message(chat_id, "Введи число строго от 1 до 10.", reply_markup=get_sim_settings_keyboard(chat_id))
            except:
                send_message(chat_id, "Неверный формат.", reply_markup=get_sim_settings_keyboard(chat_id))
            user_states.pop(chat_id, None)
            return "OK", 200

        if chat_id == ADMIN_ID and user_states.get(chat_id) == "waiting_for_sim_logic" and text:
            try:
                val = int(text.strip())
                if 1 <= val <= 10:
                    update_user_config(chat_id, "sim_logic", val)
                    send_message(chat_id, "Уровень серьезности сохранен.", reply_markup=get_sim_settings_keyboard(chat_id))
                else:
                    send_message(chat_id, "Введи число строго от 1 до 10.", reply_markup=get_sim_settings_keyboard(chat_id))
            except:
                send_message(chat_id, "Неверный формат.", reply_markup=get_sim_settings_keyboard(chat_id))
            user_states.pop(chat_id, None)
            return "OK", 200

        if chat_id == ADMIN_ID and user_states.get(chat_id) == "waiting_for_sim_rounds" and text:
            try:
                val = int(text.strip())
                if 2 <= val <= 50:
                    update_user_config(chat_id, "sim_rounds", val)
                    send_message(chat_id, "Количество раундов сохранено.", reply_markup=get_sim_settings_keyboard(chat_id))
                else:
                    send_message(chat_id, "Введи число от 2 до 50.", reply_markup=get_sim_settings_keyboard(chat_id))
            except:
                send_message(chat_id, "Неверный формат.", reply_markup=get_sim_settings_keyboard(chat_id))
            user_states.pop(chat_id, None)
            return "OK", 200

        if chat_id == ADMIN_ID and user_states.get(chat_id) == "waiting_for_limit" and text:
            try:
                new_limit = int(text.strip())
                admin_cfg["daily_limit"] = new_limit
                save_local_settings()
                send_message(chat_id, f"Дневной лимит платных моделей установлен на: {new_limit}", reply_markup=get_admin_keyboard())
            except ValueError:
                send_message(chat_id, "Ошибка. Отправь число.", reply_markup=get_admin_keyboard())
            user_states.pop(chat_id, None)
            return "OK", 200

        if chat_id == ADMIN_ID and user_states.get(chat_id) == "waiting_for_ban" and text:
            try:
                ban_id = int(text.strip())
                if ban_id not in admin_cfg["blacklist"]:
                    admin_cfg["blacklist"].append(ban_id)
                    save_local_settings()
                send_message(chat_id, f"ID {ban_id} добавлен в черный список.", reply_markup=get_admin_keyboard())
            except ValueError:
                send_message(chat_id, "Неверный формат ID.", reply_markup=get_admin_keyboard())
            user_states.pop(chat_id, None)
            return "OK", 200

        if chat_id == ADMIN_ID and user_states.get(chat_id) == "waiting_for_unban" and text:
            try:
                unban_id = int(text.strip())
                if unban_id in admin_cfg["blacklist"]:
                    admin_cfg["blacklist"].remove(unban_id)
                    save_local_settings()
                send_message(chat_id, f"ID {unban_id} удален из черного списка.", reply_markup=get_admin_keyboard())
            except ValueError:
                send_message(chat_id, "Неверный формат ID.", reply_markup=get_admin_keyboard())
            user_states.pop(chat_id, None)
            return "OK", 200

        if chat_id == ADMIN_ID and user_states.get(chat_id) == "waiting_for_broadcast" and (text or photo):
            user_states.pop(chat_id, None)
            success = 0
            for uid in list(known_users):
                if uid in admin_cfg.get("blacklist", []):
                    continue
                if photo:
                    file_id = photo[-1]["file_id"]
                    payload = {"chat_id": uid, "photo": file_id, "caption": f"Уведомление:\n\n{text}" if text else "Уведомление:", "parse_mode": "Markdown"}
                    try:
                        res = requests.post(f"{TELEGRAM_API_URL}/sendPhoto", json=payload, timeout=5)
                        if res.status_code == 200: success += 1
                    except: pass
                elif text:
                    payload = {"chat_id": uid, "text": f"Уведомление:\n\n{text}", "parse_mode": "Markdown"}
                    try:
                        res = requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=5)
                        if res.status_code == 200: success += 1
                    except: pass
            send_message(chat_id, f"Рассылка отправлена {success} пользователям.", reply_markup=get_admin_keyboard())
            return "OK", 200

        if chat_id == ADMIN_ID and user_states.get(chat_id) == "waiting_for_custom_sim" and text:
            user_states.pop(chat_id, None)
            parts = [p.strip() for p in text.split("|")]
            if len(parts) >= 3:
                r1, r2, theme = parts[0], parts[1], parts[2]
                r1_p = f"Ты {r1}. Веди диалог с {r2} на тему: {theme}."
                r2_p = f"Ты {r2}. Веди диалог с {r1} на тему: {theme}."
                threading.Thread(target=run_simulation, args=(chat_id, r1, r1_p, r2, r2_p, theme)).start()
            else:
                send_message(chat_id, "Неверный формат. Нужно прислать: Роль 1 | Роль 2 | Тема", reply_markup=get_simulator_keyboard())
            return "OK", 200

        if text in ["⚙️ Настройки", "Назад"]:
            user_states.pop(chat_id, None)
            search_status = "ВКЛ" if cfg.get("search") else "ВЫКЛ"
            image_status = "ВКЛ" if cfg.get("images") else "ВЫКЛ"
            
            settings_text = (
                f"Настройки бота:\n\n"
                f"Поиск в сети: {search_status}\n"
                f"Прикреплять картинки: {image_status}\n"
                f"Стиль: {STYLE_NAMES.get(cfg.get('style', 'default'))}\n"
                f"Формат: {FORMAT_NAMES.get(cfg.get('format', 'auto'))}\n"
                f"Промпт: {cfg.get('prompt', DEFAULT_PROMPT)}\n"
            )
            send_message(chat_id, settings_text, reply_markup=get_settings_keyboard(chat_id))
            return "OK", 200

        if text in ["Панель администратора", "Назад в админку"]:
            if chat_id != ADMIN_ID:
                return "OK", 200
            user_states.pop(chat_id, None)
            send_message(chat_id, "Панель администратора", reply_markup=get_admin_keyboard())
            return "OK", 200

        if text == "Лимиты" and chat_id == ADMIN_ID:
            limit = admin_cfg.get("daily_limit", 1)
            user_states[chat_id] = "waiting_for_limit"
            send_message(chat_id, f"Текущий лимит платных моделей: {limit} в день.\nОтправь новое число.", reply_markup=get_admin_keyboard())
            return "OK", 200

        if text == "Черный список" and chat_id == ADMIN_ID:
            banned = admin_cfg.get("blacklist", [])
            banned_str = ", ".join(map(str, banned)) if banned else "Пусто"
            send_message(chat_id, f"Заблокированные ID: {banned_str}\n\nУправление:\n/ban ID\n/unban ID", reply_markup=get_admin_keyboard())
            return "OK", 200

        if text.startswith("/ban ") and chat_id == ADMIN_ID:
            user_states[chat_id] = "waiting_for_ban"
            telegram_webhook.text = text.replace("/ban ", "").strip()
            return "OK", 200
            
        if text.startswith("/unban ") and chat_id == ADMIN_ID:
            user_states[chat_id] = "waiting_for_unban"
            telegram_webhook.text = text.replace("/unban ", "").strip()
            return "OK", 200

        if text == "Журнал ошибок" and chat_id == ADMIN_ID:
            errors = admin_cfg.get("errors", [])
            err_text = "\n\n".join(errors) if errors else "Ошибок не зафиксировано."
            send_message(chat_id, f"Последние ошибки:\n\n{err_text}", reply_markup=get_admin_keyboard())
            return "OK", 200

        if text == "Статистика" and chat_id == ADMIN_ID:
            tz_msk = timezone(timedelta(hours=3))
            today_str = datetime.now(tz_msk).strftime("%Y-%m-%d")
            total_users = len(known_users)
            active_today = sum(1 for u in users_data.values() if isinstance(u, dict) and u.get("last_active") == today_str)
            send_message(chat_id, f"Статистика:\nВсего юзеров: {total_users}\nАктивных сегодня: {active_today}", reply_markup=get_admin_keyboard())
            return "OK", 200

        if text in ["Симулятор", "Назад в симулятор"] and chat_id == ADMIN_ID:
            user_states.pop(chat_id, None)
            send_message(chat_id, "Мультиагентный Симулятор", reply_markup=get_simulator_keyboard())
            return "OK", 200

        if text == "Настройки симуляции" and chat_id == ADMIN_ID:
            send_message(chat_id, "Параметры симуляции:", reply_markup=get_sim_settings_keyboard(chat_id))
            return "OK", 200
            
        if text and text.startswith("Длина ответов") and chat_id == ADMIN_ID:
            user_states[chat_id] = "waiting_for_sim_length"
            send_message(chat_id, "Отправь число от 1 (максимально кратко) до 10 (подробно).")
            return "OK", 200

        if text and text.startswith("Цельность/Логика") and chat_id == ADMIN_ID:
            user_states[chat_id] = "waiting_for_sim_logic"
            send_message(chat_id, "Отправь число от 1 (полная театральность) до 10 (сухие факты и аргументы без эмоций).")
            return "OK", 200
            
        if text and text.startswith("Количество раундов") and chat_id == ADMIN_ID:
            user_states[chat_id] = "waiting_for_sim_rounds"
            send_message(chat_id, "Отправь количество реплик (например, 8).")
            return "OK", 200

        if text == "Суд: Судья vs Подсудимый" and chat_id == ADMIN_ID:
            r1_p = "Ты судья. Допрашиваешь подсудимого."
            r2_p = "Ты подсудимый. Отвечаешь на вопросы."
            threading.Thread(target=run_simulation, args=(chat_id, "Судья", r1_p, "Подсудимый", r2_p, "Дело о краже")).start()
            return "OK", 200

        if text == "Шахматный блиц" and chat_id == ADMIN_ID:
            r1_p = "Ты шахматист (белые). КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО пытаться рисовать доску символами. Называй свой ход строго в алгебраической нотации (например, e4) и психологически дави на оппонента."
            r2_p = "Ты шахматист (черные). КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО пытаться рисовать доску символами. Называй свой ход строго в алгебраической нотации (например, c5) и психологически дави на оппонента."
            threading.Thread(target=run_simulation, args=(chat_id, "Белые", r1_p, "Черные", r2_p, "Шахматная партия без доски")).start()
            return "OK", 200

        if text == "Философский батл" and chat_id == ADMIN_ID:
            r1_p = "Ты пессимист."
            r2_p = "Ты оптимист."
            threading.Thread(target=run_simulation, args=(chat_id, "Пессимист", r1_p, "Оптимист", r2_p, "Смысл жизни")).start()
            return "OK", 200

        if text == "Свой сценарий" and chat_id == ADMIN_ID:
            user_states[chat_id] = "waiting_for_custom_sim"
            send_message(chat_id, "Отправь: Персонаж 1 | Персонаж 2 | Тема")
            return "OK", 200

        if text == "Рассылка" and chat_id == ADMIN_ID:
            user_states[chat_id] = "waiting_for_broadcast"
            send_message(chat_id, "Отправь сообщение для рассылки.")
            return "OK", 200

        if text == "🎭 Стиль общения":
            send_message(chat_id, "Выбери характер общения бота:", reply_markup=get_style_keyboard())
            return "OK", 200

        style_map = {"💡 Обычный": "default", "😊 Дружелюбный": "friendly", "💼 Строгий": "strict", "😏 Саркастичный": "sarcastic"}
        if text in style_map:
            update_user_config(chat_id, "style", style_map[text])
            send_message(chat_id, "Стиль сохранен.", reply_markup=get_settings_keyboard(chat_id))
            return "OK", 200

        if text == "📐 Формат ответа":
            send_message(chat_id, "Выбери формат:", reply_markup=get_format_keyboard())
            return "OK", 200

        format_map = {"📝 Текст": "auto", "📊 Таблица": "table", "📌 По пунктам": "bullets", "⚡ Кратко (TL;DR)": "short"}
        if text in format_map:
            update_user_config(chat_id, "format", format_map[text])
            send_message(chat_id, "Формат сохранен.", reply_markup=get_settings_keyboard(chat_id))
            return "OK", 200

        if text in ["⬅️ Назад в меню", "/menu"]:
            user_states.pop(chat_id, None)
            active_name = MODEL_NAMES.get(cfg.get("model", "gemini"), "⚡ Gemini")
            send_message(chat_id, f"Главное меню.\nТекущая модель: {active_name}\nЗадавай вопрос", reply_markup=get_main_keyboard())
            return "OK", 200

        if text and text.startswith("🌐 Поиск:"):
            new_val = not cfg.get("search", False)
            update_user_config(chat_id, "search", new_val)
            send_message(chat_id, "Веб-поиск обновлен.", reply_markup=get_settings_keyboard(chat_id))
            return "OK", 200

        if text and text.startswith("🖼 Картинки:"):
            new_val = not cfg.get("images", False)
            update_user_config(chat_id, "images", new_val)
            send_message(chat_id, "Поиск картинок обновлен.", reply_markup=get_settings_keyboard(chat_id))
            return "OK", 200

        if text == "✏️ Промпт":
            user_states[chat_id] = "waiting_for_prompt"
            send_message(chat_id, "Напиши системный промпт:")
            return "OK", 200

        if text == "🔄 Сброс настроек":
            users_data[chat_id] = {"model": "gemini", "search": False, "images": False, "style": "default", "format": "auto", "prompt": DEFAULT_PROMPT, "paid_date": "", "paid_count": 0, "sim_rounds": 8, "sim_length": 5, "sim_logic": 8, "last_active": ""}
            save_local_settings()
            threading.Thread(target=save_user_to_supabase_async, args=(chat_id,)).start()
            user_states.pop(chat_id, None)
            send_message(chat_id, "Все настройки сброшены.", reply_markup=get_settings_keyboard(chat_id))
            return "OK", 200

        if text == "🗑 Память":
            chat_history[chat_id] = []
            save_local_settings()
            threading.Thread(target=save_user_to_supabase_async, args=(chat_id,)).start()
            send_message(chat_id, "Память диалога очищена.")
            return "OK", 200

        if text in TEXT_TO_MODEL:
            model_key = TEXT_TO_MODEL[text]
            update_user_config(chat_id, "model", model_key)
            chat_history[chat_id] = [] 
            save_local_settings()
            threading.Thread(target=save_user_to_supabase_async, args=(chat_id,)).start()
            user_states.pop(chat_id, None)
            send_message(chat_id, f"Выбрана модель: {text}\nПамять очищена.", reply_markup=get_main_keyboard())
            return "OK", 200

        if user_states.get(chat_id) == "waiting_for_prompt" and text:
            update_user_config(chat_id, "prompt", text.strip())
            user_states.pop(chat_id, None)
            send_message(chat_id, "Новый промпт сохранен.", reply_markup=get_settings_keyboard(chat_id))
            return "OK", 200

        if text in ["/start", "/clear"]:
            active_name = MODEL_NAMES.get(cfg.get("model", "gemini"), "⚡ Gemini")
            chat_history[chat_id] = [] 
            save_local_settings()
            threading.Thread(target=save_user_to_supabase_async, args=(chat_id,)).start()
            user_states.pop(chat_id, None)
            send_message(chat_id, f"Привет. Выбрана нейросеть: {active_name}", reply_markup=get_main_keyboard())
            return "OK", 200

        if not text and not image_base64:
            return "OK", 200

        current_key = cfg.get("model", "gemini")
        threading.Thread(target=process_ai_request, args=(chat_id, text, current_key, image_base64)).start()
        return "OK", 200
    except Exception as e:
        log_error(f"Webhook crit: {str(e)}")
        return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
