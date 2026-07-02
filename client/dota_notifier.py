import ctypes
import datetime
import json
import os
import queue
import sys
import threading
from tkinter import colorchooser, filedialog

import customtkinter as ctk
import pystray
from PIL import Image, ImageDraw, ImageFont, ImageTk

import monitor

APP_VERSION = "1.4.0"

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

# Зелёный из иконки приложения — основной акцент бренда: шапка, переключатели,
# слайдер, активная вкладка, основные кнопки — всё в одном цвете, чтобы
# интерфейс читался как единая система, а не набор случайных виджетов.
ACCENT_COLOR = "#267340"

# Цвета статус-пилюли на главной вкладке, по состоянию мониторинга.
STATUS_COLORS = {
    "idle": ACCENT_COLOR,
    "working": "#d99a2b",
    "alert": "#d99a2b",
    "error": CANCEL_COLOR,
}

MAIN_TAB_BG_IMAGE_SIZE = (420, 540)

HERO_SIZE = (372, 92)
HERO_SUPERSAMPLE = 3

# Окно принятия игры в Dota 2 — 30 секунд. Опрос экрана раз в секунду
# добавляет до ~1 сек задержки на обнаружение, поэтому максимум выбора
# ограничен 25 секундами — оставляет запас, чтобы клик гарантированно
# успевал до закрытия окна, даже если система притормозит.
MAX_DELAY_SECONDS = 25

# Значок "?" у карточек — рисуется заранее как картинка с суперсэмплингом
# (как иконка приложения), а не живьём мелким шрифтом CTk: на таком крошечном
# размере (22px) сглаживание "на лету" читается как размытость.
HELP_BADGE_SIZE = 22
HELP_BADGE_SUPERSAMPLE = 8


def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def blend(hex_a, hex_b, t):
    """Смешивает два HEX-цвета: t=0 -> hex_a, t=1 -> hex_b."""
    a, b = hex_to_rgb(hex_a), hex_to_rgb(hex_b)
    r = round(a[0] + (b[0] - a[0]) * t)
    g = round(a[1] + (b[1] - a[1]) * t)
    bl = round(a[2] + (b[2] - a[2]) * t)
    return f"#{r:02x}{g:02x}{bl:02x}"


def panel_shade(hex_color, amount=0.12):
    """Возвращает оттенок панели, контрастный к фону: светлее на тёмном фоне,
    темнее на светлом — чтобы карточки визуально отделялись от фона."""
    r, g, b = hex_to_rgb(hex_color)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    if luminance < 0.5:
        r = min(255, int(r + (255 - r) * amount))
        g = min(255, int(g + (255 - g) * amount))
        b = min(255, int(b + (255 - b) * amount))
    else:
        r = int(r * (1 - amount))
        g = int(g * (1 - amount))
        b = int(b * (1 - amount))
    return f"#{r:02x}{g:02x}{b:02x}"


def muted_text(text_color, bg_color, amount=0.45):
    """Приглушённый вариант text_color для второстепенных подписей —
    смешивает его с фоном, а не просто снижает яркость, поэтому остаётся
    гармоничным с любой выбранной пользователем палитрой."""
    return blend(text_color, bg_color, amount)


def load_display_font(size, bold=True):
    """Шрифт для баннера-шапки — фиксированный, не зависит от выбора
    пользователя на вкладке "Оформление" (там настраивается шрифт обычных
    надписей, а не логотипа), как и сама иконка приложения."""
    names = ("segoeuib.ttf", "arialbd.ttf", "arial.ttf") if bold else ("segoeui.ttf", "arial.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


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
DEFAULT_FONT_FAMILY = "Arial Black"


def short_path(path):
    """Возвращает короткий путь (8.3) без кириллицы — Tcl/Tk на Windows
    плохо работает с кириллицей в пути для iconbitmap (как и было раньше
    с OpenCV)."""
    buf = ctypes.create_unicode_buffer(260)
    result = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 260)
    return buf.value if result else path


