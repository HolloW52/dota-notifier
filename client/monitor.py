import os
import sys
import threading
import time

import pyautogui
import requests
from PIL import Image

MATCH_CONFIDENCE = 0.8
POLL_INTERVAL_SECONDS = 1.0
COUNTDOWN_TICK_SECONDS = 1.0

# Команда из Telegram опрашивается реже скрина — это просто лёгкий GET к
# серверу, незачем дёргать его раз в секунду на каждого пользователя.
COMMAND_POLL_EVERY_TICKS = 5

# Сколько ждём появления кнопки "Найти игру" после команды из Telegram —
# даём время вручную открыть Dota 2 и дойти до главного меню.
FIND_MATCH_TIMEOUT_SECONDS = 60


def get_bundle_dir():
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def load_image_templates(filenames):
    bundle_dir = get_bundle_dir()
    templates = []
    for filename in filenames:
        path = os.path.join(bundle_dir, filename)
        if os.path.isfile(path):
            templates.append(Image.open(path))
    return templates


def load_templates():
    return load_image_templates(["accept_button_ru.png", "accept_button_en.png"])


def load_play_templates():
    return load_image_templates(["play_button_ru.png", "play_button_en.png"])


def load_find_match_templates():
    return load_image_templates(["find_match_button_ru.png", "find_match_button_en.png"])


def load_party_invite_templates():
    return load_image_templates(["party_invite_accept_ru.png", "party_invite_accept_en.png"])


def find_button(templates):
    for template in templates:
        try:
            location = pyautogui.locateOnScreen(template, confidence=MATCH_CONFIDENCE)
        except Exception:
            location = None
        if location is not None:
            return location
    return None


def poll_pending_command(server_url, api_key):
    try:
        response = requests.get(
            f"{server_url}/poll-command",
            params={"api_key": api_key},
            timeout=10,
        )
        if response.status_code != 200:
            return None
        return response.json().get("command")
    except requests.RequestException:
        return None


def send_notification(server_url, api_key, message):
    try:
        response = requests.post(
            f"{server_url}/notify",
            json={"api_key": api_key, "message": message},
            # Бесплатный Render засыпает при простое и может просыпаться
            # 50+ секунд — короткий таймаут принимали за "не пришло".
            timeout=70,
        )
        return response.status_code == 200, response.text
    except requests.RequestException as e:
        return False, str(e)


