"""Backend'in ArduPilot .bin parser'ını test etmek için sentetik bir log üretir.

İnternetten indirilen gerçek loglar ESC telemetrisini her zaman anlamlı
(değişken) veriyle doldurmuyor (SITL testlerinde çoğu zaman sabit/placeholder
değerler var). Bu script, sadece FMT + BAT + ESC mesajlarından oluşan, akımın
bilerek zamanla değiştiği küçük bir .bin dosyası üretir; böylece parser'ı
gerçek/rastgele bir log dosyası bulmaya bağımlı olmadan, bilinen/beklenen
değerlere karşı test edebiliyoruz.

Kullanım: python make_synthetic_log.py <cikti.bin>
"""

import struct
import sys

HEAD1, HEAD2 = 0xA3, 0x95
FMT_MSG_ID = 128

BAT_TYPE = 100
ESC_TYPE = 101

# LogStructure.h'deki format karakteri -> byte boyutu tablosu (backend'deki
# fieldByteSize ile aynı); FMT mesajının "length" alanını doğru hesaplamak için.
FIELD_SIZES = {
    "a": 64, "b": 1, "B": 1, "h": 2, "H": 2, "c": 2, "C": 2,
    "i": 4, "I": 4, "f": 4, "e": 4, "E": 4, "L": 4, "n": 4,
    "N": 16, "Z": 64, "d": 8, "q": 8, "Q": 8,
}


def message_length(fmt: str) -> int:
    """Header (3 bayt) + format string'deki alanların toplam boyutu."""
    return 3 + sum(FIELD_SIZES[c] for c in fmt)


def build_fmt_message(msg_type: int, name: str, fmt: str, labels: str) -> bytes:
    # Bu, tanımlanan mesaj tipinin (ör. BAT) kendi uzunluğu; FMT mesajının
    # kendi uzunluğu (89 bayt) sabittir ve ayrıca saklanmaz.
    length = message_length(fmt)
    return (
        bytes([HEAD1, HEAD2, FMT_MSG_ID])
        + struct.pack("<BB", msg_type, length)
        + name.encode("ascii").ljust(4, b"\x00")
        + fmt.encode("ascii").ljust(16, b"\x00")
        + labels.encode("ascii").ljust(64, b"\x00")
    )


def build_bat_message(time_s: float, instance: int, volt: float, curr: float) -> bytes:
    time_us = int(time_s * 1e6)
    return bytes([HEAD1, HEAD2, BAT_TYPE]) + struct.pack("<QBff", time_us, instance, volt, curr)


def build_esc_message(time_s: float, instance: int, curr: float) -> bytes:
    time_us = int(time_s * 1e6)
    return bytes([HEAD1, HEAD2, ESC_TYPE]) + struct.pack("<QBf", time_us, instance, curr)


def build_esc_message_with_rpm(time_s: float, instance: int, curr: float, rpm: float) -> bytes:
    """RPM alanı eklenmiş ESC mesajı varyantı. FMT'nin de "QBff"/"TimeUS,
    Instance,Curr,RPM" olarak (bu fonksiyonla eşleşecek şekilde) ayrıca
    yazılması gerekir - generate()'in varsayılan (RPM'siz) FMT'sini
    bozmamak için bu, ayrı bir fonksiyon."""
    time_us = int(time_s * 1e6)
    return bytes([HEAD1, HEAD2, ESC_TYPE]) + struct.pack("<QBff", time_us, instance, curr, rpm)


def generate() -> bytes:
    out = bytearray()
    out += build_fmt_message(BAT_TYPE, "BAT", "QBff", "TimeUS,Inst,Volt,Curr")
    out += build_fmt_message(ESC_TYPE, "ESC", "QBf", "TimeUS,Instance,Curr")

    # 10 saniyelik, akımın yükselip alçaldığı basit bir uçuş profili.
    pack_currents = [5, 10, 15, 20, 25, 30, 25, 20, 15, 10]
    voltages = [16.8, 16.5, 16.2, 15.9, 15.6, 15.3, 15.6, 15.9, 16.2, 16.5]

    # Her motorun payı farklı bir katsayıyla (grafikte satırlar birbirinden
    # ayırt edilebilsin diye) hesaplanıyor; dördü de birbirinden farklı.
    motor_factors = [0.8, 0.95, 1.1, 1.25]

    for t, (volt, pack_curr) in enumerate(zip(voltages, pack_currents)):
        out += build_bat_message(float(t), 0, volt, float(pack_curr))
        # İkinci batarya (Inst=1): birden fazla batarya gruplamasını test etmek
        # için, birincinden bilerek farklı (daha küçük) değerlerle.
        out += build_bat_message(float(t), 1, volt - 1.0, float(pack_curr) * 0.6)
        for motor_id, factor in enumerate(motor_factors):
            motor_curr = pack_curr / len(motor_factors) * factor
            out += build_esc_message(float(t), motor_id, motor_curr)

    return bytes(out)


if __name__ == "__main__":
    output_path = sys.argv[1] if len(sys.argv) > 1 else "synthetic_test_log.BIN"
    with open(output_path, "wb") as f:
        f.write(generate())
    print(f"Sentetik log yazildi: {output_path}")
