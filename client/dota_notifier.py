import datetime
import json
import os
import queue
import sys
import threading

import customtkinter as ctk
import pystray
from PIL import Image, ImageTk

import monitor

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = sys._MEIPASS
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = APP_DIR

CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "dota_notifier.log")

# Адрес сервера-релея по умолчанию для нового конфига. Обновляется один раз
# после деплоя сервера на Render — отдельный шаг настройки, не пользовательский ввод.
DEFAULT_SERVER_URL = "https://dota-notifier.onrender.com"

COLOR_BG = "#1a0e0e"
COLOR_PANEL = "#241313"
COLOR_RED = "#6b1010"
COLOR_RED_HOVER = "#8a1818"
COLOR_GOLD = "#c9a227"
COLOR_GOLD_HOVER = "#e0b93a"
COLOR_TEXT = "#f1e6c8"


def default_config():
    return {
        "server_url": DEFAULT_SERVER_URL,
        "api_key": "",
        "auto_accept": True,
        "auto_accept_delay_seconds": 3,
    }


def load_config():
    if not os.path.isfile(CONFIG_PATH):
        config = default_config()
        save_config(config)
        return config

    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        config = json.load(f)

    changed = False
    for key, value in default_config().items():
        if key not in config:
            config[key] = value
            changed = True
    if changed:
        save_config(config)
    return config


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


class DotaNotifierApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.config_data = load_config()
        self.event_queue = queue.Queue()
        self.tray_icon = None

        self._build_ui()
        self._apply_icon()
        self.worker = monitor.MonitorWorker(lambda: self.config_data, self.event_queue)
        self.worker.start()
        self._start_tray()

        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self.after(150, self._poll_queue)

    # ---------- UI ----------

    def _build_ui(self):
        ctk.set_appearance_mode("dark")
        self.title("Dota 2 Notifier")
        self.geometry("420x600")
        self.configure(fg_color=COLOR_BG)
        self.resizable(False, False)

        header = ctk.CTkFrame(self, fg_color=COLOR_RED, corner_radius=0, height=70)
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text="DOTA 2 NOTIFIER",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=COLOR_GOLD,
        ).pack(pady=18)

        self.status_label = ctk.CTkLabel(
            self,
            text="Запуск...",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=COLOR_TEXT,
        )
        self.status_label.pack(pady=(16, 4))

        self.log_box = ctk.CTkTextbox(self, height=160, fg_color=COLOR_PANEL, text_color=COLOR_TEXT)
        self.log_box.pack(fill="x", padx=20, pady=4)
        self.log_box.configure(state="disabled")

        self.countdown_frame = ctk.CTkFrame(self, fg_color=COLOR_PANEL)
        self.countdown_label = ctk.CTkLabel(
            self.countdown_frame,
            text="",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLOR_GOLD,
        )
        self.countdown_label.pack(pady=(10, 4))
        self.cancel_button = ctk.CTkButton(
            self.countdown_frame,
            text="Отмена",
            fg_color=COLOR_RED,
            hover_color=COLOR_RED_HOVER,
            command=self._on_cancel_countdown,
        )
        self.cancel_button.pack(pady=(0, 10))

        settings = ctk.CTkFrame(self, fg_color=COLOR_PANEL)
        settings.pack(fill="x", padx=20, pady=16)

        switch_row = ctk.CTkFrame(settings, fg_color="transparent")
        switch_row.pack(fill="x", padx=14, pady=(14, 6))
        ctk.CTkLabel(switch_row, text="Автопринятие", text_color=COLOR_TEXT).pack(side="left")
        self.auto_accept_switch = ctk.CTkSwitch(
            switch_row,
            text="",
            progress_color=COLOR_GOLD,
            command=self._on_toggle_auto_accept,
        )
        self.auto_accept_switch.pack(side="right")
        if self.config_data.get("auto_accept", True):
            self.auto_accept_switch.select()
        else:
            self.auto_accept_switch.deselect()

        delay_row = ctk.CTkFrame(settings, fg_color="transparent")
        delay_row.pack(fill="x", padx=14, pady=6)
        self.delay_value_label = ctk.CTkLabel(
            delay_row,
            text=f"Задержка перед автопринятием: {self.config_data.get('auto_accept_delay_seconds', 3)} сек",
            text_color=COLOR_TEXT,
        )
        self.delay_value_label.pack(anchor="w")
        self.delay_slider = ctk.CTkSlider(
            settings,
            from_=0,
            to=20,
            number_of_steps=20,
            progress_color=COLOR_GOLD,
            button_color=COLOR_GOLD,
            button_hover_color=COLOR_GOLD_HOVER,
            command=self._on_delay_change,
        )
        self.delay_slider.set(self.config_data.get("auto_accept_delay_seconds", 3))
        self.delay_slider.pack(fill="x", padx=14, pady=(0, 14))

        connect_row = ctk.CTkFrame(settings, fg_color="transparent")
        connect_row.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkLabel(
            connect_row,
            text="Код подключения (напиши /start боту @dota2_notify_bot):",
            text_color=COLOR_TEXT,
            wraplength=370,
            justify="left",
        ).pack(anchor="w")
        entry_row = ctk.CTkFrame(connect_row, fg_color="transparent")
        entry_row.pack(fill="x", pady=(6, 0))
        self.api_key_entry = ctk.CTkEntry(entry_row, placeholder_text="Вставь код сюда")
        self.api_key_entry.pack(side="left", fill="x", expand=True)
        if self.config_data.get("api_key"):
            self.api_key_entry.insert(0, self.config_data["api_key"])
        ctk.CTkButton(
            entry_row,
            text="OK",
            width=50,
            fg_color=COLOR_GOLD,
            text_color=COLOR_BG,
            hover_color=COLOR_GOLD_HOVER,
            command=self._on_save_api_key,
        ).pack(side="left", padx=(8, 0))

    def _apply_icon(self):
        # iconbitmap() принимает путь к файлу, а Tcl/Tk на Windows плохо
        # работает с кириллицей в пути (как и было с OpenCV) — поэтому
        # загружаем картинку через PIL и передаём готовый объект.
        png_path = os.path.join(BUNDLE_DIR, "tray_icon.png")
        if os.path.isfile(png_path):
            try:
                self._titlebar_icon = ImageTk.PhotoImage(Image.open(png_path))
                self.iconphoto(True, self._titlebar_icon)
            except Exception:
                pass

    # ---------- Config persistence ----------

    def _persist(self):
        save_config(self.config_data)

    def _on_toggle_auto_accept(self):
        self.config_data["auto_accept"] = bool(self.auto_accept_switch.get())
        self._persist()

    def _on_delay_change(self, value):
        seconds = int(round(value))
        self.config_data["auto_accept_delay_seconds"] = seconds
        self.delay_value_label.configure(text=f"Задержка перед автопринятием: {seconds} сек")
        self._persist()

    def _on_save_api_key(self):
        api_key = self.api_key_entry.get().strip().strip("﻿")
        self.config_data["api_key"] = api_key
        self._persist()
        self._log("Код подключения сохранён.")

    def _on_cancel_countdown(self):
        self.worker.cancel_pending_accept()

    # ---------- Log/status ----------

    def _log(self, text):
        line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {text}"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def _set_status(self, text):
        self.status_label.configure(text=text)

    # ---------- Queue polling (события и от worker'а, и от трея) ----------

    def _poll_queue(self):
        try:
            while True:
                event = self.event_queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.after(150, self._poll_queue)

    def _handle_event(self, event):
        etype = event["type"]
        if etype == "status":
            self._set_status(f"🟢 {event['text']}")
            self._log(event["text"])
        elif etype == "found":
            self._set_status("⚡ ИГРА НАЙДЕНА!")
            self._log("Найдена игра!")
        elif etype == "countdown_start":
            self.countdown_frame.pack(fill="x", padx=20, pady=(4, 0))
            self.countdown_label.configure(text=f"Автопринятие через {event['seconds']} сек")
        elif etype == "countdown_tick":
            self.countdown_label.configure(text=f"Автопринятие через {event['seconds_left']} сек")
        elif etype == "countdown_cancelled":
            self.countdown_frame.pack_forget()
            self._set_status("🟢 Слежу за экраном...")
            self._log("Автопринятие отменено.")
        elif etype == "accepted":
            self.countdown_frame.pack_forget()
            self._set_status("🟢 Слежу за экраном...")
            self._log("Принято автоматически.")
        elif etype == "error":
            self._log(f"Ошибка: {event['message']}")
        elif etype == "tray_show":
            self.deiconify()
            self.lift()
            self.focus_force()
        elif etype == "tray_quit":
            self._shutdown()

    # ---------- Tray ----------

    def _start_tray(self):
        image = Image.open(os.path.join(BUNDLE_DIR, "tray_icon.png"))
        menu = pystray.Menu(
            pystray.MenuItem("Показать окно", self._on_tray_show, default=True),
            pystray.MenuItem("Выход", self._on_tray_quit),
        )
        self.tray_icon = pystray.Icon("DotaNotifier", image, "Dota 2 Notifier", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _hide_to_tray(self):
        self.withdraw()

    def _on_tray_show(self, icon=None, item=None):
        self.event_queue.put({"type": "tray_show"})

    def _on_tray_quit(self, icon=None, item=None):
        self.event_queue.put({"type": "tray_quit"})

    def _shutdown(self):
        self.worker.stop()
        if self.tray_icon:
            self.tray_icon.stop()
        self.destroy()


def main():
    app = DotaNotifierApp()
    app.mainloop()


if __name__ == "__main__":
    main()
