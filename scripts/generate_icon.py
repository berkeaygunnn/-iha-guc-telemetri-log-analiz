"""Uygulamanın ikonunu üretir: koyu, yuvarlak köşeli bir zemin üzerinde
mavi ışıklı bir quadcopter (dört pervane + gövde).

Neden kod, neden hazır bir görsel değil: ikon 16 pikselden 256 piksele kadar
her boyutta üretiliyor. Bir rasterden küçültmek küçük boyutlarda bulanıklaşır;
burada çizim her boyut için yeniden yapılıyor (aslında 4 kat büyük çizilip
küçültülüyor — PIL'in kendi çizimlerinde kenar yumuşatma yok, süperörnekleme
bunu telafi ediyor).

Renkler frontend/main.py'deki tema paletiyle akraba (koyu yüzey + mavi vurgu).

Çıktı: frontend/assets/icon.png (pencere ikonu, tüm platformlarda) ve
frontend/assets/icon.ico (Windows .exe dosya ikonu).

Kullanım: python scripts/generate_icon.py
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

BG_OUTER = (9, 18, 29)       # köşelere doğru koyulaşan zemin
BG_INNER = (28, 51, 74)      # merkezdeki aydınlık — kollar bunun üzerinde okunuyor
ARM = (11, 20, 30)           # gövdeden pervanelere giden kollar (zeminden koyu)
BODY_FILL = (13, 25, 39)
ACCENT = (63, 169, 245)      # ana mavi (pervane, halka, gövde konturu)
ACCENT_SOFT = (109, 197, 255)

SIZE = 256
SS = 4  # süperörnekleme katsayısı (kenar yumuşatma için)
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "assets"


def _radial_background(side: int) -> Image.Image:
    """Merkezi hafif aydınlık, köşeleri koyu bir zemin.

    Piksel piksel değil, küçük bir görüntü üretip büyütülerek yapılıyor:
    256*SS = 1024 piksellik bir kareyi tek tek dolaşmak gereksiz yavaş."""
    small = 64
    gradient = Image.new("RGB", (small, small))
    pixels = gradient.load()
    center = (small - 1) / 2
    max_distance = math.hypot(center, center)
    for y in range(small):
        for x in range(small):
            t = math.hypot(x - center, y - center) / max_distance
            t = min(1.0, t ** 1.4)  # merkezdeki aydınlık alanı biraz genişlet
            pixels[x, y] = tuple(
                round(inner + (outer - inner) * t)
                for inner, outer in zip(BG_INNER, BG_OUTER)
            )
    return gradient.resize((side, side), Image.BICUBIC)


def _draw_rotor(layer: ImageDraw.ImageDraw, glow: ImageDraw.ImageDraw,
                cx: float, cy: float, radius: float):
    """Bir pervane grubu: koruma halkası + çapraz iki bıçak + göbek.

    Dört ayrı bıçak yerine merkezden geçen İKİ uzun bıçak çiziliyor; görsel
    olarak aynı X'i veriyor ama bıçakların göbekte birleştiği yerde ek/çizgi
    izi oluşmuyor."""
    ring_width = max(2.0, radius * 0.055)
    # Parlama halkanın DIŞINA doğru olsun: içeriye taşınca halka bir daire
    # yerine dolu bir disk gibi görünüyordu.
    glow.ellipse(
        [cx - radius * 1.04, cy - radius * 1.04, cx + radius * 1.04, cy + radius * 1.04],
        outline=ACCENT + (90,), width=round(ring_width * 1.4),
    )
    layer.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        outline=ACCENT + (255,), width=round(ring_width),
    )

    # Bıçaklar halkadan belirgin şekilde kısa: referansta bıçak uçlarıyla
    # koruma halkası arasında boşluk var, dolduğunda pervane bir "yıldız"
    # yerine dolu bir diske dönüşüyor.
    blade_length = radius * 0.76
    blade_width = radius * 0.21
    for angle_deg in (45, 135):
        angle = math.radians(angle_deg)
        # Bıçak, kendi ekseninde bir elips: her noktası döndürülerek çokgene
        # çevriliyor (PIL doğrudan döndürülmüş elips çizemiyor).
        points = []
        for step in range(48):
            t = 2 * math.pi * step / 48
            ex, ey = blade_length * math.cos(t), blade_width * math.sin(t)
            points.append((
                cx + ex * math.cos(angle) - ey * math.sin(angle),
                cy + ex * math.sin(angle) + ey * math.cos(angle),
            ))
        layer.polygon(points, fill=ACCENT + (255,))

    hub = radius * 0.20
    layer.ellipse([cx - hub, cy - hub, cx + hub, cy + hub], fill=(8, 16, 26, 255))


def draw_icon(side: int) -> Image.Image:
    canvas = side * SS
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))

    # Zemin: yuvarlak köşeli maskeyle kırpılmış radyal degrade.
    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, canvas - 1, canvas - 1], radius=round(canvas * 0.22), fill=255
    )
    image.paste(_radial_background(canvas), (0, 0), mask)

    glow_layer = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    art_layer = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    glow = ImageDraw.Draw(glow_layer)
    art = ImageDraw.Draw(art_layer)

    center = canvas / 2
    # Pervaneler köşelere doğru itiliyor: daha yakınken gövdeyle aralarındaki
    # kollar tamamen örtülüyor ve ikon "dört daire + bir elips" gibi okunuyordu.
    rotor_offset = canvas * 0.272
    rotor_radius = canvas * 0.143
    arm_width = round(canvas * 0.075)

    # Kollar: gövdeden dört pervaneye. Pervanelerin ALTINDA kalsınlar diye
    # önce çiziliyor.
    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        art.line(
            [center, center, center + dx * rotor_offset, center + dy * rotor_offset],
            fill=ARM + (255,), width=arm_width,
        )

    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        _draw_rotor(art, glow, center + dx * rotor_offset,
                    center + dy * rotor_offset, rotor_radius)

    # Gövde: dikey bir elips, koyu dolgu + parlak mavi kontur. Kolların
    # birleştiği yeri kapatsın diye en son çiziliyor.
    body_w, body_h = canvas * 0.130, canvas * 0.245
    body = [center - body_w, center - body_h, center + body_w, center + body_h]
    art.ellipse(body, fill=BODY_FILL + (255,))
    art.ellipse(body, outline=ACCENT + (255,), width=round(canvas * 0.018))
    inset = canvas * 0.028
    art.ellipse(
        [body[0] + inset, body[1] + inset, body[2] - inset, body[3] - inset],
        outline=ACCENT_SOFT + (110,), width=max(1, round(canvas * 0.006)),
    )
    glow.ellipse(body, outline=ACCENT + (120,), width=round(canvas * 0.03))

    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(canvas * 0.018))
    image = Image.alpha_composite(image, glow_layer)
    image = Image.alpha_composite(image, art_layer)

    return image.resize((side, side), Image.LANCZOS)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    png_path = OUTPUT_DIR / "icon.png"
    draw_icon(SIZE).save(png_path)

    # Her boyut AYRI çiziliyor: tek bir 256'lıktan küçültmek 16/32 pikselde
    # pervaneleri lekeye çeviriyordu.
    ico_sizes = [16, 32, 48, 64, 128, 256]
    frames = [draw_icon(side) for side in ico_sizes]
    frames[-1].save(
        OUTPUT_DIR / "icon.ico",
        sizes=[(s, s) for s in ico_sizes],
        append_images=frames[:-1],
    )

    print(f"Yazıldı: {png_path}")
    print(f"Yazıldı: {OUTPUT_DIR / 'icon.ico'}")


if __name__ == "__main__":
    main()
