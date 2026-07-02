import hmac
import hashlib
import json
import os
import time

import requests
from flask import Flask, jsonify, request

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Подписываем api_key тем же секретом, что и токен бота — отдельный секрет не
# нужен. Так chat_id зашит прямо в ключе, и сервер не хранит вообще никакого
# состояния — это переживает перезапуски/засыпание на бесплатном тарифе Render,
# где файлы на диске не сохраняются между перезапусками контейнера.
SIGNING_KEY = TELEGRAM_BOT_TOKEN.encode()

RATE_LIMIT_SECONDS = 3
_last_notify_at = {}

# Команды, ожидающие выполнения клиентом (например "найти игру" по кнопке из
# Telegram). Как и остальное состояние сервера — только в памяти: теряется
# при перезапуске на Render, но команда живёт секунды, пока клиент её не
# заберёт следующим опросом, так что это не проблема.
_pending_commands = {}

FIND_MATCH_BUTTON_TEXT = "🔍 Найти игру"

app = Flask(__name__)


def make_api_key(chat_id):
    chat_id = str(chat_id)
    signature = hmac.new(SIGNING_KEY, chat_id.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{chat_id}.{signature}"


def verify_api_key(api_key):
    try:
        chat_id, signature = api_key.split(".", 1)
    except ValueError:
        return None

    expected = hmac.new(SIGNING_KEY, chat_id.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(signature, expected):
        return None
    return chat_id


def send_telegram_message(chat_id, text, reply_markup=None, parse_mode=None):
    data = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        data["reply_markup"] = json.dumps(reply_markup)
    if parse_mode is not None:
        data["parse_mode"] = parse_mode
        data["disable_web_page_preview"] = True
    requests.post(
        f"{TELEGRAM_API_URL}/sendMessage",
        data=data,
        timeout=10,
    )


def main_keyboard():
    return {
        "keyboard": [[{"text": FIND_MATCH_BUTTON_TEXT}]],
        "resize_keyboard": True,
    }


def help_text():
    return (
        "🎮 <b>Dota 2 Notifier</b>\n\n"
        "Я слежу за экраном твоего компьютера и пишу сюда, когда в Dota 2 "
        "находится игра — а ещё умею сам нажать «Принять» за тебя.\n\n"
        "<b>Как настроить (один раз):</b>\n"
        "1. Скачай приложение: https://github.com/HolloW52/dota-notifier/releases/latest\n"
        "2. Вставь свой персональный код в приложение на вкладке «Подключение» "
        "(код выше, в сообщении от /start)\n"
        "3. Запусти приложение и оставь его работать в фоне\n\n"
        "<b>Что я умею:</b>\n"
        f"• {FIND_MATCH_BUTTON_TEXT} — кнопка ниже запускает поиск матча прямо "
        "из Telegram. Dota 2 должна быть уже открыта на главном меню.\n"
        "• Уведомление, когда найдена игра.\n"
        "• Автопринятие с задержкой — включается в самом приложении.\n\n"
        "<b>Команды:</b>\n"
        "/start — получить (или показать снова) персональный код\n"
        "/help — показать эту инструкцию ещё раз"
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}
    message = update.get("message", {})
    text = message.get("text", "")
    chat_id = message.get("chat", {}).get("id")

    if chat_id is None:
        return jsonify({"ok": True})

    command = text.strip().lower()

    if command.startswith("/start"):
        api_key = make_api_key(chat_id)
        send_telegram_message(
            chat_id,
            f"Твой персональный код: <code>{api_key}</code>\n\n" + help_text(),
            reply_markup=main_keyboard(),
            parse_mode="HTML",
        )
    elif command.startswith("/help"):
        send_telegram_message(chat_id, help_text(), reply_markup=main_keyboard(), parse_mode="HTML")
    elif text.strip() == FIND_MATCH_BUTTON_TEXT:
        _pending_commands[str(chat_id)] = "find_match"
        send_telegram_message(
            chat_id,
            "Принято — как только приложение на компьютере увидит главное меню Dota 2, начну поиск игры.",
        )
    else:
        send_telegram_message(chat_id, "Не понял команду. Напиши /help, чтобы увидеть инструкцию.")

    return jsonify({"ok": True})


@app.route("/poll-command", methods=["GET"])
def poll_command():
    api_key = request.args.get("api_key")
    chat_id = verify_api_key(api_key) if api_key else None
    if chat_id is None:
        return jsonify({"ok": False, "error": "Неизвестный api_key"}), 404

    command = _pending_commands.pop(chat_id, None)
    return jsonify({"ok": True, "command": command})


@app.route("/notify", methods=["POST"])
def notify():
    data = request.get_json(silent=True) or {}
    api_key = data.get("api_key")
    message = data.get("message")

    if not api_key or not message:
        return jsonify({"ok": False, "error": "api_key и message обязательны"}), 400

    now = time.monotonic()
    last_at = _last_notify_at.get(api_key)
    if last_at is not None and now - last_at < RATE_LIMIT_SECONDS:
        return jsonify({"ok": False, "error": "Слишком частые запросы, подожди немного"}), 429
    _last_notify_at[api_key] = now

    chat_id = verify_api_key(api_key)
    if chat_id is None:
        return jsonify({"ok": False, "error": "Неизвестный api_key"}), 404

    send_telegram_message(chat_id, message)
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
