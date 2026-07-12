"""Минимальная самопроверка для _check_party_invite (rising-edge + переключатель).
Запуск: python test_monitor.py
"""
import queue
from unittest.mock import patch

import monitor


def make_worker(auto_accept_party_invite):
    config = {"auto_accept_party_invite": auto_accept_party_invite, "server_url": "http://x", "api_key": "k"}
    worker = monitor.MonitorWorker(lambda: config, queue.Queue())
    worker.party_invite_templates = ["fake_template"]
    return worker


with patch.object(monitor, "send_notification", return_value=(True, "")), \
     patch("pyautogui.click") as click:

    # Выключено в конфиге — не кликает, даже если кнопка на экране.
    worker = make_worker(False)
    with patch.object(monitor, "find_button", return_value=(0, 0, 10, 10)):
        assert worker._check_party_invite(False) is False
    assert click.call_count == 0

    # Включено, кнопка появилась — кликает один раз.
    worker = make_worker(True)
    with patch.object(monitor, "find_button", return_value=(0, 0, 10, 10)):
        assert worker._check_party_invite(False) is True
    assert click.call_count == 1

    # Кнопка всё ещё видна на следующем тике — повторный клик не нужен.
    with patch.object(monitor, "find_button", return_value=(0, 0, 10, 10)):
        assert worker._check_party_invite(True) is True
    assert click.call_count == 1

print("OK")