def default_config():
    return {
        "server_url": DEFAULT_SERVER_URL,
        "api_key": "",
        "auto_accept": True,
        "auto_accept_delay_seconds": 3,
        # Выключено по умолчанию: в отличие от принятия уже найденного матча,
        # это молча принимает ЛЮБОЕ приглашение в пати без разбора, кто зовёт —
        # осознанный выбор пользователя, не то, что должно включаться само.
        "auto_accept_party_invite": False,
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
        self._hero_image_ref = None
        self._help_popup = None
        self._help_popup_owner = None

        ctk.set_appearance_mode("dark")
        self.title(f"Dota 2 Notifier v{APP_VERSION}")
        self.geometry("440x730")
        self.resizable(False, False)

        self.tabview = ctk.CTkTabview(self, corner_radius=14)
        self.tabview.pack(fill="both", expand=True, padx=8, pady=8)
        self.main_tab = self.tabview.add("Главная")
        self.connect_tab = self.tabview.add("Подключение")
        self.appearance_tab = self.tabview.add("Оформление")
        self.tabview.set("Главная")

        self._help_badge_image = ctk.CTkImage(
            light_image=self._build_help_badge_image(), size=(HELP_BADGE_SIZE, HELP_BADGE_SIZE),
        )

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
        self.bind_all("<Button-1>", self._on_global_click, add="+")

    def _font(self, size, weight="bold"):
        family = self.config_data.get("font_family", DEFAULT_FONT_FAMILY)
        return ctk.CTkFont(family=family, size=size, weight=weight)

    @staticmethod
    def _lighten_accent():
        return panel_shade(ACCENT_COLOR, amount=0.25)

    # ---------- Кнопки: единый визуальный язык (primary/secondary/danger) ----------

    def _primary_button(self, parent, text, command, **kwargs):
        return ctk.CTkButton(
            parent, text=text, font=self._font(13), fg_color=ACCENT_COLOR,
            hover_color=self._lighten_accent(), text_color="#ffffff",
            corner_radius=8, command=command, **kwargs,
        )

    def _secondary_button(self, parent, text, command, **kwargs):
        bg_color = self.config_data["bg_color"]
        text_color = self.config_data["text_color"]
        return ctk.CTkButton(
            parent, text=text, font=self._font(13), fg_color=panel_shade(bg_color, amount=0.16),
            hover_color=panel_shade(bg_color, amount=0.26), text_color=text_color,
            border_width=1, border_color=panel_shade(bg_color, amount=0.32),
            corner_radius=8, command=command, **kwargs,
        )

    def _danger_button(self, parent, text, command, **kwargs):
        return ctk.CTkButton(
            parent, text=text, font=self._font(13), fg_color=CANCEL_COLOR,
            hover_color=CANCEL_COLOR_HOVER, text_color="#ffffff",
            corner_radius=8, command=command, **kwargs,
        )

    # ---------- Help-попапы ----------

    @staticmethod
    def _build_help_badge_image():
        size = HELP_BADGE_SIZE * HELP_BADGE_SUPERSAMPLE
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([0, 0, size - 1, size - 1], fill=hex_to_rgb(ACCENT_COLOR) + (255,))

        font = load_display_font(int(size * 0.6))
        text = "?"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), text,
            font=font, fill=(255, 255, 255, 255),
        )
        return img.resize((HELP_BADGE_SIZE, HELP_BADGE_SIZE), Image.LANCZOS)

    def _make_help_button(self, parent, help_text):
        button = ctk.CTkButton(
            parent, text="", image=self._help_badge_image, width=HELP_BADGE_SIZE, height=HELP_BADGE_SIZE,
            corner_radius=HELP_BADGE_SIZE // 2, fg_color="transparent",
            hover_color=panel_shade(self.config_data["bg_color"], amount=0.22),
        )
        button.configure(command=lambda: self._toggle_help_popup(button, help_text))
        return button

    def _toggle_help_popup(self, button, text):
        had_popup = self._help_popup is not None
        same_button = self._help_popup_owner is button
        self._close_help_popup()
        if had_popup and same_button:
            return
        self._open_help_popup(button, text)

    def _open_help_popup(self, button, text):
        bg_color = self.config_data["bg_color"]
        text_color = self.config_data["text_color"]
        # Toplevel сам по себе всегда прямоугольный — углы вокруг скруглённой
        # карточки иначе остаются чёрными. "-transparentcolor" (Windows-only
        # атрибут Tk) делает все пиксели этого цвета по-настоящему прозрачными,
        # так что видно содержимое за окном, а не чёрную подложку.
        sentinel_color = "#fe01fe"

        popup = ctk.CTkToplevel(self)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(fg_color=sentinel_color)
        try:
            popup.attributes("-transparentcolor", sentinel_color)
        except Exception:
            pass

        # corner_radius=0: со скруглением сглаживание на границе подмешивает
        # sentinel_color к цвету карточки, и эта смесь уже не совпадает с
        # ключом прозрачности точно — остаётся видимая цветная кайма.
        frame = ctk.CTkFrame(popup, fg_color=panel_shade(bg_color), corner_radius=0, border_width=1, border_color=ACCENT_COLOR)
        frame.pack()
        ctk.CTkLabel(
            frame, text=text, font=self._font(11, weight="normal"), text_color=text_color,
            wraplength=260, justify="left",
        ).pack(padx=12, pady=10)

        button.update_idletasks()
        x = button.winfo_rootx()
        y = button.winfo_rooty() + button.winfo_height() + 4
        popup.update_idletasks()
        popup.geometry(f"+{x}+{y}")

        self._help_popup = popup
        self._help_popup_owner = button

    def _close_help_popup(self):
        popup = self._help_popup
        self._help_popup = None
        self._help_popup_owner = None
        if popup is not None:
            try:
                popup.destroy()
            except Exception:
                pass

    def _on_global_click(self, event):
        if self._help_popup is None:
            return
        owner = self._help_popup_owner
        if owner is not None:
            try:
                ox, oy = owner.winfo_rootx(), owner.winfo_rooty()
                ow, oh = owner.winfo_width(), owner.winfo_height()
                if ox <= event.x_root <= ox + ow and oy <= event.y_root <= oy + oh:
                    return  # клик по самой кнопке — toggle обработает её command
            except Exception:
                pass
        self._close_help_popup()

    # ---------- Баннер-шапка ----------

    def _build_hero_image(self):
        text_color = self.config_data["text_color"]
        w, h = HERO_SIZE[0] * HERO_SUPERSAMPLE, HERO_SIZE[1] * HERO_SUPERSAMPLE
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Диагональный градиент "тёмный акцент -> яркий акцент" — фирменный
        # цвет приложения, а не выбранный пользователем bg_color, чтобы шапка
        # всегда узнаваемо зелёная, как иконка, независимо от темы.
        top_rgb = hex_to_rgb(blend(ACCENT_COLOR, "#000000", 0.45))
        bottom_rgb = hex_to_rgb(ACCENT_COLOR)
        for y in range(h):
            t = y / h
            r = round(top_rgb[0] + (bottom_rgb[0] - top_rgb[0]) * t)
            g = round(top_rgb[1] + (bottom_rgb[1] - top_rgb[1]) * t)
            b = round(top_rgb[2] + (bottom_rgb[2] - top_rgb[2]) * t)
            draw.line([(0, y), (w, y)], fill=(r, g, b, 255))

        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=16 * HERO_SUPERSAMPLE, fill=255)
        img.putalpha(mask)

        icon_path = os.path.join(BUNDLE_DIR, "tray_icon.png")
        text_x = 24 * HERO_SUPERSAMPLE
        if os.path.isfile(icon_path):
            try:
                icon_size = 46 * HERO_SUPERSAMPLE
                icon = Image.open(icon_path).convert("RGBA").resize((icon_size, icon_size), Image.LANCZOS)
                icon_x = 22 * HERO_SUPERSAMPLE
                icon_y = (h - icon_size) // 2
                img.paste(icon, (icon_x, icon_y), icon)
                text_x = icon_x + icon_size + 16 * HERO_SUPERSAMPLE
            except Exception:
                pass

        title_font = load_display_font(30 * HERO_SUPERSAMPLE)
        subtitle_font = load_display_font(14 * HERO_SUPERSAMPLE, bold=False)
        subtitle_rgb = hex_to_rgb(blend(text_color, ACCENT_COLOR, 0.2))

        title_bbox = draw.textbbox((0, 0), "DOTA 2", font=title_font)
        subtitle_text = "N O T I F I E R"
        subtitle_bbox = draw.textbbox((0, 0), subtitle_text, font=subtitle_font)
        gap = 6 * HERO_SUPERSAMPLE
        title_h = title_bbox[3] - title_bbox[1]
        subtitle_h = subtitle_bbox[3] - subtitle_bbox[1]
        start_y = (h - (title_h + gap + subtitle_h)) // 2

        draw.text((text_x, start_y - title_bbox[1]), "DOTA 2", font=title_font, fill=hex_to_rgb(text_color) + (255,))
        draw.text(
            (text_x, start_y + title_h + gap - subtitle_bbox[1]), subtitle_text,
            font=subtitle_font, fill=subtitle_rgb + (255,),
        )

        return img.resize(HERO_SIZE, Image.LANCZOS)

    # ---------- Главная вкладка ----------

    def _build_main_tab(self):
        for child in self.main_tab.winfo_children():
            child.destroy()

        bg_color = self.config_data["bg_color"]
        text_color = self.config_data["text_color"]
        panel_color = panel_shade(bg_color)
        bg_image_path = self.config_data.get("background_image_path", "")
        has_bg_image = bool(bg_image_path) and os.path.isfile(bg_image_path)

        self.main_tab.configure(fg_color=bg_color)
        content_master = self.main_tab

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

        self._hero_image_ref = ctk.CTkImage(light_image=self._build_hero_image(), size=HERO_SIZE)
        ctk.CTkLabel(content, image=self._hero_image_ref, text="").pack(padx=20, pady=(16, 0))

        status_pill = ctk.CTkFrame(content, fg_color=panel_color, corner_radius=18, height=40)
        status_pill.pack(pady=(16, 4), padx=20, fill="x")
        status_pill.pack_propagate(False)
        self.status_dot = ctk.CTkFrame(status_pill, width=10, height=10, corner_radius=5, fg_color=ACCENT_COLOR)
        self.status_dot.pack(side="left", padx=(16, 10))
        self.status_text_label = ctk.CTkLabel(
            status_pill, text="Запуск...", font=self._font(14, weight="normal"), text_color=text_color,
        )
        self.status_text_label.pack(side="left")

        ctk.CTkLabel(
            content, text="ЖУРНАЛ СОБЫТИЙ", font=self._font(11),
            text_color=muted_text(text_color, bg_color),
        ).pack(anchor="w", padx=22, pady=(14, 4))
        self.log_box = ctk.CTkTextbox(
            content, height=142, corner_radius=10,
            fg_color=panel_color, text_color=text_color, font=self._font(13, weight="normal"),
        )
        self.log_box.pack(fill="x", padx=20)
        self.log_box.configure(state="disabled")

        self.countdown_frame = ctk.CTkFrame(content, fg_color=panel_color, corner_radius=10)
        self.countdown_label = ctk.CTkLabel(
            self.countdown_frame, text="", font=self._font(16), text_color=text_color,
        )
        self.countdown_label.pack(pady=(10, 4))
        self.cancel_button = self._danger_button(self.countdown_frame, "Отмена", self._on_cancel_countdown)
        self.cancel_button.pack(pady=(0, 10))

        auto_accept_card = ctk.CTkFrame(content, fg_color=panel_color, corner_radius=10)
        auto_accept_card.pack(fill="x", padx=20, pady=(16, 8))

        switch_row = ctk.CTkFrame(auto_accept_card, fg_color="transparent")
        switch_row.pack(fill="x", padx=14, pady=14)
        ctk.CTkLabel(switch_row, text="🔁 Автопринятие игры", font=self._font(14), text_color=text_color).pack(side="left")
        self._make_help_button(
            switch_row,
            "Может работать нестабильно в полноэкранном режиме (Fullscreen) — "
            "в настройках видео Dota 2 выбери \"Оконный безрамочный\" режим.",
        ).pack(side="left", padx=(6, 0))
        self.auto_accept_switch = ctk.CTkSwitch(
            switch_row, text="", progress_color=ACCENT_COLOR, button_color=text_color,
            button_hover_color=text_color, command=self._on_toggle_auto_accept,
            # bg_color задан явно: сквозь несколько вложенных "transparent"
            # фреймов CTk иногда не может правильно определить, на каком
            # фоне сглаживать скруглённые края — получаются чёрные пиксели
            # по краям (тот же класс бага, что был у попапа справки).
            bg_color=panel_color, width=46, height=24, switch_width=46, switch_height=24,
        )
        self.auto_accept_switch.pack(side="right")
        if self.config_data.get("auto_accept", True):
            self.auto_accept_switch.select()
        else:
            self.auto_accept_switch.deselect()

        delay_card = ctk.CTkFrame(content, fg_color=panel_color, corner_radius=10)
        delay_card.pack(fill="x", padx=20, pady=(8, 16))

        delay_header_row = ctk.CTkFrame(delay_card, fg_color="transparent")
        delay_header_row.pack(fill="x", padx=14, pady=(14, 6))
        ctk.CTkLabel(delay_header_row, text="⏱ Задержка автопринятия", font=self._font(14), text_color=text_color).pack(side="left")
        self._make_help_button(
            delay_header_row,
            f"Максимум {MAX_DELAY_SECONDS} сек — в Dota 2 всего 30 сек на принятие игры, остальное запас на надёжность.",
        ).pack(side="left", padx=(6, 0))
        self.delay_value_label = ctk.CTkLabel(
            delay_header_row,
            text=f"{self.config_data.get('auto_accept_delay_seconds', 3)} сек",
            font=self._font(14), text_color=ACCENT_COLOR,
        )
        self.delay_value_label.pack(side="right")
        self.delay_slider = ctk.CTkSlider(
            delay_card, from_=0, to=MAX_DELAY_SECONDS, number_of_steps=MAX_DELAY_SECONDS,
            progress_color=ACCENT_COLOR, button_color=text_color, button_hover_color=text_color,
            command=self._on_delay_change,
        )
        self.delay_slider.set(self.config_data.get("auto_accept_delay_seconds", 3))
        self.delay_slider.pack(fill="x", padx=14, pady=(0, 14))

        party_card = ctk.CTkFrame(content, fg_color=panel_color, corner_radius=10)
        party_card.pack(fill="x", padx=20, pady=(0, 16))

        party_row = ctk.CTkFrame(party_card, fg_color="transparent")
        party_row.pack(fill="x", padx=14, pady=14)
        ctk.CTkLabel(party_row, text="🎉 Автопринятие в пати", font=self._font(14), text_color=text_color).pack(side="left")
        self._make_help_button(
            party_row,
            "Автоматически принимает ЛЮБОЕ приглашение в пати от друзей в Dota 2, "
            "не дожидаясь тебя за компьютером. Не различает, кто именно зовёт — "
            "включай, только если это не проблема.",
        ).pack(side="left", padx=(6, 0))
        self.party_invite_switch = ctk.CTkSwitch(
            party_row, text="", progress_color=ACCENT_COLOR, button_color=text_color,
            button_hover_color=text_color, command=self._on_toggle_party_invite,
            bg_color=panel_color, width=46, height=24, switch_width=46, switch_height=24,
        )
        self.party_invite_switch.pack(side="right")
        if self.config_data.get("auto_accept_party_invite", False):
            self.party_invite_switch.select()
        else:
            self.party_invite_switch.deselect()

    # ---------- Вкладка "Подключение" ----------

    def _build_connect_tab(self):
        for child in self.connect_tab.winfo_children():
            child.destroy()

        bg_color = self.config_data["bg_color"]
        text_color = self.config_data["text_color"]
        panel_color = panel_shade(bg_color)

        self.connect_tab.configure(fg_color=bg_color)
        container = ctk.CTkFrame(self.connect_tab, fg_color=panel_color, corner_radius=14)
        container.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            container, text="🔑 Код подключения", font=self._font(18), text_color=text_color,
        ).pack(anchor="w", padx=20, pady=(24, 4))
        ctk.CTkLabel(
            container,
            text="Напиши /start боту @dota2_notify_bot в Telegram, он пришлёт код — вставь его сюда.",
            font=self._font(13, weight="normal"), text_color=muted_text(text_color, bg_color),
            wraplength=380, justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 14))

        entry_row = ctk.CTkFrame(container, fg_color="transparent")
        entry_row.pack(fill="x", padx=20)
        self.api_key_entry = ctk.CTkEntry(
            entry_row, placeholder_text="Вставь код сюда", font=self._font(13, weight="normal"),
            corner_radius=8, border_color=panel_shade(bg_color, amount=0.32),
        )
        self.api_key_entry.pack(side="left", fill="x", expand=True)
        if self.config_data.get("api_key"):
            self.api_key_entry.insert(0, self.config_data["api_key"])
        self._primary_button(entry_row, "OK", self._on_save_api_key, width=50).pack(side="left", padx=(8, 0))

    # ---------- Вкладка "Оформление" ----------

    def _build_appearance_tab(self):
        for child in self.appearance_tab.winfo_children():
            child.destroy()

        bg_color = self.config_data["bg_color"]
        text_color = self.config_data["text_color"]
        panel_color = panel_shade(bg_color)

        self.appearance_tab.configure(fg_color=bg_color)
        container = ctk.CTkFrame(self.appearance_tab, fg_color=panel_color, corner_radius=14)
        container.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(container, text="🎨 Цвет фона", font=self._font(14), text_color=text_color).pack(anchor="w", padx=20, pady=(20, 6))
        row1 = ctk.CTkFrame(container, fg_color="transparent")
        row1.pack(fill="x", padx=20)
        ctk.CTkFrame(row1, fg_color=bg_color, width=44, height=32, corner_radius=8, border_width=2, border_color=ACCENT_COLOR).pack(side="left")
        self._secondary_button(row1, "Выбрать цвет фона", self._on_pick_bg_color).pack(side="left", padx=(10, 0))

        ctk.CTkLabel(container, text="🖋 Цвет текста", font=self._font(14), text_color=text_color).pack(anchor="w", padx=20, pady=(20, 6))
        row2 = ctk.CTkFrame(container, fg_color="transparent")
        row2.pack(fill="x", padx=20)
        ctk.CTkFrame(row2, fg_color=text_color, width=44, height=32, corner_radius=8, border_width=2, border_color=ACCENT_COLOR).pack(side="left")
        self._secondary_button(row2, "Выбрать цвет текста", self._on_pick_text_color).pack(side="left", padx=(10, 0))

        ctk.CTkLabel(container, text="🔤 Шрифт", font=self._font(14), text_color=text_color).pack(anchor="w", padx=20, pady=(20, 6))
        self.font_menu = ctk.CTkOptionMenu(
            container, values=FONT_CHOICES, font=self._font(13), corner_radius=8,
            fg_color=panel_shade(bg_color, amount=0.16), button_color=panel_shade(bg_color, amount=0.26),
            button_hover_color=panel_shade(bg_color, amount=0.36), text_color=text_color,
            dropdown_fg_color=panel_shade(bg_color, amount=0.1), dropdown_text_color=text_color,
            dropdown_font=self._font(13), command=self._on_pick_font,
        )
        self.font_menu.set(self.config_data.get("font_family", DEFAULT_FONT_FAMILY))
        self.font_menu.pack(anchor="w", padx=20)

        ctk.CTkLabel(
            container, text="🖼 Фоновая картинка", font=self._font(14), text_color=text_color,
        ).pack(anchor="w", padx=20, pady=(28, 2))
        ctk.CTkLabel(
            container, text="показывается на вкладке «Главная»", font=self._font(12, weight="normal"),
            text_color=muted_text(text_color, bg_color),
        ).pack(anchor="w", padx=20, pady=(0, 6))
        row3 = ctk.CTkFrame(container, fg_color="transparent")
        row3.pack(fill="x", padx=20)
        self._secondary_button(row3, "Выбрать фото...", self._on_pick_background_image).pack(side="left")
        self._danger_button(row3, "Убрать фото", self._on_clear_background_image).pack(side="left", padx=(10, 0))

        current_image = self.config_data.get("background_image_path") or "не выбрана"
        ctk.CTkLabel(
            container, text=f"Текущая: {current_image}", font=self._font(12, weight="normal"),
            text_color=muted_text(text_color, bg_color), wraplength=380, justify="left",
        ).pack(anchor="w", padx=20, pady=(8, 4))

        self._danger_button(
            container, "↺ Сбросить оформление по умолчанию", self._on_reset_appearance,
        ).pack(anchor="w", padx=20, pady=(28, 10))

    def _apply_icon(self):
        # iconphoto() с PNG задаёт только "маленькую" иконку (заголовок
        # окна) — Windows для панели задач берёт "большую" иконку именно
        # из .ico. iconbitmap() с кириллицей в пути не работает (как и
        # было с OpenCV), поэтому передаём короткий путь без кириллицы.
        ico_path = os.path.join(BUNDLE_DIR, "app_icon.ico")
        if os.path.isfile(ico_path):
            try:
                self.iconbitmap(short_path(ico_path))
                return
            except Exception:
                pass

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
        self.tabview.configure(
            fg_color=bg_color, text_color=text_color,
            segmented_button_fg_color=panel_shade(bg_color, amount=0.08),
            segmented_button_selected_color=ACCENT_COLOR,
            segmented_button_selected_hover_color=self._lighten_accent(),
            segmented_button_unselected_color=panel_shade(bg_color, amount=0.08),
            segmented_button_unselected_hover_color=panel_shade(bg_color, amount=0.16),
        )

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

    def _on_toggle_party_invite(self):
        self.config_data["auto_accept_party_invite"] = bool(self.party_invite_switch.get())
        self._persist()

    def _on_delay_change(self, value):
        seconds = int(round(value))
        self.config_data["auto_accept_delay_seconds"] = seconds
        self.delay_value_label.configure(text=f"{seconds} сек")
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

    def _set_status(self, text, state="idle"):
        self.status_text_label.configure(text=text)
        self.status_dot.configure(fg_color=STATUS_COLORS.get(state, ACCENT_COLOR))

    @staticmethod
    def _status_state_for_text(text):
        return "idle" if "Слежу" in text else "working"

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
            self._set_status(event["text"], state=self._status_state_for_text(event["text"]))
            self._log(event["text"])
        elif etype == "found":
            self._set_status("ИГРА НАЙДЕНА!", state="alert")
            self._log("Найдена игра!")
        elif etype == "countdown_start":
            self.countdown_frame.pack(fill="x", padx=20, pady=(4, 0))
            self.countdown_label.configure(text=f"Автопринятие через {event['seconds']} сек")
        elif etype == "countdown_tick":
            self.countdown_label.configure(text=f"Автопринятие через {event['seconds_left']} сек")
        elif etype == "countdown_cancelled":
            self.countdown_frame.pack_forget()
            self._set_status("Слежу за экраном...", state="idle")
            self._log("Автопринятие отменено.")
        elif etype == "accepted":
            self.countdown_frame.pack_forget()
            self._set_status("Слежу за экраном...", state="idle")
            self._log("Принято автоматически.")
        elif etype == "error":
            self._log(f"Ошибка: {event['message']}")
            self._set_status(event["message"], state="error")
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
        self.tray_icon = pystray.Icon("DotaNotifier", image, f"Dota 2 Notifier v{APP_VERSION}", menu)
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
