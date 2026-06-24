import os

from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Зелёный в тон фону кнопки "ПРИНЯТЬ" (accept_button_ru.png), но чуть более
# насыщенный, чтобы иконка хорошо читалась в маленьком размере.
GREEN = (38, 115, 64, 255)
WHITE = (255, 255, 255, 255)


def load_font(size):
    for name in ("arialbd.ttf", "segoeuib.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_icon(size):
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        [0, 0, size - 1, size - 1],
        radius=size * 0.22,
        fill=GREEN,
    )

    font = load_font(int(size * 0.62))
    text = "N"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((size - text_w) / 2 - bbox[0], (size - text_h) / 2 - bbox[1]),
        text,
        font=font,
        fill=WHITE,
    )
    return image


def main():
    sizes = [16, 32, 48, 64, 128, 256]
    images = [draw_icon(s) for s in sizes]

    ico_path = os.path.join(SCRIPT_DIR, "app_icon.ico")
    images[-1].save(ico_path, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"Сохранено: {ico_path}")

    tray_path = os.path.join(SCRIPT_DIR, "tray_icon.png")
    draw_icon(64).save(tray_path, format="PNG")
    print(f"Сохранено: {tray_path}")


if __name__ == "__main__":
    main()
