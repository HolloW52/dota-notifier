"""Минимальная самопроверка для _check_party_invite (rising-edge + переключатель).
Запуск: python test_monitor.py
"""
import queue
from unittest.mock import patch

import monitor


def make_worker(auto_accept_party_invite=False, confirm_before_accept=False):
    config = {
        "auto_accept_party_invite": auto_accept_party_invite,
        "confirm_before_accept": confirm_before_accept,
        "server_url": "http://x", "api_key": "k",
    }
    worker = monitor.MonitorWorker(lambda: config, queue.Queue())
    worker.party_invite_templates = ["fake_template"]
    return worker


with patch.object(monitor, "send_notification", return_value=(True, "")), \
     patch("pyautogui.click") as click:

    # Выключено в конфиге — не кликает, даже если кнопка на экране.
    worker = make_worker(auto_accept_party_invite=False)
    with patch.object(monitor, "find_button", return_value=(0, 0, 10, 10)):
        assert worker._check_party_invite(False) is False
    assert click.call_count == 0

    # Включено, кнопка появилась — кликает один раз.
    worker = make_worker(auto_accept_party_invite=True)
    with patch.object(monitor, "find_button", return_value=(0, 0, 10, 10)):
        assert worker._check_party_invite(False) is True
    assert click.call_count == 1

    # Кнопка всё ещё видна на следующем тике — повторный клик не нужен.
    with patch.object(monitor, "find_button", return_value=(0, 0, 10, 10)):
        assert worker._check_party_invite(True) is True
    assert click.call_count == 1

# _apply_roles: кликает только то, что не совпадает с желаемым состоянием.
with patch.object(monitor, "send_notification", return_value=(True, "")), \
     patch("pyautogui.click") as click, patch("time.sleep"):

    worker = make_worker()
    worker.play_templates = ["fake_play"]

    def fake_find_button(templates):
        # "carry" уже включена (найдётся её "on"-шаблон), остальные роли
        # выключены — найдётся их "off"-шаблон. play_templates находится сразу.
        if templates == ["fake_play"]:
            return (0, 0, 10, 10)
        if templates == ["carry_on"]:
            return (1, 1, 2, 2)
        if templates and templates[0].endswith("_off"):
            return (3, 3, 4, 4)
        return None

    worker.role_templates = {
        key: {"on": [f"{key}_on"], "off": [f"{key}_off"]} for key in monitor.ROLE_KEYS
    }

    with patch.object(monitor, "find_button", side_effect=fake_find_button):
        # Хотим carry (уже включена — клик не нужен) и mid (выключена — нужен клик).
        worker._apply_roles(["carry", "mid"], "http://x", "k")

    # Кликов должно быть ровно 2: один по вкладке "Играть" (открыть панель) и
    # один по "mid" — carry уже в нужном состоянии, остальные роли не трогаем.
    assert click.call_count == 2

print("OK")
