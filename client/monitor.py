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

# Столько же ждём ответа "принять/отклонить" из Telegram на найденную игру —
# держим в рамках реального окна принятия в Dota 2 (см. MAX_DELAY_SECONDS
# в dota_notifier.py, тот же запас на надёжность).
DECISION_POLL_TIMEOUT_SECONDS = 25

ROLE_KEYS = ["carry", "mid", "offlane", "support", "hard_support"]


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


def load_find_match_disabled_templates():
    return load_image_templates(["find_match_disabled_ru.png", "find_match_disabled_en.png"])


def load_party_invite_templates():
    return load_image_templates(["party_invite_accept_ru.png", "party_invite_accept_en.png"])


def load_decline_templates():
    return load_image_templates(["decline_game_ru.png", "decline_game_en.png"])


def load_role_templates():
    """Для каждой роли — картинки строки в состоянии "выбрано" (on) и
    "не выбрано" (off), чтобы по найденному шаблону сразу знать текущее
    состояние чекбокса, не отдельным способом чтения пикселей."""
    templates = {}
    for key in ROLE_KEYS:
        templates[key] = {
            "on": load_image_templates([f"role_{key}_on_ru.png", f"role_{key}_on_en.png"]),
            "off": load_image_templates([f"role_{key}_off_ru.png", f"role_{key}_off_en.png"]),
        }
    return templates


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


def request_decision(server_url, api_key, title):
    try:
        response = requests.post(
            f"{server_url}/ask-decision",
            json={"api_key": api_key, "title": title},
            timeout=10,
        )
        return response.status_code == 200, response.text
    except requests.RequestException as e:
        return False, str(e)


