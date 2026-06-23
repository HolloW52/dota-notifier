import os
import secrets
import sqlite3
import time

import requests
from flask import Flask, jsonify, request

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registrations.db")

RATE_LIMIT_SECONDS = 3
_last_notify_at = {}

app = Flask(__name__)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS registrations ("
        "api_key TEXT PRIMARY KEY, "
        "chat_id TEXT NOT NULL"
        ")"
    )
    return conn


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
        api_key = secrets.token_urlsafe(16)
        conn = get_db()
        conn.execute(
            "INSERT INTO registrations (api_key, chat_id) VALUES (?, ?)",
            (api_key, str(chat_id)),
        )
        conn.commit()
        conn.close()

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

    conn = get_db()
    row = conn.execute(
        "SELECT chat_id FROM registrations WHERE api_key = ?", (api_key,)
    ).fetchone()
    conn.close()

    if row is None:
        return jsonify({"ok": False, "error": "Неизвестный api_key"}), 404

    chat_id = row[0]
    send_telegram_message(chat_id, message)
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
