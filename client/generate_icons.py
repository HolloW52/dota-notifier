import os

from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Зелёный в тон фону кнопки "ПРИНЯТЬ" (accept_button_ru.png), но чуть более
# насыщенный, чтобы иконка хорошо читалась в маленьком размере.
GREEN = (38, 115, 64, 255)
WHITE = (255, 255, 255, 255)

# Рисуем в N раз крупнее целевого размера и уменьшаем с качественным
# фильтром. Важно делать это для КАЖДОГО размера отдельно с одним и тем же
# (умеренным) коэффициентом — если вместо этого уменьшать один большой
# источник (например, 256 -> 16, в 16 раз за один шаг), результат выходит
# мыльным даже с хорошим фильтром.
SUPERSAMPLE = 8


def load_font(size):
    for name in ("arialbd.ttf", "segoeuib.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_icon(size):
    # Полное отсутствие сглаживания (было раньше для мелких размеров) даёт
    # грубые "ступеньки" по краям на панели задач — тоже плохо, просто в
    # другую сторону, чем размытие. Компромисс: лёгкое сглаживание на малых
    # размерах (сглаживает зубцы, но не успевает "смылить" саму букву) и
    # полноценное на крупных, где лишний запас пикселей не мешает.
    if size <= 24:
        supersample = 3
    elif size <= 48:
        supersample = 5
    else:
        supersample = SUPERSAMPLE
    big = size * supersample
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        [0, 0, big - 1, big - 1],
        radius=big * 0.22,
        fill=GREEN,
    )

    # Чуть крупнее буква — на маленьких размерах разборчивость важнее точных
    # пропорций.
    font = load_font(int(big * 0.7))
    text = "N"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((big - text_w) / 2 - bbox[0], (big - text_h) / 2 - bbox[1]),
        text,
        font=font,
        fill=WHITE,
    )
    if supersample == 1:
        return image
    return image.resize((size, size), Image.LANCZOS)


def main():
    # 256 не включаем: ICO-формат хранит этот размер как встроенный PNG
    # по особым правилам, и именно с ним предыдущая сборка через
    # append_images выходила структурно нестандартной (PIL читал файл
    # нормально, но строгий .NET-парсер — нет). Панели задач/заголовку
    # окна 128 более чем достаточно.
    #
    # Размеров много специально: Windows берёт логический размер иконки
    # (обычно 16 или 24 для панели задач) и умножает на масштаб экрана
    # (100/125/150/175/200%). Если в файле нет точного совпадения, Windows
    # сама растягивает соседний размер — а это и есть "мыло". Покрываем
    # самые частые комбинации, чтобы почти всегда было точное совпадение.
    sizes = [16, 20, 24, 28, 30, 32, 36, 40, 42, 48, 60, 64, 96, 128]
    images = [draw_icon(s) for s in sizes]

    ico_path = os.path.join(SCRIPT_DIR, "app_icon.ico")
    images[-1].save(
        ico_path, format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[:-1],
    )
    print(f"Сохранено: {ico_path}")

    # Проверка валидности на месте — чтобы не словить сюрприз только после
    # пересборки .exe.
    check = Image.open(ico_path)
    found_sizes = check.info.get("sizes", set())
    missing = set((s, s) for s in sizes) - found_sizes
    if missing:
        raise RuntimeError(f"В .ico не хватает размеров: {missing}")
    for s in found_sizes:
        check.size = s
        check.load()
    print(f"Проверено, размеры читаются: {sorted(found_sizes)}")

    tray_path = os.path.join(SCRIPT_DIR, "tray_icon.png")
    draw_icon(64).save(tray_path, format="PNG")
    print(f"Сохранено: {tray_path}")


if __name__ == "__main__":
    main()