def poll_decision(server_url, api_key):
    try:
        response = requests.get(
            f"{server_url}/poll-decision",
            params={"api_key": api_key},
            timeout=10,
        )
        if response.status_code != 200:
            return None
        return response.json().get("decision")
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
        self.find_match_disabled_templates = load_find_match_disabled_templates()
        self.party_invite_templates = load_party_invite_templates()
        self.decline_templates = load_decline_templates()
        self.role_templates = load_role_templates()

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
        if not self.play_templates:
            return
        config = self.get_config()
        server_url = config["server_url"].rstrip("/")
        api_key = config.get("api_key", "")
        if not api_key:
            return

        command = poll_pending_command(server_url, api_key)
        if not command:
            return

        command_type = command.get("type") if isinstance(command, dict) else command
        if command_type == "find_match":
            if self.find_match_templates:
                self._search_for_match(server_url, api_key)
        elif command_type == "set_roles":
            self._apply_roles(command.get("roles") or [], server_url, api_key)

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

        # Шаг 2: дождаться открытия вкладки и нажать "Найти игру" — заодно
        # следим за серой (неактивной) версией той же кнопки: Dota показывает
        # её вместо зелёной, если для ролевого поиска не выбрано ни одной роли,
        # и тогда зелёная кнопка вообще не появится, сколько ни жди.
        self._emit("status", text="Ищу кнопку \"Найти игру\"...")
        while time.monotonic() < deadline and not self._stop_flag.is_set():
            location = find_button(self.find_match_templates)
            if location is not None:
                pyautogui.click(pyautogui.center(location))
                self._notify(server_url, api_key, "🔍 Начал поиск игры.")
                self._emit("status", text="Слежу за экраном...")
                return
            if find_button(self.find_match_disabled_templates) is not None:
                self._notify(
                    server_url, api_key,
                    "⚠️ Роли не выбраны — сначала выбери роли (кнопка «🧭 Роли» в Telegram), потом жми «Начать игру».",
                )
                self._emit("status", text="Слежу за экраном...")
                return
            time.sleep(POLL_INTERVAL_SECONDS)

        self._notify(
            server_url, api_key,
            "Нажал \"Играть\", но не нашёл кнопку \"Найти игру\" — попробуй запустить поиск вручную.",
        )
        self._emit("status", text="Слежу за экраном...")

    def _find_role_state(self, key):
        """(is_on, location) для роли — по тому, какой из двух шаблонов
        (выбрана/не выбрана) сейчас совпал с экраном. location=None, если
        строка роли вообще не видна (например, экран ещё не открыт)."""
        templates = self.role_templates.get(key, {})
        location = find_button(templates.get("on", []))
        if location is not None:
            return True, location
        location = find_button(templates.get("off", []))
        if location is not None:
            return False, location
        return None, None

    def _apply_roles(self, desired_roles, server_url, api_key):
        """Открывает экран "Играть" (где Dota показывает ролевой поиск) и
        кликает только те чекбоксы ролей, чьё текущее состояние на экране
        не совпадает с желаемым — не трогает то, что уже верно."""
        if not any(self.role_templates.get(key, {}).get("on") or self.role_templates.get(key, {}).get("off") for key in ROLE_KEYS):
            self._notify(server_url, api_key, "Не найдено картинок ролей в приложении — обратись к разработчику.")
            return

        desired = set(desired_roles)

        # Если панель ролей уже открыта (например, после предыдущей попытки) —
        # не тратим время на повторный клик по "Играть" и ожидание вкладки.
        already_open = any(self._find_role_state(key)[0] is not None for key in ROLE_KEYS)
        if not already_open:
            deadline = time.monotonic() + FIND_MATCH_TIMEOUT_SECONDS
            if not self._wait_and_click(self.play_templates, "Открываю выбор ролей...", deadline):
                self._notify(server_url, api_key, "Не нашёл главное меню Dota 2 — открой игру и попробуй ещё раз.")
                self._emit("status", text="Слежу за экраном...")
                return
            self._emit("status", text="Настраиваю роли...")
            time.sleep(POLL_INTERVAL_SECONDS)  # дать панели ролей отрисоваться после клика

        failed = []
        for key in ROLE_KEYS:
            want_on = key in desired
            is_on, location = self._find_role_state(key)
            if location is None:
                continue  # роль не видна на экране — пропускаем, не гадаем
            if is_on == want_on:
                continue

            # Клик — по чекбоксу у левого края строки, а не по геометрическому
            # центру шаблона: у коротких названий ("Центр", "Лёгкая") справа
            # от текста в шаблоне остаётся пустой фон, и клик по нему Dota не
            # засчитывает как выбор роли.
            click_x = location.left + 15
            click_y = location.top + location.height // 2

            matched = False
            for _ in range(3):
                pyautogui.click(click_x, click_y)
                time.sleep(0.4)
                is_on, _ = self._find_role_state(key)
                if is_on == want_on:
                    matched = True
                    break
            if not matched:
                failed.append(key)

        if failed:
            self._notify(server_url, api_key, f"⚠️ Не удалось применить часть ролей ({len(failed)}) — попробуй ещё раз.")
        else:
            self._notify(server_url, api_key, "✅ Роли обновлены.")
        self._emit("status", text="Слежу за экраном...")

    def _handle_found(self, location):
        config = self.get_config()
        server_url = config["server_url"].rstrip("/")
        api_key = config.get("api_key", "")
        auto_accept = config.get("auto_accept", False)
        confirm_before_accept = config.get("confirm_before_accept", False)
        delay = max(0, int(config.get("auto_accept_delay_seconds", 0)))

        self._emit("found")

        if confirm_before_accept:
            self._resolve_via_telegram(server_url, api_key)
            return

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

    def _resolve_via_telegram(self, server_url, api_key):
        """Вместо автоклика — спрашивает решение в Telegram и ждёт ответ,
        пока попап Dota ещё виден (или пока не истечёт окно принятия)."""
        self._emit("status", text="Жду решения в Telegram...")
        ok, info = request_decision(server_url, api_key, "Игра найдена!")
        if not ok:
            self._emit("error", message=f"Не получилось спросить решение: {info}")
            return

        deadline = time.monotonic() + DECISION_POLL_TIMEOUT_SECONDS
        decision = None
        while time.monotonic() < deadline and not self._stop_flag.is_set():
            decision = poll_decision(server_url, api_key)
            if decision:
                break
            if find_button(self.templates) is None:
                self._emit("status", text="Слежу за экраном...")
                return
            time.sleep(POLL_INTERVAL_SECONDS)

        if decision == "accept":
            current = find_button(self.templates)
            if current is not None:
                self._click_and_notify(current, server_url, api_key)
            return

        if decision == "decline":
            decline_location = find_button(self.decline_templates)
            if decline_location is not None:
                pyautogui.click(pyautogui.center(decline_location))
                self._notify(server_url, api_key, "❌ Игра отклонена.")
            else:
                self._notify(server_url, api_key, "Не нашёл кнопку \"Отклонить игру\" на экране — отклони вручную.")

        self._emit("status", text="Слежу за экраном...")

    def _notify(self, server_url, api_key, message):
        ok, info = send_notification(server_url, api_key, message)
        if not ok:
            self._emit("error", message=f"Не получилось отправить уведомление: {info}")
