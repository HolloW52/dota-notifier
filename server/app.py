import hmac
import hashlib
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


def send_telegram_message(chat_id, text):
    requests.post(
        f"{TELEGRAM_API_URL}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=10,
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

    if text.strip().lower().startswith("/start"):
        api_key = make_api_key(chat_id)
        send_telegram_message(
            chat_id,
            "Привет! Вот твой персональный код для приложения Dota 2 Notifier:\n\n"
            f"{api_key}\n\n"
            "Вставь его в приложение при первом запуске.",
        )
    else:
        send_telegram_message(chat_id, "Напиши /start, чтобы получить код для приложения.")

    return jsonify({"ok": True})


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