class MonitorWorker(threading.Thread):
    """Следит за экраном в фоне и кладёт события в очередь для GUI."""

    def __init__(self, get_config, event_queue):
        super().__init__(daemon=True)
        self.get_config = get_config
        self.event_queue = event_queue
        self._stop_flag = threading.Event()
        self._cancel_countdown_flag = threading.Event()
        self.templates = load_templates()
        self.play_templates = load_play_templates()
        self.find_match_templates = load_find_match_templates()
        self.party_invite_templates = load_party_invite_templates()

    def stop(self):
        self._stop_flag.set()

    def cancel_pending_accept(self):
        self._cancel_countdown_flag.set()

    def _emit(self, event_type, **data):
        self.event_queue.put({"type": event_type, **data})

    def run(self):
        if not self.templates:
            self._emit("error", message="Не найдено картинок кнопки Accept.")
            return

        self._emit("status", text="Слежу за экраном...")
        button_was_visible = False
        party_invite_was_visible = False
        tick = 0

        while not self._stop_flag.is_set():
            location = find_button(self.templates)
            button_is_visible = location is not None

            if button_is_visible and not button_was_visible:
                self._handle_found(location)

            button_was_visible = button_is_visible

            party_invite_was_visible = self._check_party_invite(party_invite_was_visible)

            tick += 1
            if tick % COMMAND_POLL_EVERY_TICKS == 0:
                self._check_pending_command()

            time.sleep(POLL_INTERVAL_SECONDS)

    def _check_party_invite(self, was_visible):
        """Возвращает новое состояние видимости — вызывающий код хранит его
        между тиками, как и для кнопки Accept, чтобы не кликать по одному и
        тому же всплывающему приглашению повторно, пока оно не исчезнет."""
        if not self.party_invite_templates:
            return False

        config = self.get_config()
        if not config.get("auto_accept_party_invite", False):
            return False

        location = find_button(self.party_invite_templates)
        is_visible = location is not None

        if is_visible and not was_visible:
            pyautogui.click(pyautogui.center(location))
            server_url = config["server_url"].rstrip("/")
            api_key = config.get("api_key", "")
            self._notify(server_url, api_key, "🎉 Заявка в пати принята автоматически.")
            self._emit("status", text="Слежу за экраном...")

        return is_visible

    def _check_pending_command(self):
        if not self.play_templates or not self.find_match_templates:
            return
        config = self.get_config()
        server_url = config["server_url"].rstrip("/")
        api_key = config.get("api_key", "")
        if not api_key:
            return

        command = poll_pending_command(server_url, api_key)
        if command == "find_match":
            self._search_for_match(server_url, api_key)

    def _wait_and_click(self, templates, status_text, deadline):
        self._emit("status", text=status_text)
        while time.monotonic() < deadline and not self._stop_flag.is_set():
            location = find_button(templates)
            if location is not None:
                pyautogui.click(pyautogui.center(location))
                return True
            time.sleep(POLL_INTERVAL_SECONDS)
        return False

    def _search_for_match(self, server_url, api_key):
        deadline = time.monotonic() + FIND_MATCH_TIMEOUT_SECONDS

        # Шаг 1: из главного меню открыть вкладку "Играть" — кнопка "Найти
        # игру" появляется только после неё, это отдельный экран.
        if not self._wait_and_click(self.play_templates, "Ищу кнопку \"Играть\"...", deadline):
            self._notify(
                server_url, api_key,
                "Не нашёл кнопку \"Играть\" — открой Dota 2 и зайди в главное меню, попробуй ещё раз.",
            )
            self._emit("status", text="Слежу за экраном...")
            return

        # Шаг 2: дождаться открытия вкладки и нажать "Найти игру".
        if not self._wait_and_click(self.find_match_templates, "Ищу кнопку \"Найти игру\"...", deadline):
            self._notify(
                server_url, api_key,
                "Нажал \"Играть\", но не нашёл кнопку \"Найти игру\" — попробуй запустить поиск вручную.",
            )
            self._emit("status", text="Слежу за экраном...")
            return

        self._notify(server_url, api_key, "🔍 Начал поиск игры.")
        self._emit("status", text="Слежу за экраном...")

    def _handle_found(self, location):
        config = self.get_config()
        server_url = config["server_url"].rstrip("/")
        api_key = config.get("api_key", "")
        auto_accept = config.get("auto_accept", False)
        delay = max(0, int(config.get("auto_accept_delay_seconds", 0)))

        self._emit("found")

        if not auto_accept:
            self._notify(server_url, api_key, "🎮 Игра найдена! Жми Accept!")
            return

        if delay == 0:
            self._click_and_notify(location, server_url, api_key)
            return

        self._cancel_countdown_flag.clear()
        self._emit("countdown_start", seconds=delay)
        for remaining in range(delay, 0, -1):
            if self._cancel_countdown_flag.is_set():
                self._emit("countdown_cancelled")
                self._notify(server_url, api_key, "🎮 Игра найдена! Жми Accept!")
                return
            if find_button(self.templates) is None:
                self._emit("countdown_cancelled")
                return
            self._emit("countdown_tick", seconds_left=remaining)
            time.sleep(COUNTDOWN_TICK_SECONDS)

        final_location = find_button(self.templates)
        if final_location is None:
            self._emit("countdown_cancelled")
            return

        self._click_and_notify(final_location, server_url, api_key)

    def _click_and_notify(self, location, server_url, api_key):
        pyautogui.click(pyautogui.center(location))
        self._notify(server_url, api_key, "🎮 Игра найдена! Принял за тебя автоматически.")
        self._emit("accepted")

    def _notify(self, server_url, api_key, message):
        ok, info = send_notification(server_url, api_key, message)
        if not ok:
            self._emit("error", message=f"Не получилось отправить уведомление: {info}")
