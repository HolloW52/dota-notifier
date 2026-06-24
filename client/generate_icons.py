import os

from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Зелёный в тон фону кнопки "ПРИНЯТЬ" (accept_button_ru.png), но чуть более
# насыщенный, чтобы иконка хорошо читалась в маленьком размере.
GREEN = (38, 115, 64, 255)
WHITE = (255, 255, 255, 255)

# Рисуем в N раз крупнее целевого размера и уменьшаем с качественным
# фильтром — обычное рисование PIL без суперсэмплинга даёт рваные/мутные
# края у скруглений и текста на маленьких размерах.
SUPERSAMPLE = 8


def load_font(size):
    for name in ("arialbd.ttf", "segoeuib.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_icon(size):
    big = size * SUPERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        [0, 0, big - 1, big - 1],
        radius=big * 0.22,
        fill=GREEN,
    )

    font = load_font(int(big * 0.62))
    text = "N"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((big - text_w) / 2 - bbox[0], (big - text_h) / 2 - bbox[1]),
        text,
        font=font,
        fill=WHITE,
    )
    return image.resize((size, size), Image.LANCZOS)


def main():
    sizes = [16, 32, 48, 64, 128, 256]
    images = [draw_icon(s) for s in sizes]

    ico_path = os.path.join(SCRIPT_DIR, "app_icon.ico")
    # append_images — иначе Pillow растягивает только САМУЮ БОЛЬШУЮ картинку
    # под остальные размеры, игнорируя уже отдельно отрисованные версии.
    images[-1].save(
        ico_path, format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[:-1],
    )
    print(f"Сохранено: {ico_path}")

    tray_path = os.path.join(SCRIPT_DIR, "tray_icon.png")
    draw_icon(64).save(tray_path, format="PNG")
    print(f"Сохранено: {tray_path}")


if __name__ == "__main__":
    main()
