import json
import os
import sys
import time

import pyautogui
import requests
from PIL import Image

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
TEMPLATE_PATHS = [
    os.path.join(SCRIPT_DIR, "accept_button_ru.png"),
    os.path.join(SCRIPT_DIR, "accept_button_en.png"),
]

CHECK_INTERVAL_SECONDS = 1.0
MATCH_CONFIDENCE = 0.8

# Адрес сервера-релея по умолчанию для нового конфига. Обновляется один раз
# после деплоя сервера на Render — отдельный шаг настройки, не пользовательский ввод.
DEFAULT_SERVER_URL = "https://ЗАМЕНИ-НА-АДРЕС-СЕРВЕРА.onrender.com"


def load_config():
    if not os.path.isfile(CONFIG_PATH):
        default_config = {
            "server_url": DEFAULT_SERVER_URL,
            "api_key": "",
            "auto_accept": True,
        }
        save_config(default_config)
        return default_config

    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def ensure_api_key(config):
    if config.get("api_key"):
        return config

    print("Это первый запуск — нужно привязать приложение к твоему Telegram.")
    print("1. Напиши /start боту @dota2_notify_bot в Telegram.")
    print("2. Бот пришлёт тебе персональный код.")
    api_key = input("3. Вставь этот код сюда и нажми Enter: ").strip()

    config["api_key"] = api_key
    save_config(config)
    print("Код сохранён. Продолжаю запуск...\n")
    return config


def load_templates():
    templates = []
    for path in TEMPLATE_PATHS:
        if os.path.isfile(path):
            templates.append(Image.open(path))
    if not templates:
        print("Не найдено ни одной картинки кнопки Accept (accept_button_ru.png / accept_button_en.png).")
        sys.exit(1)
    return templates


def send_notification(server_url, api_key, message):
    try:
        response = requests.post(
            f"{server_url}/notify",
            json={"api_key": api_key, "message": message},
            timeout=10,
        )
        if response.status_code != 200:
            print(f"Сервер ответил с ошибкой ({response.status_code}): {response.text}")
    except requests.RequestException as e:
        print(f"Не получилось связаться с сервером: {e}")


def find_button(templates):
    for template in templates:
        try:
            location = pyautogui.locateOnScreen(template, confidence=MATCH_CONFIDENCE)
        except Exception:
            location = None
        if location is not None:
            return location
    return None


def main():
    config = load_config()
    config = ensure_api_key(config)

    server_url = config["server_url"].rstrip("/")
    api_key = config["api_key"]
    auto_accept = config.get("auto_accept", False)

    templates = load_templates()

    print("Скрипт запущен. Слежу за экраном, жду появления кнопки Accept...")
    print("Чтобы остановить — нажми Ctrl+C в этом окне.")

    button_was_visible = False

    while True:
        location = find_button(templates)
        button_is_visible = location is not None

        if button_is_visible and not button_was_visible:
            print("Найдена игра! Отправляю уведомление в Telegram...")
            if auto_accept:
                pyautogui.click(pyautogui.center(location))
                send_notification(server_url, api_key, "🎮 Игра найдена! Принял за тебя автоматически.")
            else:
                send_notification(server_url, api_key, "🎮 Игра найдена! Жми Accept!")

        button_was_visible = button_is_visible
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
