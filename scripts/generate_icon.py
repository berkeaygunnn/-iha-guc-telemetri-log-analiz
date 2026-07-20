"""Uygulamanın basit ikonunu üretir: koyu yüzey üzerinde bir batarya + yıldırım
motifi (proje bir güç/batarya analiz aracı olduğu için). Renkler frontend/main.py
içindeki tema paletiyle aynı (SURFACE, SERIES_COLORS[0], TEXT_PRIMARY).

Çıktı: frontend/assets/icon.png (pencere ikonu, tüm platformlarda) ve
frontend/assets/icon.ico (Windows .exe dosya ikonu).

Kullanım: python scripts/generate_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

SURFACE = "#1a1a19"
ACCENT = "#3987e5"
TEXT_PRIMARY = "#ffffff"

SIZE = 256
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "assets"


def draw_icon() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Koyu, yuvarlak köşeli arka plan.
    draw.rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=48, fill=SURFACE)

    # Batarya gövdesi (dikey dikdörtgen + üstte küçük kutup çıkıntısı).
    body = [72, 56, 184, 208]
    draw.rounded_rectangle(body, radius=16, outline=TEXT_PRIMARY, width=10)
    draw.rounded_rectangle([104, 32, 152, 60], radius=8, fill=TEXT_PRIMARY)

    # Bataryanın üstüne binen yıldırım (akım/güç) simgesi.
    bolt = [(150, 76), (110, 140), (136, 140), (106, 192), (162, 122), (134, 122)]
    draw.polygon(bolt, fill=ACCENT)

    return image


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    icon = draw_icon()

    png_path = OUTPUT_DIR / "icon.png"
    icon.save(png_path)

    ico_path = OUTPUT_DIR / "icon.ico"
    icon.save(ico_path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    print(f"Yazıldı: {png_path}")
    print(f"Yazıldı: {ico_path}")


if __name__ == "__main__":
    main()
