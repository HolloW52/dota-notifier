"""Минимальная самопроверка для _check_party_invite и _apply_roles.
Запуск: python test_monitor.py
"""
import queue
from collections import namedtuple
from unittest.mock import patch

import monitor

Box = namedtuple("Box", ["left", "top", "width", "height"])


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

# _apply_roles: клик — по чекбоксу (левый край строки), не по геометрическому
# центру шаблона; панель ролей уже открыта -> клик по "Играть" не нужен;
# состояние на экране меняется не сразу -> нужен повтор клика, пока не сойдётся.
with patch.object(monitor, "send_notification", return_value=(True, "")), \
     patch("pyautogui.click") as click, patch("time.sleep"):

    worker = make_worker()
    worker.play_templates = ["fake_play"]
    worker.role_templates = {
        key: {"on": [f"{key}_on"], "off": [f"{key}_off"]} for key in monitor.ROLE_KEYS
    }

    boxes = {key: Box(100 + i * 50, 10, 200, 30) for i, key in enumerate(monitor.ROLE_KEYS)}
    state = {key: (key == "carry") for key in monitor.ROLE_KEYS}  # изначально включена только carry
    mid_clicks = [0]

    def fake_find_button(templates):
        if templates == ["fake_play"]:
            raise AssertionError("панель ролей уже открыта — клик по \"Играть\" не нужен")
        if not templates:
            return None
        name = templates[0]
        for key in monitor.ROLE_KEYS:
            if name == f"{key}_on":
                return boxes[key] if state[key] else None
            if name == f"{key}_off":
                return boxes[key] if not state[key] else None
        return None

    def fake_click(x, y):
        for key, box in boxes.items():
            if (x, y) == (box.left + 15, box.top + box.height // 2):
                if key == "mid":
                    # "Центр" переключается только со второго клика — имитация
                    # того самого "иногда срабатывает только со второго раза".
                    mid_clicks[0] += 1
                    state[key] = mid_clicks[0] >= 2
                else:
                    state[key] = not state[key]
                return
        raise AssertionError(f"клик пришёлся мимо чекбокса: ({x}, {y})")

    click.side_effect = fake_click
    with patch.object(monitor, "find_button", side_effect=fake_find_button):
        # carry уже включена (клик не нужен), mid — выключена, нужно включить.
        worker._apply_roles(["carry", "mid"], "http://x", "k")

    assert state == {"carry": True, "mid": True, "offlane": False, "support": False, "hard_support": False}
    # Два клика по "mid" (не сработал с первого раза) и ни одного по "carry"
    # или по "Играть" — панель уже была открыта.
    assert click.call_count == 2

print("OK")
