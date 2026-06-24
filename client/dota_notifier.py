import datetime
import json
import os
import queue
import sys
import threading
from tkinter import colorchooser, filedialog

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

# Фиксированный (не настраиваемый пользователем) цвет кнопки отмены —
# красный нужен только как семантическая подсказка "опасное/стоп действие".
CANCEL_COLOR = "#a13d3d"
CANCEL_COLOR_HOVER = "#bf4d4d"

MAIN_TAB_BG_IMAGE_SIZE = (420, 540)

# Шрифты ограничены тем, что обычно уже есть в Windows — без бандла своих
# .ttf и регистрации их через WinAPI, что для этой задачи избыточно.
FONT_CHOICES = [
    "Segoe UI Semibold",
    "Segoe UI Black",
    "Segoe UI",
    "Arial Black",
    "Bahnschrift",
    "Trebuchet MS",
    "Verdana",
    "Calibri",
]
DEFAULT_FONT_FAMILY = "Segoe UI Semibold"


def default_config():
    return {
        "server_url": DEFAULT_SERVER_URL,
        "api_key": "",
        "auto_accept": True,
        "auto_accept_delay_seconds": 3,
        "bg_color": "#1e1e1e",
        "text_color": "#ffffff",
        "background_image_path": "",
        "font_family": DEFAULT_FONT_FAMILY,
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
        self._bg_image_ref = None

        ctk.set_appearance_mode("dark")
        self.title("Dota 2 Notifier")
        self.geometry("440x640")
        self.resizable(False, False)

        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=8, pady=8)
        self.main_tab = self.tabview.add("Главная")
        self.connect_tab = self.tabview.add("Подключение")
        self.appearance_tab = self.tabview.add("Оформление")
        self.tabview.set("Главная")

        self._build_main_tab()
        self._build_connect_tab()
        self._build_appearance_tab()
        self._apply_window_colors()
        self._apply_icon()

        self.worker = monitor.MonitorWorker(lambda: self.config_data, self.event_queue)
        self.worker.start()
        self._start_tray()

        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self.after(150, self._poll_queue)

    def _font(self, size, weight="bold"):
        family = self.config_data.get("font_family", DEFAULT_FONT_FAMILY)
        return ctk.CTkFont(family=family, size=size, weight=weight)

    # ---------- Главная вкладка ----------

    def _build_main_tab(self):
        for child in self.main_tab.winfo_children():
            child.destroy()

        bg_color = self.config_data["bg_color"]
        text_color = self.config_data["text_color"]
        bg_image_path = self.config_data.get("background_image_path", "")
        has_bg_image = bool(bg_image_path) and os.path.isfile(bg_image_path)

        content_master = self.main_tab
        header_color = bg_color

        if has_bg_image:
            try:
                pil_image = Image.open(bg_image_path).convert("RGB").resize(MAIN_TAB_BG_IMAGE_SIZE)
                self._bg_image_ref = ImageTk.PhotoImage(pil_image)
                # Виджеты CTk не умеют по-настоящему "просвечивать" сквозь
                # друг друга — fg_color="transparent" просто подделывает цвет
                # родителя. Чтобы реально показать картинку под виджетами,
                # используем стандартный приём Tkinter: рисуем её на Canvas,
                # а содержимое вкладки встраиваем поверх через create_window.
                bg_canvas = ctk.CTkCanvas(self.main_tab, highlightthickness=0)
                bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
                bg_canvas.create_image(0, 0, anchor="nw", image=self._bg_image_ref)
                content_master = bg_canvas
                header_color = "transparent"
            except Exception:
                has_bg_image = False

        content = ctk.CTkFrame(content_master, fg_color="transparent")
        if has_bg_image:
            content_master.create_window(
                (0, 0), window=content, anchor="nw",
                width=MAIN_TAB_BG_IMAGE_SIZE[0], height=MAIN_TAB_BG_IMAGE_SIZE[1],
            )
        else:
            content.place(relx=0, rely=0, relwidth=1, relheight=1)

        header = ctk.CTkFrame(content, fg_color=header_color, corner_radius=0)
        header.pack(fill="x")
        ctk.CTkLabel(
            header, text="DOTA 2", font=self._font(28), text_color=text_color,
        ).pack(pady=(16, 0))
        ctk.CTkLabel(
            header, text="NOTIFIER", font=self._font(16), text_color=text_color,
        ).pack(pady=(0, 16))

        self.status_label = ctk.CTkLabel(
            content, text="Запуск...", font=self._font(15),
            text_color=text_color, fg_color=bg_color,
        )
        self.status_label.pack(pady=(16, 4), fill="x")

        self.log_box = ctk.CTkTextbox(
            content, height=150, fg_color=bg_color, text_color=text_color, font=self._font(13, weight="normal"),
        )
        self.log_box.pack(fill="x", padx=20, pady=4)
        self.log_box.configure(state="disabled")

        self.countdown_frame = ctk.CTkFrame(content, fg_color=bg_color)
        self.countdown_label = ctk.CTkLabel(
            self.countdown_frame, text="", font=self._font(16), text_color=text_color,
        )
        self.countdown_label.pack(pady=(10, 4))
        self.cancel_button = ctk.CTkButton(
            self.countdown_frame, text="Отмена", font=self._font(13), fg_color=CANCEL_COLOR,
            hover_color=CANCEL_COLOR_HOVER, text_color="#ffffff", command=self._on_cancel_countdown,
        )
        self.cancel_button.pack(pady=(0, 10))

        settings = ctk.CTkFrame(content, fg_color=bg_color)
        settings.pack(fill="x", padx=20, pady=16)

        switch_row = ctk.CTkFrame(settings, fg_color="transparent")
        switch_row.pack(fill="x", padx=14, pady=(14, 6))
        ctk.CTkLabel(switch_row, text="Автопринятие", font=self._font(14), text_color=text_color).pack(side="left")
        self.auto_accept_switch = ctk.CTkSwitch(
            switch_row, text="", progress_color=text_color, command=self._on_toggle_auto_accept,
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
            font=self._font(14), text_color=text_color,
        )
        self.delay_value_label.pack(anchor="w")
        self.delay_slider = ctk.CTkSlider(
            settings, from_=0, to=20, number_of_steps=20,
            progress_color=text_color, button_color=text_color, button_hover_color=text_color,
            command=self._on_delay_change,
        )
        self.delay_slider.set(self.config_data.get("auto_accept_delay_seconds", 3))
        self.delay_slider.pack(fill="x", padx=14, pady=(0, 14))

    # ---------- Вкладка "Подключение" ----------

    def _build_connect_tab(self):
        for child in self.connect_tab.winfo_children():
            child.destroy()

        bg_color = self.config_data["bg_color"]
        text_color = self.config_data["text_color"]

        container = ctk.CTkFrame(self.connect_tab, fg_color=bg_color)
        container.pack(fill="both", expand=True)

        ctk.CTkLabel(
            container, text="Код подключения", font=self._font(18), text_color=text_color,
        ).pack(anchor="w", padx=20, pady=(24, 4))
        ctk.CTkLabel(
            container,
            text="Напиши /start боту @dota2_notify_bot в Telegram, он пришлёт код — вставь его сюда.",
            font=self._font(13, weight="normal"), text_color=text_color, wraplength=380, justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 14))

        entry_row = ctk.CTkFrame(container, fg_color="transparent")
        entry_row.pack(fill="x", padx=20)
        self.api_key_entry = ctk.CTkEntry(entry_row, placeholder_text="Вставь код сюда", font=self._font(13, weight="normal"))
        self.api_key_entry.pack(side="left", fill="x", expand=True)
        if self.config_data.get("api_key"):
            self.api_key_entry.insert(0, self.config_data["api_key"])
        ctk.CTkButton(
            entry_row, text="OK", width=50, font=self._font(13), fg_color=text_color, text_color=bg_color,
            command=self._on_save_api_key,
        ).pack(side="left", padx=(8, 0))

    # ---------- Вкладка "Оформление" ----------

    def _build_appearance_tab(self):
        for child in self.appearance_tab.winfo_children():
            child.destroy()

        bg_color = self.config_data["bg_color"]
        text_color = self.config_data["text_color"]

        container = ctk.CTkFrame(self.appearance_tab, fg_color=bg_color)
        container.pack(fill="both", expand=True)

        ctk.CTkLabel(container, text="Цвет фона", font=self._font(14), text_color=text_color).pack(anchor="w", padx=20, pady=(20, 4))
        row1 = ctk.CTkFrame(container, fg_color="transparent")
        row1.pack(fill="x", padx=20)
        ctk.CTkFrame(row1, fg_color=bg_color, width=36, height=28, border_width=1, border_color=text_color).pack(side="left")
        ctk.CTkButton(row1, text="Выбрать цвет фона", font=self._font(13), fg_color=text_color, text_color=bg_color, command=self._on_pick_bg_color).pack(side="left", padx=(10, 0))

        ctk.CTkLabel(container, text="Цвет текста", font=self._font(14), text_color=text_color).pack(anchor="w", padx=20, pady=(20, 4))
        row2 = ctk.CTkFrame(container, fg_color="transparent")
        row2.pack(fill="x", padx=20)
        ctk.CTkFrame(row2, fg_color=text_color, width=36, height=28, border_width=1, border_color=text_color).pack(side="left")
        ctk.CTkButton(row2, text="Выбрать цвет текста", font=self._font(13), fg_color=text_color, text_color=bg_color, command=self._on_pick_text_color).pack(side="left", padx=(10, 0))

        ctk.CTkLabel(container, text="Шрифт", font=self._font(14), text_color=text_color).pack(anchor="w", padx=20, pady=(20, 4))
        self.font_menu = ctk.CTkOptionMenu(
            container, values=FONT_CHOICES, font=self._font(13),
            fg_color=text_color, text_color=bg_color, button_color=text_color, button_hover_color=text_color,
            dropdown_font=self._font(13), command=self._on_pick_font,
        )
        self.font_menu.set(self.config_data.get("font_family", DEFAULT_FONT_FAMILY))
        self.font_menu.pack(anchor="w", padx=20)

        ctk.CTkLabel(
            container, text="Фоновая картинка (на вкладке «Главная»)", font=self._font(14), text_color=text_color,
        ).pack(anchor="w", padx=20, pady=(28, 4))
        row3 = ctk.CTkFrame(container, fg_color="transparent")
        row3.pack(fill="x", padx=20)
        ctk.CTkButton(row3, text="Выбрать фото...", font=self._font(13), fg_color=text_color, text_color=bg_color, command=self._on_pick_background_image).pack(side="left")
        ctk.CTkButton(row3, text="Убрать фото", font=self._font(13), fg_color=CANCEL_COLOR, hover_color=CANCEL_COLOR_HOVER, text_color="#ffffff", command=self._on_clear_background_image).pack(side="left", padx=(10, 0))

        current_image = self.config_data.get("background_image_path") or "не выбрана"
        ctk.CTkLabel(
            container, text=f"Текущая: {current_image}", font=self._font(12, weight="normal"),
            text_color=text_color, wraplength=380, justify="left",
        ).pack(anchor="w", padx=20, pady=(8, 4))

        ctk.CTkButton(
            container, text="Сбросить оформление по умолчанию", font=self._font(13), fg_color=CANCEL_COLOR,
            hover_color=CANCEL_COLOR_HOVER, text_color="#ffffff", command=self._on_reset_appearance,
        ).pack(anchor="w", padx=20, pady=(28, 10))

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

    def _apply_window_colors(self):
        bg_color = self.config_data["bg_color"]
        text_color = self.config_data["text_color"]
        self.configure(fg_color=bg_color)
        self.tabview.configure(fg_color=bg_color, text_color=text_color)

    def _refresh_theme(self):
        self._build_main_tab()
        self._build_connect_tab()
        self._build_appearance_tab()
        self._apply_window_colors()

    # ---------- Оформление: обработчики ----------

    def _on_pick_bg_color(self):
        color = colorchooser.askcolor(color=self.config_data["bg_color"], title="Цвет фона")[1]
        if color:
            self.config_data["bg_color"] = color
            self._persist()
            self._refresh_theme()

    def _on_pick_text_color(self):
        color = colorchooser.askcolor(color=self.config_data["text_color"], title="Цвет текста")[1]
        if color:
            self.config_data["text_color"] = color
            self._persist()
            self._refresh_theme()

    def _on_pick_font(self, value):
        self.config_data["font_family"] = value
        self._persist()
        self._refresh_theme()

    def _on_pick_background_image(self):
        path = filedialog.askopenfilename(
            title="Выбери картинку фона",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.bmp *.gif"), ("Все файлы", "*.*")],
        )
        if path:
            self.config_data["background_image_path"] = path
            self._persist()
            self._refresh_theme()

    def _on_clear_background_image(self):
        self.config_data["background_image_path"] = ""
        self._persist()
        self._refresh_theme()

    def _on_reset_appearance(self):
        defaults = default_config()
        self.config_data["bg_color"] = defaults["bg_color"]
        self.config_data["text_color"] = defaults["text_color"]
        self.config_data["background_image_path"] = ""
        self.config_data["font_family"] = defaults["font_family"]
        self._persist()
        self._refresh_theme()

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
